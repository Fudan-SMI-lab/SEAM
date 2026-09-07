from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import subprocess
import sys

import pytest

from core.owned_directory_lock import DirectoryLockIdentity, release_owned_directory
from core.run_outcome import TerminalOutcome
from harness.run import (
    CleanupContext,
    FinalizationHookError,
    ObserverSidecar,
    ResourceCleanup,
    RunCounts,
)

ExceptionFactory = Callable[[], Exception]


class OrdinaryCleanupError(Exception):
    pass


def _type_error() -> Exception:
    return TypeError("session type failure")


def _key_error() -> Exception:
    return KeyError("session key failure")


def _ordinary_error() -> Exception:
    return OrdinaryCleanupError("session ordinary failure")


@pytest.mark.parametrize(
    "failure_factory",
    [_type_error, _key_error, _ordinary_error],
    ids=["type-error", "key-error", "custom-exception"],
)
def test_resource_cleanup_continues_after_ordinary_session_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_factory: ExceptionFactory,
) -> None:
    # Given session cleanup fails ordinarily while server and owned temp cleanup remain.
    calls: list[str] = []
    owned_temp = tmp_path / "owned-temp"
    owned_temp.mkdir()
    process = subprocess.Popen([sys.executable, "-c", "pass"])

    def fail_sessions() -> int:
        calls.append("sessions")
        raise failure_factory()

    def stop_process(_process: subprocess.Popen[bytes]) -> None:
        calls.append("server")

    def remove_temp(path: Path, identity: DirectoryLockIdentity) -> None:
        calls.append("temp")
        release_owned_directory(path, identity)

    monkeypatch.setattr("harness.run.cleanup.stop_server", stop_process)
    monkeypatch.setattr("harness.run.cleanup.release_owned_directory", remove_temp)
    cleanup = ResourceCleanup(
        CleanupContext(
            temp_dir=owned_temp,
            keep_temp_dir=False,
            owns_temp_dir=True,
            observer=ObserverSidecar(
                save_metrics=lambda: {},
                counts=lambda: RunCounts(0, 0),
                record_cleanup_requested=lambda: calls.append("requested"),
                cleanup_sessions=fail_sessions,
                record_cleaned_sessions=lambda _count: calls.append("recorded"),
            ),
            server_process=process,
        )
    )

    # When the authorized cleanup hook attempts every resource.
    try:
        with pytest.raises(FinalizationHookError) as failure:
            _ = cleanup(TerminalOutcome.PASSED)
    finally:
        _ = process.wait(timeout=5)

    # Then the ordinary failure is typed and later resources still run in order.
    assert calls == ["requested", "sessions", "server", "temp"]
    assert type(failure_factory()).__name__ in str(failure.value)
    assert not owned_temp.exists()


@pytest.mark.parametrize("signal_type", [KeyboardInterrupt, SystemExit])
def test_resource_cleanup_propagates_control_flow_from_session(
    tmp_path: Path,
    signal_type: type[BaseException],
) -> None:
    # Given session cleanup raises a process control-flow signal.
    calls: list[str] = []
    owned_temp = tmp_path / "owned-temp"
    owned_temp.mkdir()

    def interrupt_sessions() -> int:
        calls.append("sessions")
        raise signal_type()

    cleanup = ResourceCleanup(
        CleanupContext(
            temp_dir=owned_temp,
            keep_temp_dir=False,
            owns_temp_dir=True,
            observer=ObserverSidecar(
                save_metrics=lambda: {},
                counts=lambda: RunCounts(0, 0),
                record_cleanup_requested=lambda: calls.append("requested"),
                cleanup_sessions=interrupt_sessions,
                record_cleaned_sessions=lambda _count: calls.append("recorded"),
            ),
            server_process=None,
        )
    )

    # When cleanup reaches the interrupted resource, the control signal escapes.
    with pytest.raises(signal_type):
        _ = cleanup(TerminalOutcome.PASSED)

    # Then no later bookkeeping or owned-temp cleanup is attempted.
    assert calls == ["requested", "sessions"]
    assert owned_temp.is_dir()


