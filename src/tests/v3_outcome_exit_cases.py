from __future__ import annotations

from dataclasses import dataclass, replace
import inspect
from pathlib import Path

import pytest
from typing_extensions import assert_never

from core.review_gate import ReviewGate
from core.run_outcome import (
    PhaseId,
    ReviewOutcome,
    ReviewVerdict,
    TerminalOutcome,
    WorkflowTerminal,
)
from core.v3_outcome_mapping import (
    ExecutedPhase,
    Phase5Inputs,
    V3RunFacts,
    build_v3_run_outcome,
    evaluate_phase5,
)
from harness.run import RunFinalizationRequest, finalize_run
from tests.run_finalizer_test_support import FinalizerScenario, finalization_request


@dataclass(frozen=True, slots=True)
class ExitCase:
    validation_succeeded: bool
    review_outcome: ReviewOutcome
    fail_closed: bool
    expected_outcome: TerminalOutcome
    expected_exit: int


def _review_gate(outcome: ReviewOutcome) -> ReviewGate | None:
    if outcome is ReviewOutcome.DISABLED:
        return None
    elif outcome is ReviewOutcome.ACCEPTED:
        return ReviewGate(max_rounds=1).record_judgment(ReviewVerdict.ACCEPT)
    elif outcome is ReviewOutcome.REJECTED:
        return ReviewGate(max_rounds=2).record_judgment(ReviewVerdict.REJECT)
    elif outcome is ReviewOutcome.REJECT_EXHAUSTED:
        return ReviewGate(max_rounds=1).record_judgment(ReviewVerdict.REJECT)
    elif outcome is ReviewOutcome.UNKNOWN:
        return ReviewGate(max_rounds=1).record_judgment(ReviewVerdict.UNKNOWN)
    elif outcome is ReviewOutcome.SESSION_ERROR:
        return ReviewGate(max_rounds=1).record_session_error()
    elif outcome is ReviewOutcome.IMPROVEMENT_ERROR:
        rejected = ReviewGate(max_rounds=2).record_judgment(ReviewVerdict.REJECT)
        return rejected.record_improvement_error()
    else:
        assert_never(outcome)


@pytest.mark.parametrize(
    "case",
    [
        ExitCase(True, ReviewOutcome.ACCEPTED, True, TerminalOutcome.PASSED, 0),
        ExitCase(False, ReviewOutcome.ACCEPTED, True, TerminalOutcome.FAILED, 1),
        ExitCase(
            True,
            ReviewOutcome.REJECT_EXHAUSTED,
            True,
            TerminalOutcome.FAILED,
            1,
        ),
        ExitCase(
            True,
            ReviewOutcome.REJECT_EXHAUSTED,
            False,
            TerminalOutcome.PASSED_WITH_REVIEWS,
            0,
        ),
        ExitCase(True, ReviewOutcome.UNKNOWN, False, TerminalOutcome.FAILED, 1),
        ExitCase(True, ReviewOutcome.SESSION_ERROR, False, TerminalOutcome.FAILED, 1),
        ExitCase(
            True,
            ReviewOutcome.IMPROVEMENT_ERROR,
            False,
            TerminalOutcome.FAILED,
            1,
        ),
    ],
)
def test_frozen_outcome_controls_summary_and_process_exit(
    tmp_path: Path,
    case: ExitCase,
) -> None:
    # Given
    gate = _review_gate(case.review_outcome)
    decision = evaluate_phase5(
        Phase5Inputs(
            validation_succeeded=case.validation_succeeded,
            review_enabled=True,
            review_gate=gate,
            review_fail_closed=case.fail_closed,
            max_review_rounds=gate.max_rounds if gate is not None else 1,
            accepted_attempt_number=2,
        )
    )
    outcome = build_v3_run_outcome(
        V3RunFacts(
            executed_phases=(
                ExecutedPhase(
                    phase_id=PhaseId("phase_5_validation"),
                    disposition=decision.parent_disposition,
                ),
            ),
            workflow_terminal=WorkflowTerminal("complete"),
            phase5_decision=decision,
        )
    )
    request = finalization_request(tmp_path, FinalizerScenario())

    # When
    result = finalize_run(replace(request, authoritative_outcome=outcome))

    # Then
    assert outcome.terminal_outcome is case.expected_outcome
    assert result.outcome is case.expected_outcome
    assert result.summary.overall_status == (
        "FAIL" if case.expected_outcome is TerminalOutcome.FAILED else "PASS"
    )
    assert result.exit_code == case.expected_exit


def test_finalizer_fails_closed_without_frozen_outcome(tmp_path: Path) -> None:
    # Given
    request = finalization_request(tmp_path, FinalizerScenario())

    # When
    result = finalize_run(replace(request, authoritative_outcome=None))

    # Then
    assert result.outcome is TerminalOutcome.FAILED
    assert result.exit_code == 1


def test_finalizer_ignores_conflicting_phase_strings_and_errors(tmp_path: Path) -> None:
    # Given
    request = finalization_request(tmp_path, FinalizerScenario())
    misleading = replace(
        request.execution,
        phases=(replace(request.execution.phases[0], status="failed"),),
        errors=("misleading log says migration failed",),
    )

    # When
    result = finalize_run(replace(request, execution=misleading))

    # Then
    assert result.outcome is TerminalOutcome.PASSED
    assert result.summary.overall_status == "PASS"
    assert result.exit_code == 0


def test_finalization_request_requires_authoritative_outcome() -> None:
    # Given
    signature = inspect.signature(RunFinalizationRequest)

    # When
    default = signature.parameters["authoritative_outcome"].default

    # Then
    assert default is inspect.Parameter.empty
