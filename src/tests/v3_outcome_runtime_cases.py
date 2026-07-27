from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict
from unittest.mock import MagicMock

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
from core.types import PhaseDefinition, SubWorkflowDefinition, WorkflowDefinition
from core.workflow_executor import WorkflowExecutor


@dataclass(frozen=True, slots=True)
class OutcomeCase:
    review_outcome: ReviewOutcome
    validation_succeeded: bool
    fail_closed: bool
    expected: TerminalOutcome


class AttemptRecord(TypedDict):
    attempt: int


class LoopStateRecord(TypedDict):
    script_exit_code: int
    latest_shell_attempt_artifacts: AttemptRecord


class LoopResultRecord(TypedDict):
    status: str
    iterations: int
    loop_state: LoopStateRecord
    review_gate: ReviewGate | None
    review_outcome: ReviewOutcome | None


def _review_gate(outcome: ReviewOutcome) -> ReviewGate | None:
    match outcome:
        case ReviewOutcome.DISABLED:
            return None
        case ReviewOutcome.ACCEPTED:
            return ReviewGate().record_judgment(ReviewVerdict.ACCEPT)
        case ReviewOutcome.REJECTED:
            return ReviewGate().record_judgment(ReviewVerdict.REJECT)
        case ReviewOutcome.REJECT_EXHAUSTED:
            return ReviewGate(max_rounds=1).record_judgment(ReviewVerdict.REJECT)
        case ReviewOutcome.UNKNOWN:
            return ReviewGate().record_judgment(ReviewVerdict.UNKNOWN)
        case ReviewOutcome.SESSION_ERROR:
            return ReviewGate().record_session_error()
        case ReviewOutcome.IMPROVEMENT_ERROR:
            rejected = ReviewGate().record_judgment(ReviewVerdict.REJECT)
            return rejected.record_improvement_error()
        case unreachable:
            assert_never(unreachable)


def _active_executor(
    tmp_path: Path,
    phase: PhaseDefinition,
    fail_closed: bool,
) -> WorkflowExecutor:
    workflow = WorkflowDefinition(
        name="active-v3-task-8",
        version="1.0",
        globals={
            "review_fail_closed": fail_closed,
            "review_gate_enabled": True,
        },
        phases=[phase],
        terminals=["complete", "failed", "yaml_terminal"],
    )
    executor = WorkflowExecutor(
        workflow,
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )
    executor.hook_manager = MagicMock()
    return executor


def _loop_result(case: OutcomeCase) -> LoopResultRecord:
    gate = _review_gate(case.review_outcome)
    return {
        "status": "success",
        "iterations": 1,
        "loop_state": {
            "script_exit_code": 0 if case.validation_succeeded else 1,
            "latest_shell_attempt_artifacts": {"attempt": 4},
        },
        "review_gate": gate,
        "review_outcome": gate.outcome if gate is not None else None,
    }


