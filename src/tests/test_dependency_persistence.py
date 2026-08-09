"""RED contract tests for bug #14 Gap B: replayable dependency persistence.

Bug #14 Gap B: phase-2 ``installed_packages`` is consumed (accelerator_context
L70-109, orchestrator L693, workflow_executor L2389) but NEVER persisted as a
replayable manifest, so a recreated execution environment loses all recorded
dependencies and ``run_entry_script`` has no dependency-reinstall pre-step.

These tests document the intended contract and FAIL on current code (that IS
the RED). T7 implements the persistence mechanism and flips them green.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.config import load_workflow
from core.execution_backend import ContainerBackend
from core.types import ExecutionBackendConfig, WorkflowDefinition
from core.workflow_executor import WorkflowExecutor


def _build_executor(
    tmp_path: Path, exec_backend: ContainerBackend | None = None
) -> WorkflowExecutor:
    workflow = WorkflowDefinition(
        name="dependency-persistence-contract",
        version="1.0",
        phases=[],
        terminals=["complete"],
    )
    return WorkflowExecutor(
        workflow,
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
        exec_backend=exec_backend,
    )


def test_phase2_installed_packages_snapshot_is_replayable(tmp_path: Path) -> None:
    """Contract: phase-2 installed_packages must be persisted as a replayable
    manifest. The persist/replay API does not exist yet, so this must FAIL."""
    from core import execution_env_records

    installed_packages = ["torch==2.0.1", "torch-npu==2.1.0", "vllm==0.18.0"]

    persist = getattr(execution_env_records, "persist_dependency_plan", None)
    assert persist is not None, (
        "execution_env_records.persist_dependency_plan is missing: phase-2 "
        "installed_packages snapshot cannot be persisted as a replayable manifest"
    )
    replay = getattr(execution_env_records, "replay_dependency_plan", None)
    assert replay is not None, (
        "execution_env_records.replay_dependency_plan is missing: persisted "
        "dependency plan cannot be replayed"
    )
    replayed = replay(persist(installed_packages))
    assert list(replayed) == installed_packages


def test_env_recreation_does_not_lose_recorded_dependencies(tmp_path: Path) -> None:
    """Contract: _maybe_recreate_execution_environment must not lose the phase-2
    recorded dependencies — after recreation the dependency plan must be
    replayed from the persisted manifest. Nothing is recorded today, so this
    must FAIL."""
    backend = ContainerBackend(
        ExecutionBackendConfig.from_dict(
            {"mode": "container", "source": "image", "image": "test:latest"}
        )
    )
    backend.recreate_execution_environment = MagicMock(
        return_value={"old_container_id": "old", "new_container_id": "new"}
    )
    backend.probe_environment = MagicMock(return_value={"status": "ok"})
    executor = _build_executor(tmp_path, exec_backend=backend)

    installed_packages = ["torch==2.0.1", "torch-npu==2.1.0"]
    state = {"phase_2_venv_create": {"installed_packages": installed_packages}}
    executor.state = state
    loop_state = {
        "iteration": 0,
        "environment_reset_count": 0,
        "max_environment_resets": 2,
        "environment_reset_requests": [],
    }

    result = executor._maybe_recreate_execution_environment(
        {
            "environment_action": {
                "needed": True,
                "action": "recreate_execution_environment",
                "reason": "missing torch_npu",
                "scope": "execution_environment",
            }
        },
        {},
        loop_state,
    )

    assert result is not None
    assert result["applied"] is True
    assert "dependency_plan" in loop_state, (
        "execution environment was recreated without persisting a replayable "
        "dependency plan; phase-2 recorded dependencies are lost"
    )
    assert loop_state["dependency_plan"] == installed_packages


def test_run_entry_script_has_dependency_reinstall_pre_step() -> None:
    """Contract: ppu_vllm repair_loop must reinstall recorded dependencies
    before run_entry_script. Currently run_entry_script is the first phase
    (ppu_vllm.yaml L230-240) and no such pre-step exists, so this must FAIL."""
    workflow_path = (
        Path(__file__).resolve().parent.parent / "workflows" / "ppu_vllm.yaml"
    )
    workflow = load_workflow(str(workflow_path))
    repair_phases = workflow.sub_workflows["repair_loop"].phases

    phase_ids = [
        phase.get("id") for phase in repair_phases if isinstance(phase, dict)
    ]
    assert "run_entry_script" in phase_ids
    run_index = phase_ids.index("run_entry_script")
    assert any(
        isinstance(phase, dict)
        and any(
            token in str(phase.get("id", "")).lower()
            for token in ("reinstall", "restore_deps", "dependency_reinstall")
        )
        for phase in repair_phases[:run_index]
    ), (
        "run_entry_script has no dependency-reinstall pre-step; recorded phase-2 "
        "dependencies are never reinstalled into a recreated environment"
    )
