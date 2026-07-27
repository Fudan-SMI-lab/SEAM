from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from core.review_gate import ReviewGate
from core.run_outcome import ReviewOutcome
from core.types import PhaseDefinition, SubWorkflowDefinition, WorkflowDefinition
from core.workflow_executor import WorkflowExecutor


def test_review_state_resets_between_runs_on_same_executor(tmp_path: Path) -> None:
    # Given
    sub_workflow = SubWorkflowDefinition(
        id="repair_loop",
        max_iterations=2,
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
        name="review-isolation",
        version="1.0",
        phases=[],
        terminals=["complete"],
        sub_workflows={"repair_loop": sub_workflow},
    )
    session_manager = MagicMock()
    session_manager.get_or_create.return_value = "session"
    session_manager.send_command.side_effect = [
        '{"verdict": "reject", "reasoning": "first run"}',
        '{"repair_role": "code_adapter"}',
        '{"status": "fixed"}',
        '{"verdict": "accept", "reasoning": "first run accepted"}',
        '{"verdict": "reject", "reasoning": "second run"}',
        '{"repair_role": "code_adapter"}',
        '{"status": "fixed"}',
        '{"verdict": "accept", "reasoning": "second run accepted"}',
    ]
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
    first = executor._execute_loop_phase(phase, {}, {})
    second = executor._execute_loop_phase(phase, {}, {})

    # Then
    first_gate = first["review_gate"]
    second_gate = second["review_gate"]
    assert isinstance(first_gate, ReviewGate)
    assert isinstance(second_gate, ReviewGate)
    assert first_gate is not second_gate
    assert [review_round.round_number for review_round in first_gate.rounds] == [1, 2]
    assert [review_round.round_number for review_round in second_gate.rounds] == [1, 2]
    assert first_gate.outcome is ReviewOutcome.ACCEPTED
    assert second_gate.outcome is ReviewOutcome.ACCEPTED
    assert session_manager.send_command.call_count == 8
