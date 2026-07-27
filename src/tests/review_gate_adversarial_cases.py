from __future__ import annotations

from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import MagicMock

import pytest

from core.review_gate import ReviewGate
from core.run_outcome import ReviewOutcome
from core.types import PhaseDefinition, SubWorkflowDefinition, WorkflowDefinition
from core.workflow_executor import WorkflowExecutor


def test_post_improvement_validation_failure_consumes_one_repair_cycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    phases = [
        {
            "id": "run_entry_script",
            "type": "shell",
            "command": "python entry.py",
            "capture": {
                "exit_code": "script_exit_code",
                "stdout": "script_stdout",
                "stderr": "script_stderr",
            },
            "on_failure": "continue",
        },
        {
            "id": "analyze_error",
            "type": "llm",
            "condition": "$.script_exit_code != 0",
            "prompt_template": "analyze",
            "agent": "error_analyzer",
            "output_as": "error_analysis",
        },
        {
            "id": "repair_dispatch",
            "type": "dispatch",
            "condition": "$.script_exit_code != 0",
            "route_field": "${error_analysis.repair_role}",
            "routes": {"code_adapter": "fix_code"},
        },
        {
            "id": "fix_code",
            "type": "llm",
            "condition": "$.script_exit_code != 0",
            "prompt_template": "fix",
            "agent": "code_adapter",
        },
        {
            "id": "review_gate",
            "type": "review",
            "condition": "$.script_exit_code == 0 and $.review_gate_enabled == true",
            "prompt_template": "review",
            "agent": "main_engineer",
        },
    ]
    improvement_phases = [
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
            "prompt_template": "improve",
            "agent": "code_adapter",
        },
    ]
    sub_workflow = SubWorkflowDefinition(
        id="repair_loop",
        max_iterations=2,
        stagnation_threshold=99,
        review_gate_enabled=True,
        max_review_iterations=3,
        stop_conditions=[
            {
                "condition": (
                    "$.script_exit_code == 0 and $.review_verdict_status == 'accept'"
                ),
                "status": "success",
            }
        ],
        phases=phases,
        blocks={"improvement_block": {"phases": improvement_phases}},
    )
    workflow = WorkflowDefinition(
        name="review-revalidation",
        version="1.0",
        phases=[],
        terminals=["complete"],
        sub_workflows={"repair_loop": sub_workflow},
    )
    session_manager = MagicMock()
    session_manager.get_or_create.return_value = "session"
    session_manager.send_command.side_effect = [
        '{"verdict": "reject", "reasoning": "improve accelerator use"}',
        '{"repair_role": "code_adapter"}',
        '{"status": "improved"}',
        '{"repair_role": "code_adapter"}',
        '{"status": "fixed"}',
        '{"verdict": "accept", "reasoning": "evidence is complete"}',
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
    shell_run = MagicMock(
        side_effect=[
            CompletedProcess("entry", 0, "ok", ""),
            CompletedProcess("entry", 1, "", "post-improvement failure"),
            CompletedProcess("entry", 0, "ok", ""),
        ]
    )
    monkeypatch.setattr("core.workflow_executor.subprocess.run", shell_run)
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
    assert [review_round.outcome for review_round in gate.rounds] == [
        ReviewOutcome.REJECTED,
        ReviewOutcome.ACCEPTED,
    ]
    assert result["status"] == "success"
    assert result["iterations"] == 2
    assert shell_run.call_count == 3
    assert session_manager.send_command.call_count == 6
    assert result["loop_state"]["review_improvement"] == {
        "selected_phase": "imp_fix_code",
        "status": "success",
    }
    assert "fix_code" in result["loop_state"]
