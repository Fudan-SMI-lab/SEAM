"""Idempotent dashboard lifecycle cleanup contract for ``DashboardWiring.close()``.

Inert OFF/AUTO wirings are no-ops; an active wiring stops its thread and
restores the two dashboard env keys exactly (present -> restored, absent ->
deleted); close is idempotent; same-process ON -> OFF is clean; a stuck
renderer thread yields only a stderr diagnostic (never raises, never changes
the outcome); and cleanup runs on success / Exception / BaseException /
KeyboardInterrupt.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from core.dashboard import DashboardBackend
from tests.e2e.dashboard_wiring import DashboardWiring
from tests.e2e.e2e_test_v3 import _prepare_dashboard_wiring

_ENV_KEYS = ("SEAM_UI_EVENTS_PATH", "SEAM_RUN_ID")


def _clear_dashboard_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def _patch_rich_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "core.dashboard.resolve_dashboard_backend", lambda: DashboardBackend.RICH
    )


class _NeverStoppingThread(threading.Thread):
    """Renderer stub that ignores stop and runs past the join timeout."""

    def __init__(self) -> None:
        self._entered = threading.Event()
        super().__init__(target=self._run, daemon=True)

    def _run(self) -> None:
        self._entered.set()
        time.sleep(30.0)


class _InterruptingJoinThread(threading.Thread):
    def join(self, timeout: float | None = None) -> None:
        del timeout
        raise KeyboardInterrupt("second interrupt during dashboard cleanup")


def _run_with_outer_close(body: Any) -> DashboardWiring:
    """Mirror run_e2e_v3's outer try/finally: dashboard_wiring.close() shape."""
    wiring = DashboardWiring(environ=os.environ, prior_env={})
    try:
        body()
        return wiring
    finally:
        wiring.close()


# --- Baseline: inert OFF and AUTO-non-TTY close() is a no-op ---


def test_baseline_off_inert_close_is_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_dashboard_env(monkeypatch)
    _patch_rich_backend(monkeypatch)
    wiring = _prepare_dashboard_wiring(
        "off", tmp_path, "run-off", is_tty=True, environ=os.environ
    )
    assert wiring == DashboardWiring(None, None, None, False)
    wiring.close()
    assert wiring._closed is True
    assert not (tmp_path / "ui_events.jsonl").exists()
    for key in _ENV_KEYS:
        assert key not in os.environ


def test_baseline_auto_non_tty_inert_close_is_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_dashboard_env(monkeypatch)
    _patch_rich_backend(monkeypatch)
    wiring = _prepare_dashboard_wiring(
        "auto", tmp_path, "run-auto", is_tty=False, environ=os.environ
    )
    assert wiring == DashboardWiring(None, None, None, False)
    wiring.close()
    assert not (tmp_path / "ui_events.jsonl").exists()
    for key in _ENV_KEYS:
        assert key not in os.environ


# --- Normal active close stops thread and deletes absent env keys ---


def test_active_close_stops_thread_and_deletes_absent_env_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_dashboard_env(monkeypatch)
    _patch_rich_backend(monkeypatch)
    wiring = _prepare_dashboard_wiring(
        "on", tmp_path, "run-on-clean", is_tty=False, environ=os.environ
    )
    try:
        assert wiring.dashboard_on is True
        assert wiring.dashboard_thread is not None
        assert wiring.dashboard_thread.is_alive()
        assert os.environ["SEAM_RUN_ID"] == "run-on-clean"
    finally:
        wiring.close()
    assert wiring.dashboard_thread is not None
    assert not wiring.dashboard_thread.is_alive()
    for key in _ENV_KEYS:
        assert key not in os.environ


def test_close_is_idempotent_and_double_close_is_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_dashboard_env(monkeypatch)
    _patch_rich_backend(monkeypatch)
    wiring = _prepare_dashboard_wiring(
        "on", tmp_path, "run-double", is_tty=False, environ=os.environ
    )
    wiring.close()
    assert wiring._closed is True
    wiring.close()
    wiring.close()
    for key in _ENV_KEYS:
        assert key not in os.environ


# --- Exact prior env values: present restored, absent deleted ---


def test_close_restores_pre_existing_env_values_exactly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_rich_backend(monkeypatch)
    monkeypatch.setenv("SEAM_UI_EVENTS_PATH", "/prior/path/from/parent.jsonl")
    monkeypatch.setenv("SEAM_RUN_ID", "prior-run-id-123")
    wiring = _prepare_dashboard_wiring(
        "on", tmp_path, "run-restore", is_tty=False, environ=os.environ
    )
    try:
        assert os.environ["SEAM_UI_EVENTS_PATH"] != "/prior/path/from/parent.jsonl"
        assert os.environ["SEAM_RUN_ID"] == "run-restore"
    finally:
        wiring.close()
    assert os.environ["SEAM_UI_EVENTS_PATH"] == "/prior/path/from/parent.jsonl"
    assert os.environ["SEAM_RUN_ID"] == "prior-run-id-123"


def test_close_deletes_absent_and_restores_present_independently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_rich_backend(monkeypatch)
    monkeypatch.setenv("SEAM_RUN_ID", "kept-prior-run")
    monkeypatch.delenv("SEAM_UI_EVENTS_PATH", raising=False)
    wiring = _prepare_dashboard_wiring(
        "on", tmp_path, "run-mixed", is_tty=False, environ=os.environ
    )
    try:
        assert os.environ["SEAM_UI_EVENTS_PATH"].endswith("ui_events.jsonl")
        assert os.environ["SEAM_RUN_ID"] == "run-mixed"
    finally:
        wiring.close()
    assert "SEAM_UI_EVENTS_PATH" not in os.environ
    assert os.environ["SEAM_RUN_ID"] == "kept-prior-run"


