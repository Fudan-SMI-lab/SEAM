from __future__ import annotations

import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Protocol

from harness.session.opencode_contract import JsonValue
from harness.session.trace_seeds import SessionLifecycle


@dataclass(frozen=True, slots=True)
class SessionMetric:
    session_id: str
    role: str
    lifecycle: SessionLifecycle
    created_at: str
    command_count: int = 0
    phases: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CommandMetric:
    sequence: int
    phase_id: str | None
    session_id: str
    timeout_seconds: int
    started_at: str
    duration_seconds: float
    status: str
    command_length: int
    response_length: int
    error: str | None = None


@dataclass(frozen=True, slots=True)
class PhaseMetric:
    phase_id: str
    started_at: str
    ended_at: str | None = None
    duration_seconds: float = 0.0
    status: str = "running"
    error: str | None = None
    session_ids: tuple[str, ...] = ()


class PhaseEventSink(Protocol):
    def __call__(self, event_type: str, **details: JsonValue) -> None: ...


class PhaseTracker:
    _active_phase: str | None
    _metrics: dict[str, PhaseMetric]
    _record_event: PhaseEventSink

    def __init__(self, record_event: PhaseEventSink) -> None:
        self._active_phase = None
        self._metrics = {}
        self._record_event = record_event

    @property
    def active_phase(self) -> str | None:
        return self._active_phase

    @property
    def metrics(self) -> tuple[PhaseMetric, ...]:
        return tuple(self._metrics.values())

    def set_active(self, phase_id: str | None) -> None:
        self._active_phase = phase_id

    @contextmanager
    def timing(self, phase_id: str) -> Iterator[None]:
        started_monotonic = time.monotonic()
        metric = PhaseMetric(phase_id=phase_id, started_at=utc_now())
        self._metrics[phase_id] = metric
        previous_phase = self._active_phase
        self._active_phase = phase_id
        self._record_event("phase_start", phase_id=phase_id)
        try:
            yield
        finally:
            error = sys.exc_info()[1]
            metric = replace(
                metric,
                ended_at=utc_now(),
                duration_seconds=round(time.monotonic() - started_monotonic, 3),
                status="failed" if error is not None else "passed",
                error=(
                    f"{error.__class__.__name__}: {error}"
                    if error is not None
                    else None
                ),
            )
            self._metrics[phase_id] = metric
            self._record_event(
                "phase_end",
                phase_id=phase_id,
                status=metric.status,
                duration_seconds=metric.duration_seconds,
                error=metric.error,
            )
            self._active_phase = previous_phase

    def mark_status(
        self,
        phase_id: str,
        status: str,
        error: str | None = None,
    ) -> None:
        metric = self._metrics.get(phase_id)
        if metric is None:
            metric = PhaseMetric(phase_id=phase_id, started_at=utc_now())
        self._metrics[phase_id] = replace(metric, status=status, error=error)

    def link_session(self, session_id: str) -> None:
        active_phase = self._active_phase
        if active_phase is None:
            return
        metric = self._metrics.get(active_phase)
        if metric is None or session_id in metric.session_ids:
            return
        self._metrics[active_phase] = replace(
            metric,
            session_ids=(*metric.session_ids, session_id),
        )


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def exception_text(error: BaseException | None) -> str | None:
    return f"{error.__class__.__name__}: {error}" if error is not None else None
