from __future__ import annotations

from typing import Final

import pytest

from core.run_outcome import (
    AcceptedAttemptId,
    OutcomeContractError,
    PhaseId,
    ReviewOutcome,
    ReviewRound,
    ReviewVerdict,
    RunOutcome,
    TerminalAnchor,
    TerminalOutcome,
    WorkflowTerminal,
)

_PHASE5: Final = PhaseId("phase_5_validation")
_PHASE5_EXECUTION: Final = (_PHASE5,)


def _outcome(
    *,
    validation_succeeded: bool,
    accepted_attempt_id: AcceptedAttemptId | None,
    terminal_anchor: PhaseId = _PHASE5,
    executed_phases: tuple[PhaseId, ...] = _PHASE5_EXECUTION,
) -> RunOutcome:
    return RunOutcome(
        validation_succeeded=validation_succeeded,
        review_outcome=ReviewOutcome.DISABLED,
        review_fail_closed=True,
        workflow_terminal=WorkflowTerminal("complete"),
        terminal_anchor=TerminalAnchor(phase_id=terminal_anchor),
        executed_phases=executed_phases,
        accepted_attempt_id=accepted_attempt_id,
        review_rounds=(),
    )


def test_pass_rejects_missing_accepted_attempt() -> None:
    with pytest.raises(OutcomeContractError, match="accepted attempt"):
        _ = RunOutcome(
            validation_succeeded=True,
            review_outcome=ReviewOutcome.ACCEPTED,
            review_fail_closed=True,
            workflow_terminal=WorkflowTerminal("complete"),
            terminal_anchor=TerminalAnchor(phase_id=PhaseId("phase_5_validation")),
            executed_phases=(PhaseId("phase_5_validation"),),
            accepted_attempt_id=None,
            review_rounds=(
                ReviewRound(
                    round_number=1,
                    max_rounds=1,
                    verdict=ReviewVerdict.ACCEPT,
                    outcome=ReviewOutcome.ACCEPTED,
                ),
            ),
        )


def test_review_disabled_pass_rejects_missing_accepted_attempt() -> None:
    # Given successful validation with review disabled and no accepted attempt.
    # When / Then ordinary PASS authority is rejected at construction.
    with pytest.raises(OutcomeContractError, match="accepted attempt"):
        _ = _outcome(validation_succeeded=True, accepted_attempt_id=None)


def test_review_disabled_pass_retains_accepted_attempt() -> None:
    # Given review-disabled validation with accepted Phase 5 evidence.
    attempt_id = AcceptedAttemptId("phase-5-attempt-accepted")

    # When ordinary PASS authority is constructed.
    outcome = _outcome(validation_succeeded=True, accepted_attempt_id=attempt_id)

    # Then the accepted attempt remains explicit authority.
    assert outcome.terminal_outcome is TerminalOutcome.PASSED
    assert outcome.accepted_attempt_id == attempt_id


def test_reject_exhausted_compatibility_passes_without_accepted_attempt() -> None:
    # Given successful validation that exhausted review in compatibility mode.
    review_round = ReviewRound(
        round_number=1,
        max_rounds=1,
        verdict=ReviewVerdict.REJECT,
        outcome=ReviewOutcome.REJECT_EXHAUSTED,
    )

    # When compatibility authority is constructed without accepted evidence.
    outcome = RunOutcome(
        validation_succeeded=True,
        review_outcome=ReviewOutcome.REJECT_EXHAUSTED,
        review_fail_closed=False,
        workflow_terminal=WorkflowTerminal("complete"),
        terminal_anchor=TerminalAnchor(phase_id=PhaseId("phase_5_validation")),
        executed_phases=(PhaseId("phase_5_validation"),),
        accepted_attempt_id=None,
        review_rounds=(review_round,),
    )

    # Then plan-approved compatibility remains distinct from ordinary PASS.
    assert outcome.terminal_outcome is TerminalOutcome.PASSED_WITH_REVIEWS
    assert outcome.accepted_attempt_id is None


def test_later_failure_retains_accepted_attempt_and_failure_anchor() -> None:
    outcome = _outcome(
        validation_succeeded=False,
        accepted_attempt_id=AcceptedAttemptId("phase-5-attempt-1"),
        terminal_anchor=PhaseId("phase_6_report"),
    )

    assert outcome.accepted_attempt_id == "phase-5-attempt-1"
    assert outcome.terminal_anchor.phase_id == "phase_6_report"


@pytest.mark.parametrize("raw", ["", "../complete", "complete terminal", "x" * 129])
def test_workflow_terminal_rejects_unvalidated_strings(raw: str) -> None:
    with pytest.raises(OutcomeContractError, match="workflow terminal"):
        _ = WorkflowTerminal(raw)
