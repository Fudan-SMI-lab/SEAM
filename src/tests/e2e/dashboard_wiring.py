"""Typed lifecycle owner for the optional live dashboard.

``DashboardWiring`` is a mutable lifecycle object that owns the event sink,
the renderer stop event, the renderer thread, the target environment mapping,
and the exact prior values for the two dashboard env keys
(``SEAM_UI_EVENTS_PATH`` and ``SEAM_RUN_ID``).

``close()`` is idempotent: it sets stop, joins the renderer thread with a
bounded timeout, restores/deletes both env keys exactly, and reports a
stuck renderer thread as a stderr diagnostic without raising into the
migration authority. The migration result stands on its own; a stuck
cleanup thread must never rewrite the authoritative run outcome.
"""

from __future__ import annotations

import sys
import threading
from collections.abc import MutableMapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.ui_events import UIEventSink

_DASHBOARD_JOIN_TIMEOUT_SECONDS: float = 2.0

# The two env keys owned by the dashboard lifecycle. Anything else in the
# target environ is the migrator's responsibility and is never touched here.
_ENV_KEYS: tuple[str, ...] = ("SEAM_UI_EVENTS_PATH", "SEAM_RUN_ID")


@dataclass
class DashboardWiring:
    """Mutable lifecycle owner for the optional live dashboard.

    A fully-inert wiring (``dashboard_on=False``) holds ``None`` for the
    sink/stop/thread/environ and an empty ``prior_env``; ``close()`` is a
    true no-op for inert wirings and is safe to call any number of times.

    ``prior_env`` records only env keys that were *present* before the
    dashboard activated, mapped to their original values. Keys absent from
    ``prior_env`` are deleted on close; keys present are restored exactly.
    """

    ui_event_sink: UIEventSink | None = None
    dashboard_stop: threading.Event | None = None
    dashboard_thread: threading.Thread | None = None
    dashboard_on: bool = False
    environ: MutableMapping[str, str] | None = None
    prior_env: dict[str, str] = field(default_factory=dict)
    _closed: bool = False

    def close(self) -> None:
        """Idempotently stop the renderer thread and restore env keys.

        - Sets the stop event and joins the renderer thread with a bounded
          timeout (matches the prior manual cleanup budget).
        - If the thread is still alive after the timeout, logs a diagnostic
          to stderr but does NOT raise: a stuck cleanup thread must never
          rewrite the authoritative run outcome.
        - Restores the exact prior values for ``SEAM_UI_EVENTS_PATH`` and
          ``SEAM_RUN_ID``: deletes them if they were absent, restores the
          original value if they were present.
        - Safe to call repeatedly; the second and later calls do nothing.
        """
        if self._closed:
            return
        self._closed = True
        try:
            if self.dashboard_stop is not None:
                self.dashboard_stop.set()
            thread = self.dashboard_thread
            if thread is not None:
                thread.join(timeout=_DASHBOARD_JOIN_TIMEOUT_SECONDS)
                if thread.is_alive():
                    message = (
                        "SEAM dashboard cleanup: renderer thread did not exit "
                        f"within {_DASHBOARD_JOIN_TIMEOUT_SECONDS:.1f}s; leaving "
                        "it as a daemon. Migration outcome is unaffected."
                    )
                    print(message, file=sys.stderr)
        finally:
            environ = self.environ
            if environ is not None:
                for key in _ENV_KEYS:
                    if key in self.prior_env:
                        environ[key] = self.prior_env[key]
                    else:
                        _ = environ.pop(key, None)
