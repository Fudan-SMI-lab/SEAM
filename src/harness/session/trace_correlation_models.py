from __future__ import annotations

from dataclasses import dataclass

from core.run_manifest import RunId
from core.run_outcome import PhaseId, ReviewRound
from core.trace_correlation_models import (
    FrameworkInvocationId,
    PhaseExecutionId,
    ReviewRoundId,
    RunCorrelationScope,
    SessionId,
    ToolCallId,
    TransportAttemptId,
)
from harness.session.events import TransportInvocationId


@dataclass(frozen=True, slots=True)
class CorrelationDiagnostic:
    code: str
    record_kind: str
    record_id: str
    detail: str


@dataclass(frozen=True, slots=True)
class PhaseExecutionCorrelation:
    run_id: RunId
    phase_id: PhaseId
    phase_execution_id: PhaseExecutionId


@dataclass(frozen=True, slots=True)
class Phase5AttemptCorrelation:
    run_id: RunId
    phase_execution_id: PhaseExecutionId
    attempt_id: str
    attempt_number: int
    accepted: bool


@dataclass(frozen=True, slots=True)
class ReviewRoundCorrelation:
    run_id: RunId
    phase_execution_id: PhaseExecutionId
    phase5_iteration: int
    review_round_id: ReviewRoundId
    review_round: ReviewRound
    framework_invocation_id: FrameworkInvocationId
    session_id: SessionId
    record_id: str


@dataclass(frozen=True, slots=True)
class FrameworkInvocationCorrelation:
    run_id: RunId
    phase_execution_id: PhaseExecutionId
    invocation_id: FrameworkInvocationId
    session_id: SessionId


@dataclass(frozen=True, slots=True)
class TransportAttemptCorrelation:
    run_id: RunId
    phase_execution_id: PhaseExecutionId
    framework_invocation_id: FrameworkInvocationId | None
    transport_invocation_id: TransportInvocationId
    transport_attempt_id: TransportAttemptId
    session_id: SessionId
    attempt: int
    event_phase: str
    record_id: str


@dataclass(frozen=True, slots=True)
class SessionCorrelation:
    run_id: RunId
    root_session_id: SessionId
    session_id: SessionId
    parent_session_id: SessionId | None
    logical_role: str | None
    scope: str | None


@dataclass(frozen=True, slots=True)
class ToolCallCorrelation:
    run_id: RunId
    root_session_id: SessionId
    session_id: SessionId
    message_id: str
    part_id: str
    call_id: ToolCallId
    tool: str
    child_session_id: SessionId | None


@dataclass(frozen=True, slots=True)
class TraceCorrelationContext:
    scope: RunCorrelationScope
    phase_executions: tuple[PhaseExecutionCorrelation, ...]
    phase5_attempts: tuple[Phase5AttemptCorrelation, ...]
    review_rounds: tuple[ReviewRoundCorrelation, ...]
    framework_invocations: tuple[FrameworkInvocationCorrelation, ...]
    transport_attempts: tuple[TransportAttemptCorrelation, ...]
    diagnostics: tuple[CorrelationDiagnostic, ...]


@dataclass(frozen=True, slots=True)
class TraceCorrelationProjection:
    context: TraceCorrelationContext
    sessions: tuple[SessionCorrelation, ...]
    tool_calls: tuple[ToolCallCorrelation, ...]
    diagnostics: tuple[CorrelationDiagnostic, ...]

    @property
    def complete(self) -> bool:
        return not self.diagnostics
