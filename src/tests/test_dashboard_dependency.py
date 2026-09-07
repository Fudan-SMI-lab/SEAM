"""Dependency-activation contract for the optional TUI dashboard.

Covers the typed backend probe (textual preferred, rich fallback, none), the
explicit-ON missing-dependency error, and the integration in
``_prepare_dashboard_wiring``: explicit ``on`` with no renderer must raise a
typed actionable error before any side effect, while ``auto`` with no renderer
and ``off`` must remain fully inert. The selected backend must actually be
used; runtime renderer exceptions are never mislabeled as a missing
dependency.
"""

from __future__ import annotations

import importlib.util
import os
import threading
from typing import Any

import pytest

from core.dashboard import (
    DASHBOARD_INSTALL_COMMAND,
    DashboardBackend,
    DashboardBackendUnavailableError,
    SeamDashboardApp,
    resolve_dashboard_backend,
    run_dashboard,
)
from tests.e2e.e2e_test_v3 import DashboardWiring, _prepare_dashboard_wiring

_SeamDashboardApp = SeamDashboardApp
_run_dashboard = run_dashboard


def _patch_backend(monkeypatch: pytest.MonkeyPatch, backend: DashboardBackend) -> None:
    """Force the integration seam to resolve to ``backend`` regardless of env."""
    monkeypatch.setattr("core.dashboard.resolve_dashboard_backend", lambda: backend)


