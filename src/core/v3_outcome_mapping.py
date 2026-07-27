from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, unique
from typing import Final

from typing_extensions import assert_never, override

from core.review_gate import ReviewGate
from core.run_outcome import (
    AcceptedAttemptId,
    PhaseId,
    ReviewOutcome,
    ReviewRound,
    ReviewVerdict,
    RunOutcome,
    TerminalAnchor,
    WorkflowTerminal,
)


@unique
class PhaseDisposition(str, Enum):
    SUCCEEDED = "success"
    FAILED = "failure"
    SKIPPED = "skipped"
    DISPATCHED = "dispatched"


@unique
class Phase5ExecutionDisposition(str, Enum):
    COMPLETED = "completed"
    FAILED = "failed"


_PHASE5_COMPLETED_STATUSES: Final = frozenset(
    {
        "success",
        "accept",
        ReviewOutcome.ACCEPTED.value,
        ReviewOutcome.REJECTED.value,
        ReviewOutcome.REJECT_EXHAUSTED.value,
        ReviewOutcome.UNKNOWN.value,
        ReviewOutcome.SESSION_ERROR.value,
        ReviewOutcome.IMPROVEMENT_ERROR.value,
    }
)


@dataclass(frozen=True, slots=True)
class ExecutedPhase:
    phase_id: PhaseId
    disposition: PhaseDisposition


@dataclass(frozen=True, slots=True)
class Phase5Inputs:
    validation_succeeded: bool
    review_enabled: bool
    review_gate: ReviewGate | None
    review_fail_closed: bool
    max_review_rounds: int
    accepted_attempt_number: int | None


@dataclass(frozen=True, slots=True)
class Phase5Decision:
    validation_succeeded: bool
    review_outcome: ReviewOutcome
    review_rounds: tuple[ReviewRound, ...]
    review_fail_closed: bool
    accepted_attempt_id: AcceptedAttemptId | None
    parent_disposition: PhaseDisposition


@dataclass(frozen=True, slots=True)
class V3RunFacts:
    executed_phases: tuple[ExecutedPhase, ...]
    workflow_terminal: WorkflowTerminal
    phase5_decision: Phase5Decision | None
    review_disabled_validation_succeeded: bool = False
    terminal_failure_anchor: PhaseId | None = None


@dataclass(frozen=True, slots=True)
class V3OutcomeUnavailableError(RuntimeError):
    detail: str

    @override
    def __str__(self) -> str:
        return self.detail


def parse_phase_disposition(raw: str) -> PhaseDisposition:
    normalized = PhaseDisposition.SUCCEEDED.value if raw == "passed" else raw
    try:
        return PhaseDisposition(normalized)
    except ValueError:
        return PhaseDisposition.FAILED


def parse_phase5_execution_disposition(
    raw: str | None,
) -> Phase5ExecutionDisposition:
    if raw in _PHASE5_COMPLETED_STATUSES:
        return Phase5ExecutionDisposition.COMPLETED
    return Phase5ExecutionDisposition.FAILED


def _unknown_review_round(max_rounds: int) -> ReviewRound:
    return ReviewRound(
        round_number=1,
        max_rounds=max_rounds,
        verdict=ReviewVerdict.UNKNOWN,
        outcome=ReviewOutcome.UNKNOWN,
    )


def _review_facts(
    inputs: Phase5Inputs,
) -> tuple[ReviewOutcome, tuple[ReviewRound, ...]]:
    if not inputs.review_enabled:
        return ReviewOutcome.DISABLED, ()
    gate = inputs.review_gate
    if gate is None or gate.outcome is None:
        unknown = _unknown_review_round(inputs.max_review_rounds)
        return ReviewOutcome.UNKNOWN, (unknown,)
    return gate.outcome, gate.rounds


