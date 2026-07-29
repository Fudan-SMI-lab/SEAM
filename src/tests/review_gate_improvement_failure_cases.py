from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.review_gate import ReviewGate
from core.run_outcome import ReviewOutcome
from core.types import PhaseDefinition, SubWorkflowDefinition, WorkflowDefinition
from core.workflow_executor import WorkflowExecutor


@dataclass(frozen=True, slots=True)
class ImprovementFailureCase:
    responses: tuple[str, ...]
    command_count: int


@pytest.mark.parametrize(
    "case",
    [
        ImprovementFailureCase(
            responses=(
                '{"verdict": "reject", "reasoning": "needs improvement"}',
                '{"repair_role": "unknown"}',
            ),
            command_count=2,
        ),
        ImprovementFailureCase(
            responses=(
                '{"verdict": "reject", "reasoning": "needs improvement"}',
                '{"repair_role": "code_adapter"}',
                '{"ok": false, "error": "selected fixer failed"}',
            ),
            command_count=3,
        ),
    ],
)
def test_improvement_selection_failure_closes_rejected_round(
    case: ImprovementFailureCase,
    tmp_path: Path,
) -> None:
    # Given
    sub_workflow = SubWorkflowDefinition(
        id="repair_loop",
        max_iterations=4,
        stagnation_threshold=99,
        review_gate_enabled=True,
        max_review_iterations=3,
        stop_conditions=[],
        phases=[
            {
                "id": "review_gate",
                "type": "review",
                "prompt_template": "review",
                "agent": "main_engineer",
            }
        ],
        blocks={
            "improvement_block": {
                "phases": [
                    {
                        "id": "improvement_plan",
                        "type": "llm",
                        "prompt_template": "plan",
                        "agent": "error_analyzer",
                        "output_as": "improvement_plan",
                    },
                    {
                        "id": "improvement_dispatch",
                        "type": "dispatch",
                        "route_field": "${improvement_plan.repair_role}",
                        "routes": {"code_adapter": "imp_fix_code"},
                    },
                    {
                        "id": "imp_fix_code",
                        "type": "llm",
                        "prompt_template": "fix",
                        "agent": "code_adapter",
                    },
                ]
            }
        },
    )
    workflow = WorkflowDefinition(
        name="improvement-failure",
        version="1.0",
        globals={"review_gate_enabled": True, "review_fail_closed": True},
        phases=[],
        terminals=["complete"],
        sub_workflows={"repair_loop": sub_workflow},
    )
    session_manager = MagicMock()
    session_manager.get_or_create.return_value = "session"
    session_manager.send_command.side_effect = list(case.responses)
    prompt_loader = MagicMock()
    prompt_loader.load_prompt.return_value = "prompt"
    artifact_store = MagicMock()
    artifact_store.artifact_dir = str(tmp_path / "artifacts")
    artifact_store.raw_dir = str(tmp_path / "raw")
    executor = WorkflowExecutor(
        workflow,
        session_manager,
        artifact_store,
        prompt_loader,
        MagicMock(),
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )
    phase = PhaseDefinition(
        id="phase_5_validation",
        name="Phase 5",
        prompt_template="",
        output_schema={},
        type="loop",
        sub_workflow="repair_loop",
    )

    # When
    result = executor._execute_loop_phase(phase, {}, {})

    # Then
    gate = result["review_gate"]
    assert isinstance(gate, ReviewGate)
    assert len(gate.rounds) == 1
    assert gate.outcome is ReviewOutcome.IMPROVEMENT_ERROR
    assert result["status"] == ReviewOutcome.IMPROVEMENT_ERROR.value
    assert session_manager.send_command.call_count == case.command_count
