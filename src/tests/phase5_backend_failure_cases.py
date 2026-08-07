from __future__ import annotations

from pathlib import Path
import subprocess
from unittest.mock import MagicMock

import pytest

from core.artifact_store import ArtifactStore
from core.execution_backend import ContainerBackend
from core.phase5_attempt_receipt import BackendExecution, BackendKind
from core.types import ExecutionBackendConfig, PhaseDefinition, WorkflowDefinition
from core.workflow_executor import WorkflowExecutor


def test_container_setup_failure_never_allocates_fabricated_local_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a V3 container whose cached prior identity is now missing.
    config = ExecutionBackendConfig.from_dict(
        {
            "mode": "container",
            "source": "existing_container",
            "container_name": "missing-container",
            "container_workdir": "/workspace",
        }
    )
    backend = ContainerBackend(config)
    backend._container_id = "missing-container"
    backend._initialized = True
    backend._last_execution = BackendExecution(
        kind=BackendKind.CONTAINER,
        namespace="container:prior-container",
        host_cwd=str(tmp_path),
        backend_cwd="/workspace",
        runtime="docker",
        container_id="prior-container",
        container_retained=True,
    )
    backend.set_project_dir(str(tmp_path))
    monkeypatch.setattr(
        subprocess,
        "run",
        MagicMock(
            return_value=subprocess.CompletedProcess(
                args=["docker", "inspect"],
                returncode=1,
                stdout="",
                stderr="not found",
            )
        ),
    )
    workflow = WorkflowDefinition(
        name="task-18-container-failure",
        version="1.0",
        globals={"review_fail_closed": True, "review_gate_enabled": False},
        phases=[],
        terminals=["complete"],
    )
    store = ArtifactStore(str(tmp_path), "run-container-failure")
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
    phase = PhaseDefinition(
        id="run_entry_script",
        name="Run entry",
        prompt_template="",
        output_schema={},
        type="shell",
        on_failure="continue",
    )
    setattr(phase, "command", "${loop_vars.entry_script}")

    # When container setup fails before the validation process launches.
    _, output = executor._execute_shell_phase(
        phase,
        state={},
        context={},
        loop_vars={"entry_script": "python validate.py"},
        loop_state={},
    )

    # Then one reservation remains, with no second ID or false local receipt.
    shell_dir = Path(store.artifact_dir) / "shell_attempts"
    assert output["exit_code"] == 1
    assert len(tuple(shell_dir.glob("*.reserved"))) == 1
    assert tuple(shell_dir.glob("*.receipt.json")) == ()
    assert tuple(shell_dir.glob("*.meta.json")) == ()
    assert store.reserve_phase5_attempt().attempt_number == 2
