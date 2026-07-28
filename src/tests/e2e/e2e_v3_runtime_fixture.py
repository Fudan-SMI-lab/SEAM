from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import pytest

import harness.server.lifecycle as server_lifecycle
import harness.session.manager as manager_module
import core.execution_backend as backend_module
import core.resource_manifest as resource_manifest_module
import harness.run.cleanup as cleanup_module
from core.review_policy import ReviewCliOverrides, ReviewIterationLimit
from core.resource_retention import ContainerRetention
from harness.session.opencode_contract import JsonObject
from harness.session.opencode_contract_json import load_json
from . import e2e_test_v3 as target

from .e2e_v3_runtime_fakes import ScriptedSessionManager, SessionScript
from .e2e_v3_runtime_backend import FakeContainerRuntime


@dataclass(frozen=True, slots=True)
class RuntimeScenario:
    run_hex: str
    review_responses: tuple[str, ...] = ()
    review_enabled: bool = False
    review_fail_closed: bool = True
    review_limit: int = 3
    save_agent_trace: bool | None = None
    session_script: SessionScript | None = None
    container_retention: ContainerRetention = ContainerRetention.RETAIN
    container_source: str | None = None
    container_runtime: FakeContainerRuntime | None = None
    server_cleanup_error: RuntimeError | None = None
    resource_seal_error: OSError | None = None


@dataclass(frozen=True, slots=True)
class RuntimeResult:
    exit_code: int
    report_dir: Path
    project_dir: Path
    manager: ScriptedSessionManager
    validation_count_path: Path


