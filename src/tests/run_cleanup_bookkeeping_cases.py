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


class OrdinaryBookkeepingError(Exception):
    pass


def _type_error() -> Exception:
    return TypeError("bookkeeping type failure")


def _key_error() -> Exception:
    return KeyError("bookkeeping key failure")


def _ordinary_error() -> Exception:
    return OrdinaryBookkeepingError("bookkeeping ordinary failure")


@pytest.mark.parametrize(
    "failure_factory",
    [_type_error, _key_error, _ordinary_error],
    ids=["type-error", "key-error", "custom-exception"],
)
@pytest.mark.parametrize("failed_operation", ["requested", "sessions", "recorded"])
def test_cleanup_bookkeeping_ordinary_failure_preserves_later_operations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failed_operation: str,
    failure_factory: ExceptionFactory,
) -> None:
    # Given one observer operation fails ordinarily and later resources remain.
    calls: list[str] = []
    owned_temp = tmp_path / "owned-temp"
    owned_temp.mkdir()
    process = subprocess.Popen([sys.executable, "-c", "pass"])

    def requested() -> None:
        calls.append("requested")
        if failed_operation == "requested":
            raise failure_factory()

    def sessions() -> int:
        calls.append("sessions")
        if failed_operation == "sessions":
            raise failure_factory()
        return 2

    def recorded(_count: int) -> None:
        calls.append("recorded")
        if failed_operation == "recorded":
            raise failure_factory()

    def stop_process(_process: subprocess.Popen[bytes]) -> None:
        calls.append("server")

    def remove_temp(path: Path, identity: DirectoryLockIdentity) -> None:
        calls.append("temp")
        release_owned_directory(path, identity)

    monkeypatch.setattr("harness.run.cleanup.stop_server", stop_process)
    monkeypatch.setattr("harness.run.cleanup.release_owned_directory", remove_temp)
    observer = ObserverSidecar(
        lambda: {}, lambda: RunCounts(0, 0), requested, sessions, recorded
    )
    cleanup = ResourceCleanup(
        CleanupContext(owned_temp, False, True, observer, process)
    )

    # When authorized cleanup attempts every independent operation.
    try:
        with pytest.raises(FinalizationHookError) as failure:
            _ = cleanup(TerminalOutcome.PASSED)
    finally:
        _ = process.wait(timeout=5)

    # Then ordinary bookkeeping failure is diagnosed without skipping later work.
    expected = ["requested", "sessions"]
    if failed_operation != "sessions":
        expected.append("recorded")
    expected.extend(("server", "temp"))
    assert calls == expected
    assert type(failure_factory()).__name__ in str(failure.value)
    assert not owned_temp.exists()


@pytest.mark.parametrize("signal_type", [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("failed_operation", ["requested", "recorded"])
def test_cleanup_bookkeeping_control_flow_propagates_immediately(
    tmp_path: Path,
    failed_operation: str,
    signal_type: type[BaseException],
) -> None:
    # Given requested or recorded bookkeeping raises process control flow.
    calls: list[str] = []
    owned_temp = tmp_path / "owned-temp"
    owned_temp.mkdir()

    def requested() -> None:
        calls.append("requested")
        if failed_operation == "requested":
            raise signal_type()

    def sessions() -> int:
        calls.append("sessions")
        return 1

    def recorded(_count: int) -> None:
        calls.append("recorded")
        if failed_operation == "recorded":
            raise signal_type()

    observer = ObserverSidecar(
        lambda: {}, lambda: RunCounts(0, 0), requested, sessions, recorded
    )
    cleanup = ResourceCleanup(CleanupContext(owned_temp, False, True, observer, None))

    # When cleanup reaches the control-flow operation, the signal escapes.
    with pytest.raises(signal_type):
        _ = cleanup(TerminalOutcome.PASSED)

    # Then no operation after the signal is attempted.
    expected = ["requested"]
    if failed_operation == "recorded":
        expected.extend(("sessions", "recorded"))
    assert calls == expected
    assert owned_temp.is_dir()


def test_cleanup_successful_operation_order_is_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given every cleanup and bookkeeping operation succeeds.
    calls: list[str] = []
    owned_temp = tmp_path / "owned-temp"
    owned_temp.mkdir()
    process = subprocess.Popen([sys.executable, "-c", "pass"])

    def remove_temp(path: Path, identity: DirectoryLockIdentity) -> None:
        calls.append("temp")
        release_owned_directory(path, identity)

    def stop_process(_process: subprocess.Popen[bytes]) -> None:
        calls.append("server")

    def requested() -> None:
        calls.append("requested")

    def sessions() -> int:
        calls.append("sessions")
        return 1

    def recorded(_count: int) -> None:
        calls.append("recorded")

    monkeypatch.setattr("harness.run.cleanup.stop_server", stop_process)
    monkeypatch.setattr("harness.run.cleanup.release_owned_directory", remove_temp)
    observer = ObserverSidecar(
        lambda: {},
        lambda: RunCounts(0, 0),
        requested,
        sessions,
        recorded,
    )
    cleanup = ResourceCleanup(
        CleanupContext(owned_temp, False, True, observer, process)
    )

    # When authorized cleanup runs.
    try:
        result = cleanup(TerminalOutcome.PASSED)
    finally:
        _ = process.wait(timeout=5)

    # Then the established successful order remains unchanged.
    assert calls == ["requested", "sessions", "recorded", "server", "temp"]
    assert result.telemetry_paths == ()
    assert not owned_temp.exists()