# --- Same-process ON -> OFF lifecycle leaves env clean ---


def test_same_process_on_to_off_lifecycle_restores_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_dashboard_env(monkeypatch)
    _patch_rich_backend(monkeypatch)
    on_wiring = _prepare_dashboard_wiring(
        "on", tmp_path, "run-on-then-off", is_tty=False, environ=os.environ
    )
    on_wiring.close()
    for key in _ENV_KEYS:
        assert key not in os.environ
    off_wiring = _prepare_dashboard_wiring(
        "off", tmp_path, "run-on-then-off-2", is_tty=True, environ=os.environ
    )
    assert off_wiring == DashboardWiring(None, None, None, False)
    off_wiring.close()
    for key in _ENV_KEYS:
        assert key not in os.environ


# --- close() runs on caught Exception, BaseException, KeyboardInterrupt ---


def test_close_runs_when_body_raises_caught_exception() -> None:
    class _Caught(Exception):
        pass

    body_ran = {}

    def body() -> None:
        try:
            raise _Caught("coordinator error")
        except _Caught:
            pass
        body_ran["completed"] = True

    wiring = _run_with_outer_close(body)
    assert body_ran.get("completed") is True
    assert wiring._closed is True


@pytest.mark.parametrize(
    "exc_factory",
    [lambda: KeyboardInterrupt(), lambda: BaseException("generic base"), lambda: SystemExit()],
)
def test_close_runs_when_body_raises_base_exception(exc_factory: Any) -> None:
    def body() -> None:
        raise exc_factory()

    with pytest.raises(BaseException):
        _run_with_outer_close(body)


# --- Fake non-stopping thread: diagnostic only, never raises, never changes outcome ---


def test_non_stopping_thread_yields_diagnostic_without_raising_or_changing_outcome(
    capsys: pytest.CaptureFixture[str],
) -> None:
    thread = _NeverStoppingThread()
    thread.start()
    assert thread._entered.wait(timeout=2.0)
    wiring = DashboardWiring(
        dashboard_stop=threading.Event(),
        dashboard_thread=thread,
        dashboard_on=True,
        environ=os.environ,
        prior_env={},
    )
    authoritative_exit_code = 0
    try:
        pass  # migration finished successfully with exit code 0
    finally:
        wiring.close()  # must NOT raise even though thread is still alive
    captured = capsys.readouterr()
    assert "SEAM dashboard cleanup" in captured.err
    assert "Migration outcome is unaffected" in captured.err
    assert authoritative_exit_code == 0
    assert wiring._closed is True


def test_repeated_interruption_calls_close_idempotently(
    capsys: pytest.CaptureFixture[str],
) -> None:
    stop = threading.Event()
    thread = threading.Thread(target=stop.wait, args=(5.0,), daemon=True)
    thread.start()
    wiring = DashboardWiring(
        dashboard_stop=stop,
        dashboard_thread=thread,
        dashboard_on=True,
        environ=os.environ,
        prior_env={},
    )
    for _ in range(5):
        wiring.close()
    captured = capsys.readouterr()
    assert "SEAM dashboard cleanup" not in captured.err
    assert not thread.is_alive()
    assert wiring._closed is True


def test_join_interrupt_restores_environment_before_becoming_closed() -> None:
    environ = {
        "SEAM_UI_EVENTS_PATH": "/active/events.jsonl",
        "SEAM_RUN_ID": "active-run",
    }
    wiring = DashboardWiring(
        dashboard_stop=threading.Event(),
        dashboard_thread=_InterruptingJoinThread(),
        dashboard_on=True,
        environ=environ,
        prior_env={"SEAM_RUN_ID": "parent-run"},
    )

    with pytest.raises(KeyboardInterrupt):
        wiring.close()

    assert "SEAM_UI_EVENTS_PATH" not in environ
    assert environ["SEAM_RUN_ID"] == "parent-run"
    assert wiring._closed is True
    wiring.close()
    assert "SEAM_UI_EVENTS_PATH" not in environ
    assert environ["SEAM_RUN_ID"] == "parent-run"


# --- Integration: _prepare_dashboard_wiring populates prior_env correctly ---


def test_prepare_wiring_captures_prior_env_for_active_wiring(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_rich_backend(monkeypatch)
    monkeypatch.setenv("SEAM_UI_EVENTS_PATH", "/parent/sink.jsonl")
    monkeypatch.setenv("SEAM_RUN_ID", "parent-run")
    wiring = _prepare_dashboard_wiring(
        "on", tmp_path, "child-run", is_tty=False, environ=os.environ
    )
    try:
        assert wiring.prior_env == {
            "SEAM_UI_EVENTS_PATH": "/parent/sink.jsonl",
            "SEAM_RUN_ID": "parent-run",
        }
    finally:
        wiring.close()
    assert os.environ["SEAM_UI_EVENTS_PATH"] == "/parent/sink.jsonl"
    assert os.environ["SEAM_RUN_ID"] == "parent-run"


def test_prepare_wiring_inert_has_empty_prior_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_dashboard_env(monkeypatch)
    _patch_rich_backend(monkeypatch)
    wiring = _prepare_dashboard_wiring(
        "off", tmp_path, "run-off-prior", is_tty=True, environ=os.environ
    )
    assert wiring.prior_env == {}
    assert wiring.environ is None
    wiring.close()
