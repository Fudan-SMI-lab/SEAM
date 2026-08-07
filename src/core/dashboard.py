"""Public facade for the SEAM live dashboard.

This module is a thin compatibility surface: it selects a renderer backend
(textual preferred, rich fallback, none), exposes the
:class:`SeamDashboardApp` runner that dispatches to the selected backend, and
re-exports the symbols tests and callers import from ``core.dashboard``.

The implementations live in focused, single-responsibility modules:

* :mod:`core.dashboard_state` -- the event state machine (``DashboardState``,
  ``PhaseRow``, ``_apply_event``, ``visible_phase_rows``, ``_load_events``);
* :mod:`core.dashboard_rich` -- the Rich render loop (``run_dashboard``);
* :mod:`core.dashboard_textual`` -- the Textual app factory
  (``_build_textual_dashboard``).

Re-exporting keeps every historical import path working unchanged:

* ``from core.dashboard import DashboardState, _apply_event, visible_phase_rows``
* ``from core.dashboard import _load_events``
* ``from core.dashboard import run_dashboard``
* ``from core.dashboard import _build_textual_dashboard``
* ``from core.dashboard import (DashboardBackend,
   DashboardBackendUnavailableError, SeamDashboardApp,
   resolve_dashboard_backend, DASHBOARD_INSTALL_COMMAND)``

Both ``run_dashboard`` and ``resolve_dashboard_backend`` remain module-level
attributes of ``core.dashboard``, so ``monkeypatch.setattr(
"core.dashboard.run_dashboard", ...)`` and the ``resolve_dashboard_backend``
patch still target this facade. Neither ``rich`` nor ``textual`` is imported
at module top level; they stay lazy inside the renderer entry points.
"""

from __future__ import annotations

import importlib
import importlib.util
import threading
from enum import Enum
from pathlib import Path
from core.compat import assert_never

from core.dashboard_rich import run_dashboard
from core.dashboard_state import (
    DashboardState,
    PhaseRow,
    _apply_event,
    _load_events,
    visible_phase_rows,
)
from core.dashboard_textual import _build_textual_dashboard

__all__ = [
    "DASHBOARD_INSTALL_COMMAND",
    "DashboardBackend",
    "DashboardBackendUnavailableError",
    "DashboardState",
    "PhaseRow",
    "SeamDashboardApp",
    "resolve_dashboard_backend",
    "run_dashboard",
    "visible_phase_rows",
    "_apply_event",
    "_build_textual_dashboard",
    "_load_events",
]


class DashboardBackend(Enum):
    """Renderer backend selected by the dependency probe."""

    TEXTUAL = "textual"
    RICH = "rich"
    NONE = "none"


DASHBOARD_INSTALL_COMMAND = 'python -m pip install -e "./src[dashboard]"'


class DashboardBackendUnavailableError(RuntimeError):
    """Raised when explicit ``on`` mode has no installed renderer backend."""

    def __init__(self) -> None:
        super().__init__(
            "Dashboard mode 'on' requires an installed renderer backend "
            "(prefer textual, fall back to rich) but neither was importable. "
            f"Install the optional dashboard extra: {DASHBOARD_INSTALL_COMMAND}"
        )
        self.install_command = DASHBOARD_INSTALL_COMMAND


def resolve_dashboard_backend() -> DashboardBackend:
    """Probe renderer availability via ``importlib.util.find_spec``.

    Discovers whether ``textual`` or ``rich`` is installed without importing
    them, so a backend that is installed but fails at runtime is still
    selected: its renderer exceptions are then allowed to propagate rather
    than being mislabeled as a missing dependency. Textual is preferred;
    Rich is the fallback; otherwise ``NONE``.
    """
    if importlib.util.find_spec("textual") is not None:
        return DashboardBackend.TEXTUAL
    if importlib.util.find_spec("rich") is not None:
        return DashboardBackend.RICH
    return DashboardBackend.NONE


def _activate_dashboard_backend(backend: DashboardBackend) -> None:
    if backend is DashboardBackend.TEXTUAL:
        requirements = (
            ("rich.text", "Text"),
            ("textual.app", "App"),
            ("textual.app", "ComposeResult"),
            ("textual.containers", "Container"),
            ("textual.widgets", "Footer"),
            ("textual.widgets", "Header"),
            ("textual.widgets", "Static"),
        )
    elif backend is DashboardBackend.RICH:
        requirements = (
            ("rich.console", "Group"),
            ("rich.live", "Live"),
            ("rich.panel", "Panel"),
            ("rich.table", "Table"),
            ("rich.text", "Text"),
        )
    elif backend is DashboardBackend.NONE:
        raise DashboardBackendUnavailableError()
    else:
        assert_never(backend)
    for module_name, attribute_name in requirements:
        module = importlib.import_module(module_name)
        _ = getattr(module, attribute_name)


class SeamDashboardApp:
    """Small wrapper used by the runner to launch the live dashboard."""

    def __init__(
        self,
        events_path: str | Path,
        stop_event: threading.Event,
        *,
        backend: DashboardBackend,
    ) -> None:
        self.events_path = Path(events_path)
        self.stop_event = stop_event
        self.backend = backend

    def run(self) -> None:
        if self.backend is DashboardBackend.TEXTUAL:
            self._run_textual()
            return
        # ``run_dashboard`` is resolved through this module's globals so the
        # historical ``monkeypatch.setattr("core.dashboard.run_dashboard", ...)``
        # contract still patches the rich backend path.
        run_dashboard(self.events_path, self.stop_event)

    def _run_textual(self) -> None:
        _build_textual_dashboard(self.events_path, self.stop_event).run()
