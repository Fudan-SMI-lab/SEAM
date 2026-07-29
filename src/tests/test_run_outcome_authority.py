from __future__ import annotations

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
    WorkflowTerminal,
)


def _outcome(
    *,
    validation_succeeded: bool,
    accepted_attempt_id: AcceptedAttemptId | None,
    terminal_anchor: PhaseId = PhaseId("phase_5_validation"),
    executed_phases: tuple[PhaseId, ...] = (PhaseId("phase_5_validation"),),
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
