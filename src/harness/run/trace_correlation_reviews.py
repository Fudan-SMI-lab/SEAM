from __future__ import annotations

from dataclasses import dataclass

from core.run_manifest import RunId
from core.run_outcome import PhaseId, ReviewRound
from core.runtime_observability_models import ObservabilitySummary
from core.trace_correlation_models import (
    FrameworkInvocationId,
    ReviewRoundId,
    RunCorrelationScope,
    SessionId,
    make_phase_execution_id,
    make_review_round_id,
)
from harness.session.trace_correlation_models import (
    CorrelationDiagnostic,
    FrameworkInvocationCorrelation,
    ReviewRoundCorrelation,
)


@dataclass(frozen=True)
class ReviewCorrelationRequest:
    scope: RunCorrelationScope
    phase_ids: frozenset[str]
    review_rounds: tuple[ReviewRound, ...]
    observability: ObservabilitySummary


@dataclass(frozen=True)
class ReviewCorrelationResult:
    reviews: tuple[ReviewRoundCorrelation, ...]
    invocations: tuple[FrameworkInvocationCorrelation, ...]
    diagnostics: tuple[CorrelationDiagnostic, ...]


def build_review_correlations(
    request: ReviewCorrelationRequest,
) -> ReviewCorrelationResult:
    reviews: list[ReviewRoundCorrelation] = []
    invocations: dict[FrameworkInvocationId, FrameworkInvocationCorrelation] = {}
    diagnostics: list[CorrelationDiagnostic] = []
    records: set[str] = set()
    observed_rounds: set[int] = set()
    for details in request.observability.reviews:
        record_id = details["record_id"]
        if record_id in records:
            diagnostics.append(_diagnostic("duplicate_record", record_id))
            continue
        records.add(record_id)
        run_id = RunId(details["run_id"])
        if run_id != request.scope.run_id:
            diagnostics.append(_diagnostic("cross_run_record", record_id))
        phase_id = PhaseId(details["phase_id"])
        execution_id = make_phase_execution_id(run_id, phase_id)
        if details["phase_execution_id"] != execution_id:
            diagnostics.append(_diagnostic("contradictory_phase", record_id))
        if str(phase_id) not in request.phase_ids:
            diagnostics.append(_diagnostic("orphan_phase", record_id))
        round_number = details["logical_round"]
        observed_rounds.add(round_number)
        typed = next(
            (
                item
                for item in request.review_rounds
                if item.round_number == round_number
            ),
            None,
        )
        if typed is None or (
            typed.max_rounds != details["max_rounds"]
            or typed.verdict.value != details["verdict"]
            or typed.outcome.value != details["outcome"]
        ):
            diagnostics.append(_diagnostic("contradictory_review", record_id))
            continue
        expected_round_id = make_review_round_id(run_id, phase_id, round_number)
        if details["review_round_id"] != expected_round_id:
            diagnostics.append(_diagnostic("contradictory_review", record_id))
        invocation_id = FrameworkInvocationId(details["framework_invocation_id"])
        session_id = SessionId(details["session_id"])
        invocation = FrameworkInvocationCorrelation(
            run_id, execution_id, invocation_id, session_id
        )
        existing = invocations.get(invocation_id)
        if existing is not None and existing != invocation:
            diagnostics.append(
                CorrelationDiagnostic(
                    "contradictory_invocation",
                    "framework_invocation",
                    str(invocation_id),
                    "",
                )
            )
        else:
            invocations[invocation_id] = invocation
        reviews.append(
            ReviewRoundCorrelation(
                run_id=run_id,
                phase_execution_id=execution_id,
                phase5_iteration=details["phase5_iteration"],
                review_round_id=ReviewRoundId(details["review_round_id"]),
                review_round=typed,
                framework_invocation_id=invocation_id,
                session_id=session_id,
                record_id=record_id,
            )
        )
    for review_round in request.review_rounds:
        if review_round.round_number not in observed_rounds:
            diagnostics.append(
                CorrelationDiagnostic(
                    "orphan_review_round",
                    "review_round",
                    str(review_round.round_number),
                    "no observability record",
                )
            )
    return ReviewCorrelationResult(
        tuple(reviews), tuple(invocations.values()), tuple(diagnostics)
    )


def _diagnostic(code: str, record_id: str) -> CorrelationDiagnostic:
    return CorrelationDiagnostic(code, "review_round", record_id, "")


__all__ = (
    "ReviewCorrelationRequest",
    "ReviewCorrelationResult",
    "build_review_correlations",
)
