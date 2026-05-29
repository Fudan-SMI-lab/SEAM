from __future__ import annotations

import json
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Protocol, cast

from core.agent_io_logger import AgentIOLogger


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _trim_text(text: str, limit: int = 500) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


@dataclass
class SessionMetric:
    session_id: str
    role: str
    lifecycle: str
    created_at: str
    command_count: int = 0
    phases: list[str] = field(default_factory=list)


@dataclass
class CommandMetric:  # pylint: disable=too-many-instance-attributes; silent
    sequence: int
    phase_id: str | None
    session_id: str
    timeout_seconds: int
    started_at: str
    duration_seconds: float
    status: str
    command_length: int
    response_length: int
    command_preview: str
    response_preview: str
    error: str | None = None


@dataclass
class PhaseMetric:
    phase_id: str
    started_at: str
    ended_at: str | None = None
    duration_seconds: float = 0.0
    status: str = "running"
    error: str | None = None
    session_ids: list[str] = field(default_factory=list)


class SessionManagerBackend(Protocol):
    def get_or_create(  # pylint: disable=too-many-arguments,too-many-positional-arguments; silent
        self,
        role: str,
        lifecycle: Literal["persistent", "reusable", "ephemeral"] = "persistent",
        agent: str = "",
        title: str = "",
        working_dir: str = "",
        initial_prompt: str = "",
    ) -> str: ...

    def send_command(  # pylint: disable=too-many-arguments,too-many-positional-arguments; silent
        self,
        session_id: str,
        command: str,
        agent: str = "",
        timeout: int = 600,
        retries: int = 2,
    ) -> str: ...

    def cleanup_all(self) -> int: ...