@pytest.mark.parametrize(
    "case",
    [
        OutcomeCase(ReviewOutcome.DISABLED, True, True, TerminalOutcome.PASSED),
        OutcomeCase(ReviewOutcome.ACCEPTED, True, True, TerminalOutcome.PASSED),
        OutcomeCase(ReviewOutcome.ACCEPTED, False, False, TerminalOutcome.FAILED),
        OutcomeCase(ReviewOutcome.REJECTED, True, False, TerminalOutcome.FAILED),
        OutcomeCase(ReviewOutcome.REJECT_EXHAUSTED, True, True, TerminalOutcome.FAILED),
        OutcomeCase(
            ReviewOutcome.REJECT_EXHAUSTED,
            True,
            False,
            TerminalOutcome.PASSED_WITH_REVIEWS,
        ),
        OutcomeCase(ReviewOutcome.UNKNOWN, True, False, TerminalOutcome.FAILED),
        OutcomeCase(ReviewOutcome.UNKNOWN, True, True, TerminalOutcome.FAILED),
        OutcomeCase(ReviewOutcome.SESSION_ERROR, True, False, TerminalOutcome.FAILED),
        OutcomeCase(ReviewOutcome.SESSION_ERROR, True, True, TerminalOutcome.FAILED),
        OutcomeCase(
            ReviewOutcome.IMPROVEMENT_ERROR, True, False, TerminalOutcome.FAILED
        ),
        OutcomeCase(
            ReviewOutcome.IMPROVEMENT_ERROR, True, True, TerminalOutcome.FAILED
        ),
    ],
)
def test_active_executor_outcome_matrix(
    tmp_path: Path,
    case: OutcomeCase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    phase = PhaseDefinition(
        id="phase_5_validation",
        name="Validation",
        prompt_template="",
        output_schema={},
        type="loop",
        transitions={"on_success": "complete", "on_failure": "complete"},
    )
    executor = _active_executor(tmp_path, phase, case.fail_closed)
    globals_config = executor.workflow.globals
    assert globals_config is not None
    globals_config["review_gate_enabled"] = (
        case.review_outcome is not ReviewOutcome.DISABLED
    )
    monkeypatch.setattr(
        executor,
        "_execute_loop_phase",
        MagicMock(return_value=_loop_result(case)),
    )

    # When
    result = executor.execute({})

    # Then
    outcome = result["run_outcome"]
    assert outcome.terminal_outcome is case.expected
    assert outcome.review_outcome is case.review_outcome
    assert outcome.workflow_terminal == WorkflowTerminal("complete")
    assert outcome.terminal_anchor.phase_id == PhaseId("phase_5_validation")
    accepted = case.validation_succeeded and case.review_outcome in (
        ReviewOutcome.DISABLED,
        ReviewOutcome.ACCEPTED,
    )
    assert outcome.accepted_attempt_id == (
        "phase_5_validation-attempt-4" if accepted else None
    )
    assert executor.phase_results["phase_5_validation"]["status"] == (
        "failure" if case.expected is TerminalOutcome.FAILED else "success"
    )


def test_active_executor_maps_missing_review_gate_to_unknown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    case = OutcomeCase(ReviewOutcome.UNKNOWN, True, True, TerminalOutcome.FAILED)
    phase = PhaseDefinition(
        id="phase_5_validation",
        name="Validation",
        prompt_template="",
        output_schema={},
        type="loop",
        transitions={"on_success": "complete", "on_failure": "complete"},
    )
    executor = _active_executor(tmp_path, phase, case.fail_closed)
    malformed = _loop_result(case)
    malformed["review_gate"] = None
    malformed["review_outcome"] = None
    monkeypatch.setattr(
        executor,
        "_execute_loop_phase",
        MagicMock(return_value=malformed),
    )

    # When
    outcome = executor.execute({})["run_outcome"]

    # Then
    assert outcome.review_outcome is ReviewOutcome.UNKNOWN
    assert outcome.terminal_outcome is TerminalOutcome.FAILED


def test_active_executor_preserves_yaml_terminal_and_failure_anchor(
    tmp_path: Path,
) -> None:
    # Given
    phase = PhaseDefinition(
        id="phase_4_rule_migration",
        name="Rule migration",
        prompt_template="",
        output_schema={},
        type="unsupported",
        transitions={"on_failure": "yaml_terminal"},
    )
    executor = _active_executor(tmp_path, phase, True)

    # When
    outcome = executor.execute({})["run_outcome"]

    # Then
    assert outcome.terminal_outcome is TerminalOutcome.FAILED
    assert outcome.workflow_terminal == WorkflowTerminal("yaml_terminal")
    assert outcome.terminal_anchor.phase_id == PhaseId("phase_4_rule_migration")


def test_active_executor_rejects_empty_execution_as_pass(tmp_path: Path) -> None:
    # Given
    phase = PhaseDefinition("unused", "Unused", "", {})
    executor = _active_executor(tmp_path, phase, True)
    executor.workflow.phases = []

    # When
    outcome = executor.execute({})["run_outcome"]

    # Then
    assert outcome.terminal_outcome is TerminalOutcome.FAILED
    assert outcome.executed_phases == ()
    assert outcome.terminal_anchor.phase_id == PhaseId("workflow_start")


def test_phase5_natural_exhaustion_discards_stale_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    sub_workflow = SubWorkflowDefinition(
        id="repair_loop",
        max_iterations=1,
        stop_conditions=[],
        phases=[],
    )
    phase = PhaseDefinition(
        id="phase_5_validation",
        name="Validation",
        prompt_template="",
        output_schema={},
        type="loop",
        sub_workflow="repair_loop",
    )
    executor = _active_executor(tmp_path, phase, True)
    executor.workflow.sub_workflows = {"repair_loop": sub_workflow}
    monkeypatch.setattr(
        executor,
        "_run_sub_workflow",
        MagicMock(
            return_value={"status": "success", "step_outputs": {"script_exit_code": 0}}
        ),
    )

    # When
    result = executor._execute_loop_phase(phase, {}, {})

    # Then
    assert result["status"] == "failure"