def evaluate_phase5(inputs: Phase5Inputs) -> Phase5Decision:
    review_outcome, review_rounds = _review_facts(inputs)
    match review_outcome:
        case ReviewOutcome.DISABLED | ReviewOutcome.ACCEPTED:
            accepted_attempt_id = (
                AcceptedAttemptId(
                    f"phase_5_validation-attempt-{inputs.accepted_attempt_number}"
                )
                if inputs.validation_succeeded
                and inputs.accepted_attempt_number is not None
                else None
            )
        case (
            ReviewOutcome.REJECTED
            | ReviewOutcome.REJECT_EXHAUSTED
            | ReviewOutcome.UNKNOWN
            | ReviewOutcome.SESSION_ERROR
            | ReviewOutcome.IMPROVEMENT_ERROR
        ):
            accepted_attempt_id = None
        case unreachable:
            assert_never(unreachable)
    if not inputs.validation_succeeded:
        parent_disposition = PhaseDisposition.FAILED
    else:
        match review_outcome:
            case ReviewOutcome.DISABLED | ReviewOutcome.ACCEPTED:
                parent_disposition = PhaseDisposition.SUCCEEDED
            case ReviewOutcome.REJECT_EXHAUSTED:
                parent_disposition = (
                    PhaseDisposition.FAILED
                    if inputs.review_fail_closed
                    else PhaseDisposition.SUCCEEDED
                )
            case (
                ReviewOutcome.REJECTED
                | ReviewOutcome.UNKNOWN
                | ReviewOutcome.SESSION_ERROR
                | ReviewOutcome.IMPROVEMENT_ERROR
            ):
                parent_disposition = PhaseDisposition.FAILED
            case unreachable:
                assert_never(unreachable)
    return Phase5Decision(
        validation_succeeded=inputs.validation_succeeded,
        review_outcome=review_outcome,
        review_rounds=review_rounds,
        review_fail_closed=inputs.review_fail_closed,
        accepted_attempt_id=accepted_attempt_id,
        parent_disposition=parent_disposition,
    )


def _terminal_anchor(facts: V3RunFacts) -> TerminalAnchor:
    if facts.terminal_failure_anchor is not None:
        return TerminalAnchor(phase_id=facts.terminal_failure_anchor)
    failed_phases = tuple(
        phase.phase_id
        for phase in facts.executed_phases
        if phase.disposition is PhaseDisposition.FAILED
    )
    if failed_phases:
        return TerminalAnchor(phase_id=failed_phases[-1])
    if facts.phase5_decision is not None:
        return TerminalAnchor(phase_id=PhaseId("phase_5_validation"))
    if facts.executed_phases:
        return TerminalAnchor(phase_id=facts.executed_phases[-1].phase_id)
    return TerminalAnchor(phase_id=PhaseId("workflow_start"))


def build_v3_run_outcome(facts: V3RunFacts) -> RunOutcome:
    match facts.phase5_decision:
        case None:
            validation_succeeded = (
                facts.review_disabled_validation_succeeded
                and bool(facts.executed_phases)
                and facts.terminal_failure_anchor is None
                and all(
                    phase.disposition is not PhaseDisposition.FAILED
                    for phase in facts.executed_phases
                )
            )
            review_outcome = ReviewOutcome.DISABLED
            review_fail_closed = True
            accepted_attempt_id = None
            review_rounds: tuple[ReviewRound, ...] = ()
        case Phase5Decision() as phase5:
            later_or_prior_failure = any(
                phase.disposition is PhaseDisposition.FAILED
                and phase.phase_id != PhaseId("phase_5_validation")
                for phase in facts.executed_phases
            )
            validation_succeeded = (
                phase5.validation_succeeded
                and not later_or_prior_failure
                and facts.terminal_failure_anchor is None
            )
            review_outcome = phase5.review_outcome
            review_fail_closed = phase5.review_fail_closed
            accepted_attempt_id = phase5.accepted_attempt_id
            review_rounds = phase5.review_rounds
        case unreachable:
            assert_never(unreachable)
    return RunOutcome(
        validation_succeeded=validation_succeeded,
        review_outcome=review_outcome,
        review_fail_closed=review_fail_closed,
        workflow_terminal=facts.workflow_terminal,
        terminal_anchor=_terminal_anchor(facts),
        executed_phases=tuple(phase.phase_id for phase in facts.executed_phases),
        accepted_attempt_id=accepted_attempt_id,
        review_rounds=review_rounds,
    )
