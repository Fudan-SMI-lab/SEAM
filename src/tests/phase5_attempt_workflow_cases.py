from __future__ import annotations

from pathlib import Path
import subprocess
import sys
from unittest.mock import MagicMock

import pytest

from core.artifact_store import ArtifactStore
from core.execution_backend import ContainerBackend
from core.phase5_attempt_receipt import load_attempt_receipt
from core.types import (
    ExecutionBackendConfig,
    PhaseDefinition,
    SubWorkflowDefinition,
    WorkflowDefinition,
)
from core.workflow_executor import WorkflowExecutor


def test_real_validation_rerun_reserves_before_launch_and_accepts_actual_attempt(
    tmp_path: Path,
) -> None:
    # Given a real validation command that fails once and succeeds on its rerun.
    store = ArtifactStore(str(tmp_path), "run-real")
    shell_dir = Path(store.artifact_dir) / "shell_attempts"
    marker = tmp_path / "validation-ran.marker"
    script = tmp_path / "validate_once.py"
    _ = script.write_text(
        "from pathlib import Path\n"
        f"marker = Path({str(marker)!r})\n"
        f"shell_dir = Path({str(shell_dir)!r})\n"
        "if not any(shell_dir.glob('.phase_5_validation-attempt-*.reserved')):\n"
        "    raise SystemExit(88)\n"
        "if marker.exists():\n"
        "    raise SystemExit(0)\n"
        "marker.write_text('first failed', encoding='utf-8')\n"
        "raise SystemExit(9)\n",
        encoding="utf-8",
    )
    command = f'"{sys.executable}" "{script}"'
    run_phase = {
        "id": "run_entry_script",
        "type": "shell",
        "command": "${loop_vars.entry_script}",
        "on_failure": "continue",
    }
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
        name="task-18-real-rerun",
        version="1.0",
        globals={"review_gate_enabled": False, "review_fail_closed": True},
        phases=[phase5],
        terminals=["complete"],
        sub_workflows={
            "repair_loop": SubWorkflowDefinition(
                id="repair_loop",
                type="loop",
                max_iterations=2,
                stop_conditions=[
                    {"condition": "$.script_exit_code == 0", "status": "success"}
                ],
                phases=[run_phase],
            )
        },
    )
    executor = WorkflowExecutor(
        workflow,
        MagicMock(),
        store,
        MagicMock(),
        MagicMock(),
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
    )
    executor.hook_manager = MagicMock()

    # When the active V3 workflow executes Phase 5.
    result = executor.execute({})

    # Then both launches had identities and only the successful rerun is accepted.
    outcome = result["run_outcome"]
    assert outcome.accepted_attempt_id == "phase_5_validation-attempt-2"
    receipts = Path(store.artifact_dir) / "shell_attempts"
    first = load_attempt_receipt(receipts / "run_entry_script_attempt0001.receipt.json")
    second = load_attempt_receipt(
        receipts / "run_entry_script_attempt0002.receipt.json"
    )
    assert first.accepted is False
    assert second.accepted is True


def test_fake_retained_container_receipt_captures_same_call_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a retained container backend and one successful validation command.
    config = ExecutionBackendConfig.from_dict(
        {
            "mode": "container",
            "source": "existing_container",
            "container_name": "cid-123",
            "container_workdir": "/workspace/project",
            "runtime": "docker",
            "cleanup": False,
        }
    )
    backend = ContainerBackend(config)
    backend.set_project_dir(str(tmp_path))
    fake_run = MagicMock(
        side_effect=[
            subprocess.CompletedProcess(
                args=["docker", "inspect"],
                returncode=0,
                stdout="running|immutable-cid-123\n",
                stderr="",
            ),
            subprocess.CompletedProcess(
                args=["docker", "exec"], returncode=0, stdout="ok\n", stderr=""
            ),
        ]
    )
    monkeypatch.setattr(subprocess, "run", fake_run)
    run_phase = {
        "id": "run_entry_script",
        "type": "shell",
        "command": "${loop_vars.entry_script}",
        "on_failure": "continue",
    }
    phase5 = PhaseDefinition(
        id="phase_5_validation",
        name="Validation",
        prompt_template="",
        output_schema={},
        type="loop",
        sub_workflow="repair_loop",
        input_mapping={"entry_script": "python validate.py"},
        transitions={"on_success": "complete", "on_failure": "complete"},
    )
    workflow = WorkflowDefinition(
        name="task-18-container",
        version="1.0",
        globals={"review_gate_enabled": False, "review_fail_closed": True},
        phases=[phase5],
        terminals=["complete"],
        sub_workflows={
            "repair_loop": SubWorkflowDefinition(
                id="repair_loop",
                type="loop",
                max_iterations=1,
                stop_conditions=[
                    {"condition": "$.script_exit_code == 0", "status": "success"}
                ],
                phases=[run_phase],
            )
        },
    )
    store = ArtifactStore(str(tmp_path), "run-container")
    executor = WorkflowExecutor(
        workflow,
        MagicMock(),
        store,
        MagicMock(),
        MagicMock(),
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
        exec_backend=backend,
    )
    executor.hook_manager = MagicMock()

    # When the fake container executes through the real backend adapter.
    result = executor.execute({})

    # Then the accepted receipt contains exact same-call container facts.
    assert result["run_outcome"].accepted_attempt_id == ("phase_5_validation-attempt-1")
    receipt = load_attempt_receipt(
        Path(store.artifact_dir)
        / "shell_attempts"
        / "run_entry_script_attempt0001.receipt.json"
    )
    assert receipt.accepted is True
    assert receipt.invocation.argv == ("python", "validate.py")
    assert receipt.backend.namespace == "container:immutable-cid-123"
    assert receipt.backend.runtime == "docker"
    assert receipt.backend.container_id == "immutable-cid-123"
    assert receipt.backend.backend_cwd == "/workspace/project"
    assert receipt.backend.container_retained is True
