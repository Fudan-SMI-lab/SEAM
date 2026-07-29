from __future__ import annotations

import pytest
from typing_extensions import assert_never

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


def review_round(
    round_number: int,
    max_rounds: int,
    outcome: ReviewOutcome,
) -> ReviewRound:
    if outcome is ReviewOutcome.ACCEPTED:
        verdict = ReviewVerdict.ACCEPT
    elif (
        outcome is ReviewOutcome.REJECTED
        or outcome is ReviewOutcome.REJECT_EXHAUSTED
        or outcome is ReviewOutcome.IMPROVEMENT_ERROR
    ):
        verdict = ReviewVerdict.REJECT
    elif outcome is ReviewOutcome.UNKNOWN or outcome is ReviewOutcome.SESSION_ERROR:
        verdict = ReviewVerdict.UNKNOWN
    elif outcome is ReviewOutcome.DISABLED:
        pytest.fail("disabled review has no logical round")
    else:
        assert_never(outcome)
    return ReviewRound(round_number, max_rounds, verdict, outcome)


def review_rounds(outcome: ReviewOutcome) -> tuple[ReviewRound, ...]:
    if outcome is ReviewOutcome.DISABLED:
        return ()
    if outcome is ReviewOutcome.REJECT_EXHAUSTED:
        return (
            review_round(1, 3, ReviewOutcome.REJECTED),
            review_round(2, 3, ReviewOutcome.REJECTED),
            review_round(3, 3, ReviewOutcome.REJECT_EXHAUSTED),
        )
    return (review_round(1, 3, outcome),)


def run_outcome(
    validation_succeeded: bool,
    review_outcome: ReviewOutcome,
    review_fail_closed: bool,
) -> RunOutcome:
    return RunOutcome(
        validation_succeeded=validation_succeeded,
        review_outcome=review_outcome,
        review_fail_closed=review_fail_closed,
        workflow_terminal=WorkflowTerminal("complete"),
        terminal_anchor=TerminalAnchor(phase_id=PhaseId("phase_5_validation")),
        executed_phases=(PhaseId("phase_5_validation"),),
        accepted_attempt_id=AcceptedAttemptId("phase-5-attempt-3")
        if validation_succeeded
        else None,
        review_rounds=review_rounds(review_outcome),
    )


def outcome_from_history(
    review_outcome: ReviewOutcome,
    rounds: tuple[ReviewRound, ...],
) -> RunOutcome:
    return RunOutcome(
        validation_succeeded=True,
        review_outcome=review_outcome,
        review_fail_closed=True,
        workflow_terminal=WorkflowTerminal("complete"),
        terminal_anchor=TerminalAnchor(phase_id=PhaseId("phase_5_validation")),
        executed_phases=(PhaseId("phase_5_validation"),),
        accepted_attempt_id=AcceptedAttemptId("phase-5-attempt-2"),
        review_rounds=rounds,
    )
