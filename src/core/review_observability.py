from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Final, Protocol, runtime_checkable

from core.review_gate import ReviewGate
from core.runtime_observability_models import (
    CommandCorrelation,
    ImprovementStatus,
    ObservabilityContractError,
    ReviewCompletion,
    ReviewScope,
)

REVIEW_RECEIPT_STATE_KEY: Final = "_review_observability_receipt"
logger = logging.getLogger("core.review_observability")


@dataclass(frozen=True)
class ReviewCommandReceipt:
    session_id: str
    command_id: str
    reviewer_agent: str
    sub_phase: str
    duration_seconds: float

    def __post_init__(self) -> None:
        identifiers = (
            self.session_id,
            self.command_id,
            self.reviewer_agent,
            self.sub_phase,
        )
        if not all(identifier.strip() for identifier in identifiers):
            raise ObservabilityContractError(
                "review receipt identifiers must be non-empty"
            )
        if not math.isfinite(self.duration_seconds) or self.duration_seconds < 0:
            raise ObservabilityContractError("review receipt duration must be finite")


@dataclass(frozen=True)
class ReviewTransition:
    phase_id: str
    phase5_iteration: int
    previous_round_count: int
    gate: ReviewGate
    receipt: ReviewCommandReceipt
    improvement_status: ImprovementStatus


@dataclass(frozen=True)
class ReviewObserverDiagnostic:
    observer_type: str
    error_type: str


@runtime_checkable
class ReviewCompletionObserver(Protocol):
    @property
    def run_id(self) -> str | None: ...

    def record_review_completion(self, completion: ReviewCompletion) -> bool: ...


def publish_review_transition(
    observer: ReviewCompletionObserver | None,
    transition: ReviewTransition,
) -> bool:
    try:
        # A runtime-checkable protocol may access descriptors while checking
        # structural conformance. Keep that observer-owned code inside the
        # same outcome-neutral boundary as the later property/method calls.
        if not isinstance(observer, ReviewCompletionObserver):
            return False
        if len(transition.gate.rounds) != transition.previous_round_count + 1:
            logger.warning("Review observability dropped stale or repeated transition")
            return False
        run_id = observer.run_id
        if run_id is None or not run_id.strip():
            logger.warning("Review observability skipped: missing run correlation")
            return False
        review_round = transition.gate.rounds[-1]
        completion = ReviewCompletion(
            correlation=CommandCorrelation(
                run_id=run_id,
                session_id=transition.receipt.session_id,
                command_id=transition.receipt.command_id,
            ),
            scope=ReviewScope(
                phase_id=transition.phase_id,
                phase5_iteration=transition.phase5_iteration,
                reviewer_agent=transition.receipt.reviewer_agent,
                sub_phase=transition.receipt.sub_phase,
            ),
            review_round=review_round,
            duration_seconds=transition.receipt.duration_seconds,
            improvement_status=transition.improvement_status,
        )
        return observer.record_review_completion(completion)
    except Exception as exc:
        diagnostic = ReviewObserverDiagnostic(
            observer_type=type(observer).__name__,
            error_type=type(exc).__name__,
        )
        logger.warning(
            "Review observability observer failed: observer=%s error=%s",
            diagnostic.observer_type,
            diagnostic.error_type,
        )
        return False