@pytest.mark.parametrize(
    "failure_factory",
    [_type_error, _key_error, _ordinary_error],
    ids=["type-error", "key-error", "custom-exception"],
)
def test_resource_cleanup_continues_after_ordinary_server_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_factory: ExceptionFactory,
) -> None:
    # Given server cleanup fails ordinarily while owned-temp cleanup remains.
    calls: list[str] = []
    owned_temp = tmp_path / "owned-temp"
    owned_temp.mkdir()
    process = subprocess.Popen([sys.executable, "-c", "pass"])

    def fail_server(_process: subprocess.Popen[bytes]) -> None:
        calls.append("server")
        raise failure_factory()

    def remove_temp(path: Path, identity: DirectoryLockIdentity) -> None:
        calls.append("temp")
        release_owned_directory(path, identity)

    monkeypatch.setattr("harness.run.cleanup.stop_server", fail_server)
    monkeypatch.setattr("harness.run.cleanup.release_owned_directory", remove_temp)
    cleanup = ResourceCleanup(CleanupContext(owned_temp, False, True, None, process))

    # When cleanup attempts the server resource.
    try:
        with pytest.raises(FinalizationHookError) as failure:
            _ = cleanup(TerminalOutcome.PASSED)
    finally:
        _ = process.wait(timeout=5)

    # Then the typed failure is retained and owned-temp cleanup still runs.
    assert calls == ["server", "temp"]
    assert type(failure_factory()).__name__ in str(failure.value)
    assert not owned_temp.exists()


@pytest.mark.parametrize(
    "failure_factory",
    [_type_error, _key_error, _ordinary_error],
    ids=["type-error", "key-error", "custom-exception"],
)
def test_resource_cleanup_records_ordinary_temp_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_factory: ExceptionFactory,
) -> None:
    # Given the final owned-temp resource raises an ordinary exception.
    owned_temp = tmp_path / "owned-temp"
    owned_temp.mkdir()

    def fail_temp(_path: Path, _identity: DirectoryLockIdentity) -> None:
        raise failure_factory()

    monkeypatch.setattr("harness.run.cleanup.release_owned_directory", fail_temp)
    cleanup = ResourceCleanup(CleanupContext(owned_temp, False, True, None, None))

    # When cleanup attempts the final resource.
    with pytest.raises(FinalizationHookError) as failure:
        _ = cleanup(TerminalOutcome.PASSED)

    # Then the ordinary failure is represented without changing control flow.
    assert type(failure_factory()).__name__ in str(failure.value)
    assert owned_temp.is_dir()


@pytest.mark.parametrize("signal_type", [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("resource", ["server", "temp"])
def test_resource_cleanup_propagates_control_flow_from_later_resources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    resource: str,
    signal_type: type[BaseException],
) -> None:
    # Given a later resource raises a process control-flow signal.
    owned_temp = tmp_path / "owned-temp"
    owned_temp.mkdir()
    process = subprocess.Popen([sys.executable, "-c", "pass"])

    def interrupt_server(_process: subprocess.Popen[bytes]) -> None:
        raise signal_type()

    def interrupt_temp(_path: Path, _identity: DirectoryLockIdentity) -> None:
        raise signal_type()

    if resource == "server":
        monkeypatch.setattr("harness.run.cleanup.stop_server", interrupt_server)
    else:
        monkeypatch.setattr(
            "harness.run.cleanup.release_owned_directory", interrupt_temp
        )
    cleanup = ResourceCleanup(CleanupContext(owned_temp, False, True, None, process))

    # When cleanup reaches that resource, the control signal escapes.
    try:
        with pytest.raises(signal_type):
            _ = cleanup(TerminalOutcome.PASSED)
    finally:
        _ = process.wait(timeout=5)

    # Then the owned directory remains when control flow interrupts cleanup.
    assert owned_temp.is_dir()
