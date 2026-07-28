from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum, unique
from typing import TypedDict

from typing_extensions import override

from core.run_outcome import ReviewRound
from core.trace_correlation_models import FrameworkInvocationId


@dataclass(frozen=True, slots=True)
class ObservabilityContractError(ValueError):
    detail: str

    @override
    def __str__(self) -> str:
        return self.detail


@unique
class ImprovementStatus(str, Enum):
    NOT_REQUIRED = "not_required"
    APPLIED = "applied"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class CommandCorrelation:
    run_id: str
    session_id: str
    command_id: str

    def __post_init__(self) -> None:
        values = (self.run_id, self.session_id, self.command_id)
        if not all(value.strip() for value in values):
            raise ObservabilityContractError(
                "correlation identifiers must be non-empty"
            )


@dataclass(frozen=True, slots=True)
class ReviewScope:
    phase_id: str
    phase5_iteration: int
    reviewer_agent: str
    sub_phase: str

    def __post_init__(self) -> None:
        if self.phase5_iteration < 1:
            raise ObservabilityContractError("phase5_iteration must be positive")
        identifiers = (self.phase_id, self.reviewer_agent, self.sub_phase)
        if not all(identifier.strip() for identifier in identifiers):
            raise ObservabilityContractError(
                "review scope identifiers must be non-empty"
            )


@dataclass(frozen=True, slots=True)
class ReviewCompletion:
    correlation: CommandCorrelation
    scope: ReviewScope
    review_round: ReviewRound
    duration_seconds: float
    improvement_status: ImprovementStatus

    def __post_init__(self) -> None:
        if not math.isfinite(self.duration_seconds) or self.duration_seconds < 0:
            raise ObservabilityContractError(
                "review duration must be finite and non-negative"
            )

    @property
    def record_id(self) -> str:
        return (
            f"{self.correlation.run_id}:{self.scope.phase_id}:"
            f"review:{self.review_round.round_number}"
        )


@dataclass(frozen=True, slots=True)
class TimeoutScope:
    run_id: str
    agent: str
    sub_phase: str
    framework_invocation_id: FrameworkInvocationId | None = None

    def __post_init__(self) -> None:
        identifiers = (self.run_id, self.agent, self.sub_phase)
        if not all(identifier.strip() for identifier in identifiers):
            raise ObservabilityContractError(
                "timeout scope identifiers must be non-empty"
            )


class ReviewDetails(TypedDict):
    record_id: str
    run_id: str
    phase_execution_id: str
    review_round_id: str
    framework_invocation_id: str
    phase_id: str
    phase5_iteration: int
    logical_round: int
    max_rounds: int
    remaining_rounds: int
    verdict: str
    outcome: str
    duration_seconds: float
    improvement_status: str
    session_id: str
    command_id: str
    reviewer_agent: str
    sub_phase: str


class TimeoutDetails(TypedDict):
    record_id: str
    run_id: str
    phase_execution_id: str
    framework_invocation_id: str | None
    transport_invocation_id: str
    transport_attempt_id: str
    event_phase: str
    agent: str
    sub_phase: str
    session_id: str
    command_id: str
    attempt: int
    max_attempts: int
    configured_timeout_seconds: float
    elapsed_seconds: float
    retry_decision: str
    reason: str
    exhausted: bool


class ObservabilityAggregate(TypedDict):
    review_count: int
    timeout_count: int
    exhaustion_count: int
    dropped_event_count: int
    review_duration_seconds: float
    timeout_elapsed_seconds: float


class ObservabilityArtifact(TypedDict):
    schema_version: str
    aggregate: ObservabilityAggregate
    reviews: list[ReviewDetails]
    timeouts: list[TimeoutDetails]


@dataclass(frozen=True, slots=True)
class ObservabilitySummary:
    schema_version: str = "1.0"
    review_count: int = 0
    reviews: tuple[ReviewDetails, ...] = ()
    timeout_count: int = 0
    timeouts: tuple[TimeoutDetails, ...] = ()
    exhaustion_count: int = 0
    dropped_event_count: int = 0
    review_duration_seconds: float = 0.0
    timeout_elapsed_seconds: float = 0.0


EMPTY_OBSERVABILITY_SUMMARY = ObservabilitySummary()
