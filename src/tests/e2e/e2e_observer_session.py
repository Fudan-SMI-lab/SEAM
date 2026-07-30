from __future__ import annotations

import logging
import sys
import time
from dataclasses import replace
from typing import Any, Protocol, final

from core.agent_io_logger import AgentIOLogger, redact_sensitive_text
from core.session_registry import TraceSeedAnnotator
from core.trace_correlation_models import FrameworkInvocationId
from core.ui_events import UIEventSink, summarize_text
from harness.session.opencode_contract import JsonValue
from harness.session.trace_seeds import SessionLifecycle

from .e2e_observer_models import CommandMetric, SessionMetric, exception_text, utc_now
from .e2e_observer_runtime import SessionManagerBackend

logger = logging.getLogger(__name__)

_OPTIONAL_LOGGER_FAILURES: tuple[type[Exception], ...] = (Exception,)


class SessionInstrumentationContext(Protocol):
    @property
    def active_phase(self) -> str | None: ...

    def link_session_to_phase(self, session_id: str) -> None: ...

    def record_event(self, event_type: str, **details: JsonValue) -> None: ...


@final
class ObservedSessionInstrumentation:
    def __init__(
        self,
        backend: SessionManagerBackend,
        context: SessionInstrumentationContext,
        agent_io_logger: AgentIOLogger | None,
        ui_event_sink: UIEventSink | None = None,
    ) -> None:
        self._backend = backend
        self._context = context
        self._agent_io_logger = agent_io_logger
        self._ui_event_sink = ui_event_sink
        self._command_sequence = 0
        self._active_framework_invocation_id: FrameworkInvocationId | None = None
        self._sessions: dict[str, SessionMetric] = {}
        self._commands: list[CommandMetric] = []

    def _emit_ui_event(self, event_type: str, **kwargs: Any) -> None:
        if self._ui_event_sink is None:
            return
        try:
            self._ui_event_sink.emit(event_type, **kwargs)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.debug("UI event emission failed", exc_info=True)

    @property
    def active_framework_invocation_id(self) -> FrameworkInvocationId | None:
        return self._active_framework_invocation_id

    @property
    def session_count(self) -> int:
        return len(self._sessions)

    @property
    def command_count(self) -> int:
        return len(self._commands)

    @property
    def sessions(self) -> tuple[SessionMetric, ...]:
        return tuple(self._sessions.values())

    @property
    def commands(self) -> tuple[CommandMetric, ...]:
        return tuple(self._commands)

    def role_for(self, session_id: str) -> str | None:
        metric = self._sessions.get(session_id)
        return metric.role if metric is not None else None

    def get_or_create(
        self,
        role: str,
        lifecycle: SessionLifecycle = "persistent",
        agent: str = "",
        title: str = "",
        working_dir: str = "",
        initial_prompt: str = "",
    ) -> str:
        session_id = self._backend.get_or_create(
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
                created_at=utc_now(),
            )
            self._sessions[session_id] = metric
            self._context.record_event(
                "session_ready",
                session_id=session_id,
                role=role,
                lifecycle=lifecycle,
            )
            self._emit_ui_event(
                "session_ready",
                phase_id=self._context.active_phase,
                agent_role=role,
                session_id=session_id,
                status="ready",
                message=f"{role} session ready",
                details={"lifecycle": lifecycle},
            )
        active_phase = self._context.active_phase
        if active_phase and active_phase not in metric.phases:
            self._sessions[session_id] = replace(
                metric, phases=(*metric.phases, active_phase)
            )
        return session_id

    def send_command(
        self,
        session_id: str,
        command: str,
        agent: str = "",
        timeout: int = 600,
        retries: int = 2,
    ) -> str:
        self._command_sequence += 1
        sequence = self._command_sequence
        framework_invocation_id = FrameworkInvocationId(f"framework-{sequence:06d}")
        previous_invocation_id = self._active_framework_invocation_id
        self._active_framework_invocation_id = framework_invocation_id
        started_at = utc_now()
        started_monotonic = time.monotonic()
        active_phase = self._context.active_phase
        response = ""
        metric = self._sessions.get(session_id)
        if metric is not None:
            phases = metric.phases
            if active_phase and active_phase not in phases:
                phases = (*phases, active_phase)
            self._sessions[session_id] = replace(
                metric,
                command_count=metric.command_count + 1,
                phases=phases,
            )
        self._context.link_session_to_phase(session_id)
        self._emit_ui_event(
            "agent_command_started",
            phase_id=active_phase,
            agent_role=self.role_for(session_id),
            session_id=session_id,
            status="running",
            message=summarize_text(command, 180),
            details={
                "timeout_seconds": timeout,
                "command_preview": summarize_text(command, 300),
                "command_sequence": sequence,
                "active_phase": active_phase,
            },
        )
        try:
            response = self._backend.send_command(
                session_id,
                command,
                agent=agent,
                timeout=timeout,
                retries=retries,
            )
            return response
        finally:
            try:
                error = sys.exc_info()[1]
                status = "failed" if error is not None else "passed"
                error_message = exception_text(error)
                ended_at = utc_now()
                duration_seconds = round(time.monotonic() - started_monotonic, 3)
                self._record_agent_io(
                    sequence,
                    session_id,
                    command,
                    response,
                    agent,
                    timeout,
                    started_at,
                    ended_at,
                    duration_seconds,
                    status,
                    error_message,
                )
                self._commands.append(
                    CommandMetric(
                        sequence=sequence,
                        phase_id=active_phase,
                        session_id=session_id,
                        timeout_seconds=timeout,
                        started_at=started_at,
                        duration_seconds=duration_seconds,
                        status=status,
                        command_length=len(command),
                        response_length=len(response),
                        error=(
                            redact_sensitive_text(error_message)
                            if error_message is not None
                            else None
                        ),
                    )
                )
                self._context.record_event(
                    "session_command",
                    session_id=session_id,
                    phase_id=active_phase,
                    status=status,
                    duration_seconds=duration_seconds,
                    command_length=len(command),
                    response_length=len(response),
                    error=error_message,
                )
                self._emit_ui_event(
                    "agent_command_finished",
                    phase_id=active_phase,
                    agent_role=self.role_for(session_id),
                    session_id=session_id,
                    status=status,
                    message=error_message or summarize_text(response, 180),
                    details={
                        "duration_seconds": duration_seconds,
                        "command_sequence": sequence,
                        "active_phase": active_phase,
                        "command_length": len(command),
                        "response_length": len(response),
                        "response_preview": summarize_text(response, 300),
                        "error": error_message,
                    },
                )
            finally:
                self._active_framework_invocation_id = previous_invocation_id

    def cleanup_all(self) -> int:
        cleaned = self._backend.cleanup_all()
        self._context.record_event("cleanup_all", cleaned_sessions=cleaned)
        return cleaned

    def annotate_trace_seed(
        self,
        session_id: str,
        logical_role: str | None,
        scope: str | None,
    ) -> None:
        if isinstance(self._backend, TraceSeedAnnotator):
            self._backend.annotate_trace_seed(session_id, logical_role, scope)

    def _record_agent_io(
        self,
        sequence: int,
        session_id: str,
        command: str,
        response: str,
        agent: str,
        timeout: int,
        started_at: str,
        ended_at: str,
        duration_seconds: float,
        status: str,
        error_message: str | None,
    ) -> None:
        if self._agent_io_logger is None:
            return
        metric = self._sessions.get(session_id)
        try:
            _ = self._agent_io_logger.record(
                sequence=sequence,
                phase_id=self._context.active_phase,
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
        except _OPTIONAL_LOGGER_FAILURES as error:
            self._context.record_event(
                "agent_io_log_error",
                session_id=session_id,
                phase_id=self._context.active_phase,
                error=f"{error.__class__.__name__}: {error}",
            )
