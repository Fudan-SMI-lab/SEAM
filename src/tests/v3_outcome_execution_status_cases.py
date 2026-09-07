from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.review_gate import ReviewGate
from core.run_outcome import ReviewOutcome, ReviewVerdict, TerminalOutcome
from core.types import PhaseDefinition, WorkflowDefinition
from core.v3_phase5_runtime import (
    Phase5RuntimeConfig,
    RuntimeValue,
    phase5_decision_from_runtime,
)
from core.workflow_executor import WorkflowExecutor


def _phase5_executor(
    tmp_path: Path,
    *,
    review_gate_enabled: bool | None,
) -> WorkflowExecutor:
    phase = PhaseDefinition(
        id="phase_5_validation",
        name="Validation",
        prompt_template="",
        output_schema={},
        type="loop",
        transitions={"on_success": "complete", "on_failure": "complete"},
    )
    workflow = WorkflowDefinition(
        name="review-remediation",
        version="1.0",
        globals={},
        phases=[phase],
        terminals=["complete"],
    )
    if workflow.globals is not None:
        workflow.globals["review_fail_closed"] = True
        if review_gate_enabled is not None:
            workflow.globals["review_gate_enabled"] = review_gate_enabled
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


def test_phase5_parser_rejects_boolean_validation_and_attempt() -> None:
    # Given
    runtime_config = Phase5RuntimeConfig(
        review_enabled=False,
        review_fail_closed=True,
        max_review_rounds=3,
    )

    # When
    decision = phase5_decision_from_runtime(
        {
            "loop_state": {
                "script_exit_code": False,
                "latest_shell_attempt_artifacts": {"attempt": True},
            }
        },
        runtime_config,
    )

    # Then
    assert decision.validation_succeeded is False
    assert decision.accepted_attempt_id is None
    assert decision.parent_disposition.value == "failure"


def test_execute_cannot_promote_failed_phase5_with_stale_zero_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    executor = _phase5_executor(tmp_path, review_gate_enabled=False)
    monkeypatch.setattr(
        executor,
        "_execute_loop_phase",
        MagicMock(
            return_value={
                "status": "failure",
                "loop_state": {"script_exit_code": 0},
                "review_gate": None,
            }
        ),
    )

    # When
    result = executor.execute({})

    # Then
    assert result["run_outcome"].terminal_outcome is TerminalOutcome.FAILED
    assert executor.phase_results["phase_5_validation"]["status"] == "failure"


@pytest.mark.parametrize("status", [None, 0, "unexpected"])
def test_execute_rejects_malformed_phase5_execution_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: RuntimeValue,
) -> None:
    # Given
    executor = _phase5_executor(tmp_path, review_gate_enabled=False)
    monkeypatch.setattr(
        executor,
        "_execute_loop_phase",
        MagicMock(
            return_value={
                "status": status,
                "loop_state": {
                    "script_exit_code": 0,
                    "latest_shell_attempt_artifacts": {"attempt": 4},
                },
                "review_gate": None,
            }
        ),
    )

    # When
    outcome = executor.execute({})["run_outcome"]

    # Then
    assert outcome.terminal_outcome is TerminalOutcome.FAILED
    assert outcome.validation_succeeded is False
    assert outcome.accepted_attempt_id is None
    assert executor.phase_results["phase_5_validation"]["status"] == "failure"


def test_execute_uses_effective_runtime_review_gate_when_global_is_absent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    executor = _phase5_executor(tmp_path, review_gate_enabled=None)
    gate = ReviewGate(max_rounds=1).record_judgment(ReviewVerdict.REJECT)
    monkeypatch.setattr(
        executor,
        "_execute_loop_phase",
        MagicMock(
            return_value={
                "status": ReviewOutcome.REJECT_EXHAUSTED.value,
                "loop_state": {
                    "script_exit_code": 0,
                    "review_gate_enabled": True,
                },
                "review_gate": gate,
            }
        ),
    )

    # When
    result = executor.execute({})

    # Then
    assert result["run_outcome"].review_outcome is ReviewOutcome.REJECT_EXHAUSTED
    assert result["run_outcome"].terminal_outcome is TerminalOutcome.FAILED
    assert executor.phase_results["phase_5_validation"]["status"] == "failure"
