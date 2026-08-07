from __future__ import annotations

import json
from pathlib import Path
import sys
from unittest.mock import MagicMock

from core.artifact_store import ArtifactStore
from core.phase5_attempt_receipt import load_attempt_receipt
from core.types import PhaseDefinition, SubWorkflowDefinition, WorkflowDefinition
from core.workflow_executor import WorkflowExecutor


def test_final_budget_validation_only_rerun_is_the_accepted_receipt(
    tmp_path: Path,
) -> None:
    # Given one final repair iteration whose fixer makes only the bonus rerun pass.
    flag_path = tmp_path / "fixed.flag"
    python = sys.executable.replace(chr(92), "/")
    command = (
        f'{python} -c "from pathlib import Path; '
        f"raise SystemExit(0 if Path(r'{flag_path}').exists() else 9)\""
    )
    sub_workflow = SubWorkflowDefinition(
        id="repair_loop",
        type="loop",
        max_iterations=1,
        stop_conditions=[{"condition": "$.script_exit_code == 0", "status": "success"}],
        phases=[
            {
                "id": "run_entry_script",
                "type": "shell",
                "command": "${loop_vars.entry_script}",
                "on_failure": "continue",
            },
            {
                "id": "analyze_error",
                "type": "llm",
                "condition": "$.script_exit_code != 0",
                "prompt_template": "analyze_prompt",
                "agent": "error_analyzer",
                "output_as": "error_analysis",
            },
            {
                "id": "repair_dispatch",
                "type": "dispatch",
                "condition": "$.script_exit_code != 0",
                "route_field": "${error_analysis.repair_role}",
                "routes": {"dependency_fixer": "fix_dependency"},
            },
            {
                "id": "fix_dependency",
                "condition": "$.script_exit_code != 0",
                "type": "llm",
                "prompt_template": "fix_dependency_prompt",
                "agent": "dependency_fixer",
            },
        ],
    )
    phase5 = PhaseDefinition(
        id="phase_5_validation",
        name="Validation",
        prompt_template="",
        output_schema={},
        type="loop",
        sub_workflow="repair_loop",
        input_mapping={"entry_script": command},
        transitions={"on_success": "complete", "on_failure": "complete"},
    )
    workflow = WorkflowDefinition(
        name="task-18-final-budget",
        version="1.0",
        globals={"review_gate_enabled": False, "review_fail_closed": True},
        phases=[phase5],
        terminals=["complete"],
        agents={
            "error_analyzer": {"role": "error_analyzer", "lifecycle": "persistent"},
            "dependency_fixer": {"role": "dependency_fixer", "lifecycle": "persistent"},
        },
        sub_workflows={"repair_loop": sub_workflow},
    )
    session_manager = MagicMock()
    session_manager.get_or_create.side_effect = lambda role, lifecycle: (
        f"session:{role}"
    )

    def respond(session_id: str, _prompt: str, timeout: int = 600) -> str:
        if session_id == "session:error_analyzer":
            return json.dumps(
                {
                    "repair_role": "dependency_fixer",
                    "category": "dependency",
                    "root_cause": "missing flag",
                    "suggested_fix": "create flag",
                }
            )
        _ = flag_path.write_text("fixed", encoding="utf-8")
        return json.dumps(
            {
                "fixed": True,
                "summary": "created flag",
                "modified_files": [str(flag_path)],
            }
        )

    session_manager.send_command.side_effect = respond
    prompt_loader = MagicMock()
    prompt_loader.load_prompt.side_effect = lambda template, context: template
    store = ArtifactStore(str(tmp_path), "run-final-budget")
    executor = WorkflowExecutor(
        workflow,
        session_manager,
        store,
        prompt_loader,
        MagicMock(),
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )
    executor.hook_manager = MagicMock()

    # When active V3 executes the final-budget repair and validation-only rerun.
    result = executor.execute({})

    # Then the successful bonus execution itself is complete and accepted.
    assert result["run_outcome"].accepted_attempt_id == ("phase_5_validation-attempt-2")
    receipt_dir = Path(store.artifact_dir) / "shell_attempts"
    first = load_attempt_receipt(
        receipt_dir / "run_entry_script_attempt0001.receipt.json"
    )
    second = load_attempt_receipt(
        receipt_dir / "run_entry_script_attempt0002.receipt.json"
    )
    assert first.shell_exit_code == 9
    assert first.accepted is False
    assert second.shell_exit_code == 0
    assert second.complete is True
    assert second.accepted is True
    assert session_manager.send_command.call_count == 2
