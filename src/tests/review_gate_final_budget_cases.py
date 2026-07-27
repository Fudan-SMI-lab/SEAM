from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from subprocess import CompletedProcess
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from core.artifact_store import ArtifactStore
from core.config import load_workflow
from core.review_gate import ReviewGate
from core.run_outcome import ReviewOutcome
from core.types import PhaseDefinition
from core.workflow_executor import WorkflowExecutor


@dataclass(frozen=True, slots=True)
class ActiveFinalBudgetHarness:
    executor: WorkflowExecutor
    phase: PhaseDefinition
    state: dict[str, dict[str, str]]
    context: dict[str, str]
    session_manager: MagicMock
    shell_run: MagicMock


def _active_final_budget_harness(
    tmp_path: Path,
    responses: tuple[str | TimeoutError, ...],
    shell_exit_codes: tuple[int, ...],
) -> ActiveFinalBudgetHarness:
    workflow_path = (
        Path(__file__).resolve().parent.parent / "workflows" / "npu_ascend_general.yaml"
    )
    workflow = load_workflow(str(workflow_path))
    workflow.globals = {
        **(workflow.globals or {}),
        "review_gate_enabled": True,
        "max_repair_iterations": 1,
    }
    session_manager = MagicMock()
    session_manager.get_or_create.side_effect = lambda role, lifecycle: (
        f"session:{role}:{lifecycle}"
    )
    session_manager.send_command.side_effect = list(responses)
    prompt_loader = MagicMock()
    prompt_loader.load_prompt.return_value = "prompt"
    validator = MagicMock()
    validator.validate.return_value = SimpleNamespace(passed=True, errors=[])
    artifact_store = ArtifactStore(str(tmp_path), "active-final-budget")
    executor = WorkflowExecutor(
        workflow,
        session_manager,
        artifact_store,
        prompt_loader,
        validator,
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
        exec_backend=False,
    )
    shell_run = MagicMock(
        side_effect=[
            CompletedProcess(
                args="entry",
                returncode=exit_code,
                stdout="ok" if exit_code == 0 else "",
                stderr="" if exit_code == 0 else "post-improvement failure",
            )
            for exit_code in shell_exit_codes
        ]
    )
    phase = next(
        workflow_phase
        for workflow_phase in workflow.phases
        if workflow_phase.id == "phase_5_validation"
    )
    return ActiveFinalBudgetHarness(
        executor=executor,
        phase=phase,
        state={"phase_3_entry_script": {"run_command": "python entry.py"}},
        context={"PROJECT_DIR": str(tmp_path)},
        session_manager=session_manager,
        shell_run=shell_run,
    )


def test_active_final_budget_bonus_cannot_return_success_with_rejected_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    harness = _active_final_budget_harness(
        tmp_path,
        responses=(
            '{"verdict": "reject", "reasoning": "round one"}',
            '{"repair_role": "code_adapter"}',
            '{"fixed": true, "modified_files": ["entry.py"], "summary": "improved"}',
            '{"repair_role": "code_adapter"}',
            '{"fixed": true, "modified_files": ["entry.py"], "summary": "fixed"}',
            TimeoutError("resumed review timed out"),
        ),
        shell_exit_codes=(0, 1, 0),
    )
    monkeypatch.setattr("core.workflow_executor.subprocess.run", harness.shell_run)

    # When
    result = harness.executor._execute_loop_phase(
        harness.phase,
        harness.state,
        harness.context,
    )

    # Then
    gate = result["review_gate"]
    assert isinstance(gate, ReviewGate)
    observed = (
        result["status"],
        gate.outcome,
        len(gate.rounds),
        harness.shell_run.call_count,
        harness.session_manager.send_command.call_count,
    )
    assert observed == (
        "failure",
        ReviewOutcome.REJECTED,
        1,
        3,
        6,
    ), observed


def test_active_final_budget_resumes_same_gate_until_explicit_accept(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    harness = _active_final_budget_harness(
        tmp_path,
        responses=(
            '{"verdict": "reject", "reasoning": "round one"}',
            '{"repair_role": "code_adapter"}',
            '{"fixed": true, "modified_files": ["entry.py"], "summary": "improved"}',
            '{"repair_role": "code_adapter"}',
            '{"fixed": true, "modified_files": ["entry.py"], "summary": "fixed"}',
            '{"verdict": "accept", "reasoning": "round two accepted"}',
        ),
        shell_exit_codes=(0, 1, 0),
    )
    monkeypatch.setattr("core.workflow_executor.subprocess.run", harness.shell_run)

    # When
    result = harness.executor._execute_loop_phase(
        harness.phase,
        harness.state,
        harness.context,
    )

    # Then
    gate = result["review_gate"]
    assert isinstance(gate, ReviewGate)
    assert [review_round.round_number for review_round in gate.rounds] == [1, 2]
    assert [review_round.outcome for review_round in gate.rounds] == [
        ReviewOutcome.REJECTED,
        ReviewOutcome.ACCEPTED,
    ]
    assert result["status"] == "success"
    assert result["iterations"] == 1
    assert harness.shell_run.call_count == 3
    assert harness.session_manager.send_command.call_count == 6


def test_active_final_budget_repeated_rejects_exhaust_without_fourth_review(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    harness = _active_final_budget_harness(
        tmp_path,
        responses=(
            '{"verdict": "reject", "reasoning": "round one"}',
            '{"repair_role": "code_adapter"}',
            '{"fixed": true, "modified_files": ["entry.py"], "summary": "improved one"}',
            '{"repair_role": "code_adapter"}',
            '{"fixed": true, "modified_files": ["entry.py"], "summary": "fixed"}',
            '{"verdict": "reject", "reasoning": "round two"}',
            '{"repair_role": "code_adapter"}',
            '{"fixed": true, "modified_files": ["entry.py"], "summary": "improved two"}',
            '{"verdict": "reject", "reasoning": "round three"}',
        ),
        shell_exit_codes=(0, 1, 0, 0),
    )
    monkeypatch.setattr("core.workflow_executor.subprocess.run", harness.shell_run)

    # When
    result = harness.executor._execute_loop_phase(
        harness.phase,
        harness.state,
        harness.context,
    )

    # Then
    gate = result["review_gate"]
    assert isinstance(gate, ReviewGate)
    assert [review_round.round_number for review_round in gate.rounds] == [1, 2, 3]
    assert gate.outcome is ReviewOutcome.REJECT_EXHAUSTED
    assert result["status"] == ReviewOutcome.REJECT_EXHAUSTED.value
    assert result["iterations"] == 1
    assert harness.shell_run.call_count == 4
    assert harness.session_manager.send_command.call_count == 9