def run_runtime_scenario(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scenario: RuntimeScenario,
) -> RuntimeResult:
    scenario_root = tmp_path.parent / scenario.run_hex[:8]
    scenario_root.mkdir()
    report_root = scenario_root / "r"
    project_dir = scenario_root / "p"
    workflow_path = _write_workflow(scenario_root, scenario)
    manager: ScriptedSessionManager | None = None
    server_calls: list[str] = []

    def deterministic_mkdtemp(*, prefix: str) -> str:
        assert prefix == "migration-utils-e2e-v3-"
        project_dir.mkdir()
        return str(project_dir)

    def manager_factory(**kwargs) -> ScriptedSessionManager:
        nonlocal manager
        script = scenario.session_script or SessionScript(scenario.review_responses)
        manager = ScriptedSessionManager(script=script, **kwargs)
        return manager

    class FakeServerProcess:
        pid = 4242

    server_process = FakeServerProcess() if scenario.server_cleanup_error else None

    def resolve_server(*_args, **_kwargs):
        server_calls.append("resolve")
        return ("http://opencode.test", server_process)

    monkeypatch.setattr(target, "OUTPUT_ROOT", report_root)
    monkeypatch.setattr(target, "TEMPLATE_DIR", tmp_path / "missing-template")
    monkeypatch.setattr(target.tempfile, "mkdtemp", deterministic_mkdtemp)
    monkeypatch.setattr(target, "uuid4", lambda: UUID(hex=scenario.run_hex))
    monkeypatch.setattr(server_lifecycle, "resolve_server_url", resolve_server)
    monkeypatch.setattr(target, "check_server_running", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        target, "log_server_diagnostics", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(manager_module, "SessionManager", manager_factory)
    if scenario.container_runtime is not None:
        monkeypatch.setattr(
            backend_module.subprocess, "run", scenario.container_runtime
        )
    if scenario.server_cleanup_error is not None:
        cleanup_error = scenario.server_cleanup_error

        def fail_server_cleanup(_process) -> None:
            raise cleanup_error

        monkeypatch.setattr(cleanup_module, "stop_server", fail_server_cleanup)
    if scenario.resource_seal_error is not None:
        seal_error = scenario.resource_seal_error

        def fail_resource_seal(*_args, **_kwargs):
            raise seal_error

        monkeypatch.setattr(
            resource_manifest_module.ResourceManifestStore,
            "seal",
            fail_resource_seal,
        )

    exit_code = target.run_e2e_v3(
        base_url="http://opencode.test",
        max_phase5_iter=2,
        keep_temp_dir=True,
        agent_name=None,
        project_dir=None,
        server_auto_start=False,
        review_gate=scenario.review_enabled,
        review_policy_overrides=ReviewCliOverrides(
            max_iterations=ReviewIterationLimit(scenario.review_limit),
            fail_closed=scenario.review_fail_closed,
        ),
        workflow_path=workflow_path,
        opencode_readiness="off",
        container_retention=scenario.container_retention,
        save_agent_trace=scenario.save_agent_trace,
    )
    assert server_calls == ["resolve"]
    assert manager is not None
    run_id = f"e2e-v3-{scenario.run_hex[:12]}"
    return RuntimeResult(
        exit_code,
        report_root / run_id,
        project_dir,
        manager,
        scenario_root / "validation-count.txt",
    )


def read_json(path: Path) -> JsonObject:
    value = load_json(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _write_workflow(tmp_path: Path, scenario: RuntimeScenario) -> Path:
    validation = tmp_path / "v.py"
    count_path = tmp_path / "validation-count.txt"
    _ = validation.write_text(
        "from pathlib import Path\n"
        f"count_path = Path({str(count_path)!r})\n"
        "count = int(count_path.read_text()) if count_path.exists() else 0\n"
        "count_path.write_text(str(count + 1), encoding='utf-8')\n"
        "print('validated runtime')\n",
        encoding="utf-8",
    )
    command = json.dumps(f'"{sys.executable}" "{validation}"')
    backend = ""
    if scenario.container_source is not None:
        image = "  image: cpu:test\n" if scenario.container_source == "image" else ""
        name = (
            "  container_name: external-user\n"
            if scenario.container_source == "existing_container"
            else ""
        )
        backend = (
            "execution_backend:\n"
            "  mode: container\n"
            f"  source: {scenario.container_source}\n"
            "  runtime: docker\n"
            f"{image}{name}"
            "  container_workdir: /workspace/project\n"
            "  cleanup: true\n"
        )
    workflow = f"""\
name: task-24-runtime
version: '1.0'
{backend}globals:
  review_fail_closed: {str(scenario.review_fail_closed).lower()}
experience:
  enabled: false
phases:
  - id: phase_5_validation
    type: loop
    sub_workflow: repair_loop
    input_mapping:
      entry_script: {command}
      project_dir: "${{PROJECT_DIR}}"
    transitions:
      on_success: complete
      on_failure: failed
terminals: [complete, failed]
sub_workflows:
  repair_loop:
    id: repair_loop
    type: loop
    max_iterations: 2
    review_gate_enabled: {str(scenario.review_enabled).lower()}
    max_review_iterations: {scenario.review_limit}
    stop_conditions:
      - condition: "$.script_exit_code == 0 and $.review_gate_enabled == false"
        status: success
      - condition: "$.script_exit_code == 0 and $.review_verdict_status == 'accept'"
        status: success
    phases:
      - id: run_entry_script
        type: shell
        command: "${{loop_vars.entry_script}}"
        cwd: "${{loop_vars.project_dir}}"
        on_failure: continue
      - id: review_gate
        type: review
        condition: "$.script_exit_code == 0 and $.review_gate_enabled == true"
        agent: reviewer
        prompt_template: phase_5_review_container
    blocks:
      improvement_block:
        phases:
          - id: improvement_plan
            type: llm
            agent: error_analyzer
            prompt_template: phase_review_improvement_container
            output_as: improvement_plan
          - id: improvement_dispatch
            type: dispatch
            route_field: "${{improvement_plan.repair_role}}"
            routes:
              code_adapter: imp_fix_code
          - id: imp_fix_code
            type: llm
            agent: code_adapter
            prompt_template: repair_code_adapter_container
"""
    path = tmp_path / "w.yaml"
    _ = path.write_text(workflow, encoding="utf-8")
    return path
