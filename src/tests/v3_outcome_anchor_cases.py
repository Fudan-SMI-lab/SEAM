from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.review_gate import ReviewGate
from core.run_outcome import (
    OutcomeContractError,
    PhaseId,
    ReviewVerdict,
    TerminalAnchor,
    TerminalOutcome,
)
from core.types import PhaseDefinition, WorkflowDefinition
from core.workflow_executor import WorkflowExecutor


def test_later_failure_keeps_phase5_attempt_and_anchors_later_phase(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    phase5 = PhaseDefinition(
        id="phase_5_validation",
        name="Validation",
        prompt_template="",
        output_schema={},
        type="loop",
        transitions={"on_success": "phase_6_report", "on_failure": "complete"},
    )
    phase6 = PhaseDefinition(
        id="phase_6_report",
        name="Report",
        prompt_template="",
        output_schema={},
        type="unsupported",
        transitions={"on_failure": "complete"},
    )
    workflow = WorkflowDefinition(
        name="active-v3-later-failure",
        version="1.0",
        globals={
            "review_gate_enabled": True,
            "review_fail_closed": True,
            "max_review_iterations": 3,
        },
        phases=[phase5, phase6],
        terminals=["complete"],
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
    gate = ReviewGate().record_judgment(ReviewVerdict.ACCEPT)
    monkeypatch.setattr(
        executor,
        "_execute_loop_phase",
        MagicMock(
            return_value={
                "status": "success",
                "loop_state": {
                    "script_exit_code": 0,
                    "latest_shell_attempt_artifacts": {"attempt": 2},
                },
                "review_gate": gate,
                "review_outcome": gate.outcome,
            },
        ),
    )

    # When
    outcome = executor.execute({})["run_outcome"]

    # Then
    assert outcome.terminal_outcome is TerminalOutcome.FAILED
    assert outcome.terminal_anchor.phase_id == PhaseId("phase_6_report")
    assert outcome.accepted_attempt_id == "phase_5_validation-attempt-2"


def test_terminal_anchor_rejects_missing_phase() -> None:
    # Given / When / Then
    with pytest.raises(OutcomeContractError, match="valid identifier"):
        _ = TerminalAnchor(phase_id=PhaseId(""))


def test_missing_transition_target_fails_at_unresolved_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    phase = PhaseDefinition(
        id="phase_5_validation",
        name="Validation",
        prompt_template="",
        output_schema={},
        type="loop",
        transitions={"on_success": "missing_target"},
    )
    workflow = WorkflowDefinition(
        name="missing-terminal-target",
        version="1.0",
        globals={"review_gate_enabled": True, "review_fail_closed": True},
        phases=[phase],
        terminals=["complete"],
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
    gate = ReviewGate(max_rounds=1).record_judgment(ReviewVerdict.ACCEPT)
    monkeypatch.setattr(
        executor,
        "_execute_loop_phase",
        MagicMock(
            return_value={
                "status": "success",
                "loop_state": {"script_exit_code": 0},
                "review_gate": gate,
            }
        ),
    )

    # When
    outcome = executor.execute({})["run_outcome"]

    # Then
    assert outcome.terminal_outcome is TerminalOutcome.FAILED
    assert outcome.workflow_terminal == "missing_target"
    assert outcome.terminal_anchor.phase_id == PhaseId("missing_target")
