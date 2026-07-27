from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum, unique
from typing import NewType, TypeAlias

TransportInvocationId = NewType("TransportInvocationId", str)


@unique
class TransportEventPhase(str, Enum):
    STARTED = "started"
    COMPLETED = "completed"
    TIMEOUT = "timeout"
    ERROR = "error"
    EXHAUSTED = "exhausted"


@unique
class RetryDecision(str, Enum):
    PENDING = "pending"
    COMPLETE = "complete"
    RETRY_SAME_SESSION = "retry_same_session"
    STOP = "stop"
    NO_REPOST = "no_repost"


@unique
class TransportEventReason(str, Enum):
    ATTEMPT_STARTED = "attempt_started"
    REQUEST_COMPLETED = "request_completed"
    REQUEST_TIMEOUT = "request_timeout"
    TRANSPORT_ERROR = "transport_error"
    HARD_ERROR = "hard_error"
    SESSION_ERROR = "session_error"
    RETRIES_EXHAUSTED = "retries_exhausted"
    POST_ACCEPTANCE_TIMEOUT = "post_acceptance_timeout"


@dataclass(frozen=True, slots=True)
class TransportAttemptDetails:
    session_id: str
    method: str
    path: str
    invocation_id: TransportInvocationId
    attempt: int
    max_attempts: int
    timeout_s: float
    elapsed_s: float
    retry_decision: RetryDecision
    reason: TransportEventReason
    exhausted: bool


@dataclass(frozen=True, slots=True)
class TransportAttemptStarted(TransportAttemptDetails):
    phase: TransportEventPhase = field(
        default=TransportEventPhase.STARTED,
        init=False,
    )


@dataclass(frozen=True, slots=True)
class TransportAttemptCompleted(TransportAttemptDetails):
    phase: TransportEventPhase = field(
        default=TransportEventPhase.COMPLETED,
        init=False,
    )


@dataclass(frozen=True, slots=True)
class TransportAttemptTimedOut(TransportAttemptDetails):
    phase: TransportEventPhase = field(
        default=TransportEventPhase.TIMEOUT,
        init=False,
    )


@dataclass(frozen=True, slots=True)
class TransportAttemptErrored(TransportAttemptDetails):
    phase: TransportEventPhase = field(
        default=TransportEventPhase.ERROR,
        init=False,
    )


@dataclass(frozen=True, slots=True)
class TransportAttemptsExhausted(TransportAttemptDetails):
    phase: TransportEventPhase = field(
        default=TransportEventPhase.EXHAUSTED,
        init=False,
    )


TransportAttemptEvent: TypeAlias = (
    TransportAttemptStarted
    | TransportAttemptCompleted
    | TransportAttemptTimedOut
    | TransportAttemptErrored
    | TransportAttemptsExhausted
)
TransportObserver: TypeAlias = Callable[[TransportAttemptEvent], None]


@dataclass(frozen=True, slots=True)
class TransportInvocation:
    session_id: str
    invocation_id: TransportInvocationId
    max_attempts: int
    timeout_s: float


@dataclass(frozen=True, slots=True)
class PreparedTransportAttempt:
    invocation: TransportInvocation
    attempt: int


@dataclass(frozen=True, slots=True)
class ActiveTransportAttempt:
    prepared: PreparedTransportAttempt
    started_at: float


@dataclass(frozen=True, slots=True)
class TransportDisposition:
    retry_decision: RetryDecision
    reason: TransportEventReason
    exhausted: bool


def attempt_started(prepared: PreparedTransportAttempt) -> TransportAttemptStarted:
    invocation = prepared.invocation
    return TransportAttemptStarted(
        session_id=invocation.session_id,
        method="POST",
        path=f"/session/{invocation.session_id}/message",
        invocation_id=invocation.invocation_id,
        attempt=prepared.attempt,
        max_attempts=invocation.max_attempts,
        timeout_s=invocation.timeout_s,
        elapsed_s=0.0,
        retry_decision=RetryDecision.PENDING,
        reason=TransportEventReason.ATTEMPT_STARTED,
        exhausted=False,
    )


def attempt_completed(
    active: ActiveTransportAttempt,
    elapsed_s: float,
) -> TransportAttemptCompleted:
    prepared = active.prepared
    invocation = prepared.invocation
    return TransportAttemptCompleted(
        session_id=invocation.session_id,
        method="POST",
        path=f"/session/{invocation.session_id}/message",
        invocation_id=invocation.invocation_id,
        attempt=prepared.attempt,
        max_attempts=invocation.max_attempts,
        timeout_s=invocation.timeout_s,
        elapsed_s=elapsed_s,
        retry_decision=RetryDecision.COMPLETE,
        reason=TransportEventReason.REQUEST_COMPLETED,
        exhausted=False,
    )


def attempt_timed_out(
    active: ActiveTransportAttempt,
    disposition: TransportDisposition,
    elapsed_s: float,
) -> TransportAttemptTimedOut:
    prepared = active.prepared
    invocation = prepared.invocation
    return TransportAttemptTimedOut(
        session_id=invocation.session_id,
        method="POST",
        path=f"/session/{invocation.session_id}/message",
        invocation_id=invocation.invocation_id,
        attempt=prepared.attempt,
        max_attempts=invocation.max_attempts,
        timeout_s=invocation.timeout_s,
        elapsed_s=elapsed_s,
        retry_decision=disposition.retry_decision,
        reason=disposition.reason,
        exhausted=disposition.exhausted,
    )


def attempt_errored(
    active: ActiveTransportAttempt,
    disposition: TransportDisposition,
    elapsed_s: float,
) -> TransportAttemptErrored:
    prepared = active.prepared
    invocation = prepared.invocation
    return TransportAttemptErrored(
        session_id=invocation.session_id,
        method="POST",
        path=f"/session/{invocation.session_id}/message",
        invocation_id=invocation.invocation_id,
        attempt=prepared.attempt,
        max_attempts=invocation.max_attempts,
        timeout_s=invocation.timeout_s,
        elapsed_s=elapsed_s,
        retry_decision=disposition.retry_decision,
        reason=disposition.reason,
        exhausted=disposition.exhausted,
    )


def attempts_exhausted(
    active: ActiveTransportAttempt,
    disposition: TransportDisposition,
    elapsed_s: float,
) -> TransportAttemptsExhausted:
    prepared = active.prepared
    invocation = prepared.invocation
    return TransportAttemptsExhausted(
        session_id=invocation.session_id,
        method="POST",
        path=f"/session/{invocation.session_id}/message",
        invocation_id=invocation.invocation_id,
        attempt=prepared.attempt,
        max_attempts=invocation.max_attempts,
        timeout_s=invocation.timeout_s,
        elapsed_s=elapsed_s,
        retry_decision=disposition.retry_decision,
        reason=disposition.reason,
        exhausted=True,
    )