class TelemetryObserver:  # pylint: disable=too-many-instance-attributes; silent
    _session_mgr: SessionManagerBackend
    _output_dir: Path
    _run_started_at: str
    _run_started_monotonic: float
    _command_sequence: int
    _active_phase: str | None
    _sessions: dict[str, SessionMetric]
    _commands: list[CommandMetric]
    _phases: dict[str, PhaseMetric]
    _events: list[dict[str, object]]
    _metadata: dict[str, object]
    _agent_io_logger: AgentIOLogger | None

    def __init__(
        self,
        session_mgr: SessionManagerBackend,
        output_dir: str | Path,
        agent_io_logger: AgentIOLogger | None = None,
    ) -> None:
        self._session_mgr = session_mgr
        self._output_dir = Path(output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._run_started_at = _utc_now()
        self._run_started_monotonic = time.monotonic()
        self._command_sequence = 0
        self._active_phase = None
        self._sessions = {}
        self._commands = []
        self._phases = {}
        self._events = []
        self._metadata = {}
        self._agent_io_logger = agent_io_logger

    def __getattr__(self, name: str) -> object:
        return cast(object, getattr(self._session_mgr, name))

    @property
    def active_phase(self) -> str | None:
        return self._active_phase

    @property
    def session_count(self) -> int:
        return len(self._sessions)

    @property
    def command_count(self) -> int:
        return len(self._commands)

    @property
    def phase_metrics(self) -> dict[str, PhaseMetric]:
        return dict(self._phases)

    def set_metadata(self, key: str, value: object) -> None:
        self._metadata[key] = value

    def record_event(self, event_type: str, **details: object) -> None:
        self._events.append(
            {
                "event_type": event_type,
                "timestamp": _utc_now(),
                "phase_id": self._active_phase,
                "details": details,
            }
        )

    def set_active_phase(self, phase_id: str | None) -> None:
        self._active_phase = phase_id

    @contextmanager
    def timing_phase(self, phase_id: str) -> Iterator[None]:
        started_at = _utc_now()
        started_monotonic = time.monotonic()
        metric = PhaseMetric(phase_id=phase_id, started_at=started_at)
        self._phases[phase_id] = metric
        previous_phase = self._active_phase
        self._active_phase = phase_id
        self.record_event("phase_start", phase_id=phase_id)
        try:
            yield
        except Exception as exc:
            metric.status = "failed"
            metric.error = f"{exc.__class__.__name__}: {exc}"
            raise
        else:
            metric.status = "passed"
        finally:
            metric.ended_at = _utc_now()
            metric.duration_seconds = round(time.monotonic() - started_monotonic, 3)
            self.record_event(
                "phase_end",
                phase_id=phase_id,
                status=metric.status,
                duration_seconds=metric.duration_seconds,
                error=metric.error,
            )
            self._active_phase = previous_phase

    def mark_phase_status(self, phase_id: str, status: str, error: str | None = None) -> None:
        metric = self._phases.get(phase_id)
        if metric is None:
            metric = PhaseMetric(phase_id=phase_id, started_at=_utc_now())
            self._phases[phase_id] = metric
        metric.status = status
        metric.error = error

    def get_or_create(  # pylint: disable=too-many-arguments,too-many-positional-arguments; silent
        self,
        role: str,
        lifecycle: Literal["persistent", "reusable", "ephemeral"] = "persistent",
        agent: str = "",
        title: str = "",
        working_dir: str = "",
        initial_prompt: str = "",
    ) -> str:
        session_id = self._session_mgr.get_or_create(
            role=role,
            agent=agent,
            lifecycle=lifecycle,
            title=title,
            working_dir=working_dir,
            initial_prompt=initial_prompt,
        )
        metric = self._sessions.get(session_id)
        if metric is None:
            metric = SessionMetric(
                session_id=session_id,
                role=role,
                lifecycle=lifecycle,
                created_at=_utc_now(),
            )
            self._sessions[session_id] = metric
            self.record_event(
                "session_ready",
                session_id=session_id,
                role=role,
                lifecycle=lifecycle,
            )
        if self._active_phase and self._active_phase not in metric.phases:
            metric.phases.append(self._active_phase)
        return session_id

    # pylint: disable-next=too-many-arguments,too-many-locals,too-many-positional-arguments; silent
    def send_command(
        self,
        session_id: str,
        command: str,
        agent: str = "",
        timeout: int = 600,
        retries: int = 2,
    ) -> str:
        self._command_sequence += 1
        started_at = _utc_now()
        started_monotonic = time.monotonic()
        active_phase = self._active_phase
        status = "passed"
        response = ""
        error_message: str | None = None

        metric = self._sessions.get(session_id)
        if metric is not None:
            metric.command_count += 1
            if active_phase and active_phase not in metric.phases:
                metric.phases.append(active_phase)

        phase_metric = self._phases.get(active_phase or "") if active_phase else None
        if phase_metric is not None and session_id not in phase_metric.session_ids:
            phase_metric.session_ids.append(session_id)

        try:
            response = self._session_mgr.send_command(
                session_id,
                command,
                agent=agent,
                timeout=timeout,
                retries=retries,
            )
            return response
        except Exception as exc:
            status = "failed"
            error_message = f"{exc.__class__.__name__}: {exc}"
            raise
        finally:
            ended_at = _utc_now()
            duration_seconds = round(time.monotonic() - started_monotonic, 3)
            if self._agent_io_logger is not None:
                try:
                    _ = self._agent_io_logger.record(
                        sequence=self._command_sequence,
                        phase_id=active_phase,
                        session_id=session_id,
                        role=metric.role if metric is not None else None,
                        agent=agent or None,
                        lifecycle=metric.lifecycle if metric is not None else None,
                        started_at=started_at,
                        ended_at=ended_at,
                        duration_seconds=duration_seconds,
                        timeout_seconds=timeout,
                        status=status,
                        command=command,
                        response=response,
                        error=error_message,
                    )
                except Exception as exc:  # pylint: disable=broad-exception-caught; silent
                    self.record_event(
                        "agent_io_log_error",
                        session_id=session_id,
                        phase_id=active_phase,
                        error=f"{exc.__class__.__name__}: {exc}",
                    )
            self._commands.append(
                CommandMetric(
                    sequence=self._command_sequence,
                    phase_id=active_phase,
                    session_id=session_id,
                    timeout_seconds=timeout,
                    started_at=started_at,
                    duration_seconds=duration_seconds,
                    status=status,
                    command_length=len(command),
                    response_length=len(response),
                    command_preview=_trim_text(command),
                    response_preview=_trim_text(response),
                    error=error_message,
                )
            )
            self.record_event(
                "session_command",
                session_id=session_id,
                phase_id=active_phase,
                status=status,
                duration_seconds=duration_seconds,
                command_length=len(command),
                response_length=len(response),
                error=error_message,
            )

    def cleanup_all(self) -> int:
        cleaned = self._session_mgr.cleanup_all()
        self.record_event("cleanup_all", cleaned_sessions=cleaned)
        return cleaned

    def save_metrics(self) -> dict[str, str]:
        output_path = self._output_dir / "telemetry.json"
        metadata: dict[str, object] = {
            "run_started_at": self._run_started_at,
            "generated_at": _utc_now(),
            "elapsed_seconds": round(time.monotonic() - self._run_started_monotonic, 3),
            "session_count": self.session_count,
            "command_count": self.command_count,
            **self._metadata,
        }
        if self._agent_io_logger is not None:
            metadata["agent_io_paths"] = self._agent_io_logger.paths()

        payload = {
            "metadata": metadata,
            "phases": [asdict(metric) for metric in self._phases.values()],
            "sessions": [asdict(metric) for metric in self._sessions.values()],
            "commands": [asdict(metric) for metric in self._commands],
            "events": self._events,
        }
        _ = output_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return {"telemetry_json": str(output_path)}
