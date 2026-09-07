from __future__ import annotations

from pathlib import Path
from subprocess import CompletedProcess
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from core.artifact_store import ArtifactStore
from core.config import load_workflow
from core.run_outcome import ReviewOutcome, TerminalOutcome
from core.workflow_executor import WorkflowExecutor


def test_deepwave_repeated_rejects_and_swallowed_fixer_fail_explicitly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    workflow_path = (
        Path(__file__).resolve().parent.parent / "workflows" / "npu_ascend_general.yaml"
    )
    workflow = load_workflow(str(workflow_path))
    phase = next(
        candidate
        for candidate in workflow.phases
        if candidate.id == "phase_5_validation"
    )
    workflow.phases = [phase]
    workflow.globals = {
        **(workflow.globals or {}),
        "review_gate_enabled": True,
        "review_fail_closed": True,
        "max_review_iterations": 3,
        "max_repair_iterations": 1,
    }
    session_manager = MagicMock()
    session_manager.get_or_create.side_effect = lambda role, lifecycle: (
        f"session:{role}:{lifecycle}"
    )
    session_manager.send_command.side_effect = [
        '{"verdict": "reject", "reasoning": "round one"}',
        '{"repair_role": "operator_fixer"}',
        '{"fixed": false, "summary": "fixer swallowed the failure"}',
        '{"repair_role": "code_adapter"}',
        '{"fixed": true, "summary": "validation repair"}',
        '{"verdict": "reject", "reasoning": "round two"}',
        '{"repair_role": "operator_fixer"}',
        '{"fixed": false, "summary": "still swallowed"}',
        '{"verdict": "reject", "reasoning": "round three"}',
    ]
    prompt_loader = MagicMock()
    prompt_loader.load_prompt.return_value = "prompt"
    validator = MagicMock()
    validator.validate.return_value = SimpleNamespace(passed=True, errors=[])
    executor = WorkflowExecutor(
        workflow,
        session_manager,
        ArtifactStore(str(tmp_path), "deepwave-task-8"),
        prompt_loader,
        validator,
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
        exec_backend=False,
    )
    executor.hook_manager = MagicMock()
    shell_run = MagicMock(
        side_effect=[
            CompletedProcess("entry", 0, "ok", ""),
            CompletedProcess("entry", 1, "", "post-improvement failure"),
            CompletedProcess("entry", 0, "ok", ""),
            CompletedProcess("entry", 0, "ok", ""),
        ]
    )
    monkeypatch.setattr("core.workflow_executor.subprocess.run", shell_run)

    # When
    result = executor.execute(
        {
            "PROJECT_DIR": str(tmp_path),
            "phase_3_entry_script": {"run_command": "python entry.py"},
        }
    )

    # Then
    outcome = result["run_outcome"]
    assert outcome.terminal_outcome is TerminalOutcome.FAILED
    assert outcome.review_outcome is ReviewOutcome.REJECT_EXHAUSTED
    assert executor.phase_results["phase_5_validation"]["status"] == "failure"
    assert shell_run.call_count == 4
    called_sessions = [
        call.args[0] for call in session_manager.send_command.call_args_list
    ]
    assert sum("operator_fixer" in session for session in called_sessions) == 2
    assert all("dependency_fixer" not in session for session in called_sessions)
    assert all("final_gate_report_fixer" not in session for session in called_sessions)