def _patch_find_spec(
    monkeypatch: pytest.MonkeyPatch, *, textual: Any, rich: Any
) -> None:
    real_find_spec = importlib.util.find_spec

    def fake_find_spec(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "textual":
            return textual
        if name == "rich":
            return rich
        return real_find_spec(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)


def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SEAM_UI_EVENTS_PATH", raising=False)
    monkeypatch.delenv("SEAM_RUN_ID", raising=False)
    monkeypatch.delenv("CI", raising=False)


def _release(wiring: DashboardWiring) -> None:
    if wiring.dashboard_stop is not None:
        wiring.dashboard_stop.set()
    if wiring.dashboard_thread is not None:
        wiring.dashboard_thread.join(timeout=2)
    os.environ.pop("SEAM_UI_EVENTS_PATH", None)
    os.environ.pop("SEAM_RUN_ID", None)


# --- Backend probe contract ---


def test_resolve_dashboard_backend_prefers_rich(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_find_spec(monkeypatch, textual=object(), rich=object())
    assert resolve_dashboard_backend() is DashboardBackend.RICH


def test_resolve_dashboard_backend_falls_back_to_textual(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_find_spec(monkeypatch, textual=object(), rich=None)
    assert resolve_dashboard_backend() is DashboardBackend.TEXTUAL


def test_resolve_dashboard_backend_returns_none_when_neither(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_find_spec(monkeypatch, textual=None, rich=None)
    assert resolve_dashboard_backend() is DashboardBackend.NONE


def test_install_command_is_exact_extra_pin() -> None:
    assert DASHBOARD_INSTALL_COMMAND == 'python -m pip install -e "./src[dashboard]"'


def test_unavailable_error_message_contains_install_command() -> None:
    assert DASHBOARD_INSTALL_COMMAND in str(DashboardBackendUnavailableError())


# --- Baseline: OFF and AUTO-nonTTY stay inert regardless of backend state ---


def test_baseline_off_stays_inert_even_with_no_backend(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clean_env(monkeypatch)
    _patch_backend(monkeypatch, DashboardBackend.NONE)
    wiring = _prepare_dashboard_wiring(
        "off", tmp_path, "run-off", is_tty=True, environ=os.environ
    )
    assert wiring == DashboardWiring(None, None, None, False)
    assert not any(tmp_path.iterdir())
    assert "SEAM_UI_EVENTS_PATH" not in os.environ
    assert "SEAM_RUN_ID" not in os.environ


def test_baseline_auto_non_tty_stays_inert_even_with_backend_present(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clean_env(monkeypatch)
    _patch_backend(monkeypatch, DashboardBackend.TEXTUAL)
    wiring = _prepare_dashboard_wiring(
        "auto", tmp_path, "run-auto", is_tty=False, environ=os.environ
    )
    assert wiring == DashboardWiring(None, None, None, False)
    assert not any(tmp_path.iterdir())
    assert "SEAM_UI_EVENTS_PATH" not in os.environ
    assert "SEAM_RUN_ID" not in os.environ


def test_auto_mode_with_no_backend_is_inert_like_off(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clean_env(monkeypatch)
    _patch_backend(monkeypatch, DashboardBackend.NONE)
    wiring = _prepare_dashboard_wiring(
        "auto", tmp_path, "run-auto-none", is_tty=True, environ=os.environ
    )
    assert wiring == DashboardWiring(None, None, None, False)
    assert not any(tmp_path.iterdir())
    assert "SEAM_UI_EVENTS_PATH" not in os.environ
    assert "SEAM_RUN_ID" not in os.environ


# --- Explicit ON with no backend: typed actionable error before side effects ---


def test_on_mode_with_no_backend_raises_typed_error_before_side_effects(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clean_env(monkeypatch)
    _patch_backend(monkeypatch, DashboardBackend.NONE)
    threads_before = threading.active_count()
    with pytest.raises(DashboardBackendUnavailableError) as info:
        _prepare_dashboard_wiring(
            "on", tmp_path, "run-on-none", is_tty=False, environ=os.environ
        )
    assert DASHBOARD_INSTALL_COMMAND in str(info.value)
    assert threading.active_count() == threads_before
    assert not any(tmp_path.iterdir())
    assert "SEAM_UI_EVENTS_PATH" not in os.environ
    assert "SEAM_RUN_ID" not in os.environ


def test_on_mode_with_broken_renderer_raises_before_side_effects(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clean_env(monkeypatch)
    _patch_backend(monkeypatch, DashboardBackend.RICH)
    wiring: DashboardWiring | None = None
    threads_before = threading.active_count()
    real_import_module = importlib.import_module

    class _BrokenRendererImport(RuntimeError):
        pass

    def import_module(name: str, package: str | None = None) -> Any:
        if name.startswith("rich"):
            raise _BrokenRendererImport("simulated broken Rich installation")
        return real_import_module(name, package)

    monkeypatch.setattr(importlib, "import_module", import_module)
    monkeypatch.setattr("core.dashboard.run_dashboard", lambda _path, _stop: None)

    try:
        with pytest.raises(_BrokenRendererImport):
            wiring = _prepare_dashboard_wiring(
                "on", tmp_path, "run-on-broken", is_tty=False, environ=os.environ
            )
    finally:
        if wiring is not None:
            wiring.close()

    assert threading.active_count() == threads_before
    assert not (tmp_path / "ui_events.jsonl").exists()
    assert "SEAM_UI_EVENTS_PATH" not in os.environ
    assert "SEAM_RUN_ID" not in os.environ


@pytest.mark.parametrize("mode", ["on", "ON", "On", " on "])
def test_explicit_on_normalized_variants_raise_typed_error(
    mode: str, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clean_env(monkeypatch)
    _patch_backend(monkeypatch, DashboardBackend.NONE)
    with pytest.raises(DashboardBackendUnavailableError):
        _prepare_dashboard_wiring(
            mode, tmp_path, "run-norm", is_tty=False, environ=os.environ
        )
    assert "SEAM_UI_EVENTS_PATH" not in os.environ
    assert not (tmp_path / "ui_events.jsonl").exists()


def test_malformed_mode_with_no_backend_is_inert_like_auto(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clean_env(monkeypatch)
    _patch_backend(monkeypatch, DashboardBackend.NONE)
    wiring = _prepare_dashboard_wiring(
        "garbage", tmp_path, "run-bad", is_tty=True, environ=os.environ
    )
    assert wiring == DashboardWiring(None, None, None, False)
    assert "SEAM_UI_EVENTS_PATH" not in os.environ


# --- Selected backend is actually used ---


def test_on_mode_with_textual_starts_textual_backend_only(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clean_env(monkeypatch)
    _patch_backend(monkeypatch, DashboardBackend.TEXTUAL)
    monkeypatch.setattr("core.dashboard._activate_dashboard_backend", lambda _backend: None)
    textual_calls: list[Any] = []
    rich_calls: list[Any] = []
    monkeypatch.setattr(
        _SeamDashboardApp, "_run_textual", lambda self: textual_calls.append(self.events_path)
    )
    monkeypatch.setattr(
        "core.dashboard.run_dashboard", lambda path, stop: rich_calls.append(path)
    )
    wiring = _prepare_dashboard_wiring(
        "on", tmp_path, "run-textual", is_tty=False, environ=os.environ
    )
    try:
        assert wiring.dashboard_on is True
        assert wiring.dashboard_thread is not None
        wiring.dashboard_thread.join(timeout=2)
        assert textual_calls and not rich_calls
        assert os.environ["SEAM_RUN_ID"] == "run-textual"
    finally:
        _release(wiring)


def test_on_mode_with_rich_only_starts_rich_backend_only(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clean_env(monkeypatch)
    _patch_backend(monkeypatch, DashboardBackend.RICH)
    textual_calls: list[Any] = []
    rich_calls: list[Any] = []
    monkeypatch.setattr(
        _SeamDashboardApp, "_run_textual", lambda self: textual_calls.append(self.events_path)
    )
    monkeypatch.setattr(
        "core.dashboard.run_dashboard", lambda path, stop: rich_calls.append(path)
    )
    wiring = _prepare_dashboard_wiring(
        "on", tmp_path, "run-rich", is_tty=False, environ=os.environ
    )
    try:
        assert wiring.dashboard_on is True
        assert wiring.dashboard_thread is not None
        wiring.dashboard_thread.join(timeout=2)
        assert rich_calls and not textual_calls
        assert os.environ["SEAM_RUN_ID"] == "run-rich"
    finally:
        _release(wiring)


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_selected_backend_does_not_swallow_runtime_renderer_error(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clean_env(monkeypatch)
    _patch_backend(monkeypatch, DashboardBackend.TEXTUAL)
    monkeypatch.setattr("core.dashboard._activate_dashboard_backend", lambda _backend: None)

    class _RendererBug(RuntimeError):
        pass

    def boom_textual(self: Any) -> None:
        raise _RendererBug("real textual crash, not a missing dep")

    rich_calls: list[Any] = []
    monkeypatch.setattr(_SeamDashboardApp, "_run_textual", boom_textual)
    monkeypatch.setattr(
        "core.dashboard.run_dashboard", lambda path, stop: rich_calls.append(path)
    )
    wiring = _prepare_dashboard_wiring(
        "on", tmp_path, "run-bug", is_tty=False, environ=os.environ
    )
    try:
        assert wiring.dashboard_thread is not None
        wiring.dashboard_thread.join(timeout=2)
        # Runtime renderer error propagates and kills the thread; it is NOT
        # swallowed into a silent rich fallback nor mislabeled as a missing
        # dependency (which would require catching and converting it).
        assert not wiring.dashboard_thread.is_alive()
        assert rich_calls == []
    finally:
        _release(wiring)
