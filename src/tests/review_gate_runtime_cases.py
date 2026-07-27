from __future__ import annotations

from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import MagicMock

import pytest

from core.review_gate import ReviewGate
from core.types import PhaseDefinition, SubWorkflowDefinition, WorkflowDefinition
from core.workflow_executor import WorkflowExecutor
from tests.test_repair_loop import build_mocked_engine


def test_legacy_repair_loop_preserves_passed_with_reviews_on_reject_exhaustion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    engine, _session_manager, _store, _loader, _validator = build_mocked_engine()
    (tmp_path / "entry.py").write_text("print('ok')\n", encoding="utf-8")
    monkeypatch.setattr(
        "subprocess.run",
        lambda *_args, **_kwargs: CompletedProcess(
            args="python entry.py",
            returncode=0,
            stdout="ok",
            stderr="",
        ),
    )
    monkeypatch.setattr(
        engine,
        "_run_improvement_iteration",
        lambda **_kwargs: {"status": "success", "repair_role": "code_adapter"},
    )

    reject_review = MagicMock(
        return_value={"verdict": "reject", "reasoning": "legacy rejection"}
    )

    # When
    result = engine.run(
        entry_script="python entry.py",
        project_dir=str(tmp_path),
        max_iterations=2,
        review_callable=reject_review,
        enable_review_gate=True,
        max_review_iterations=1,
    )

    # Then
    assert result["success"] is True
    assert result["status"] == "passed_with_reviews"
    assert result["iteration_count"] == 1
    reject_review.assert_called_once()


def test_active_v3_review_exhaustion_stops_before_fourth_judgment(
    tmp_path: Path,
) -> None:
    # Given
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
            "prompt_template": "fix",
            "agent": "code_adapter",
        },
    ]
    sub_workflow = SubWorkflowDefinition(
        id="repair_loop",
        max_iterations=5,
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
        blocks={"improvement_block": {"phases": improvement_phases}},
    )
    workflow = WorkflowDefinition(
        name="logical-review-rounds",
        version="1.0",
        phases=[],
        terminals=["complete"],
        sub_workflows={"repair_loop": sub_workflow},
    )
    session_manager = MagicMock()
    session_manager.get_or_create.return_value = "session"
    session_manager.send_command.side_effect = [
        '{"verdict": "reject", "reasoning": "round one"}',
        '{"repair_role": "code_adapter"}',
        '{"status": "fixed"}',
        '{"verdict": "reject", "reasoning": "round two"}',
        '{"repair_role": "code_adapter"}',
        '{"status": "fixed"}',
        '{"verdict": "reject", "reasoning": "round three"}',
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
    result = executor._execute_loop_phase(phase, {}, {})

    # Then
    assert result["status"] == "reject_exhausted"
    gate = result["review_gate"]
    assert isinstance(gate, ReviewGate)
    assert len(gate.rounds) == 3
    assert session_manager.send_command.call_count == 7
