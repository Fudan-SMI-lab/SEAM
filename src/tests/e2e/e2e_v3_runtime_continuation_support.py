from __future__ import annotations

import json
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

import harness.server.lifecycle as server_lifecycle
import harness.session.manager as manager_module
from core.continuation_environment_models import RetainedEnvironmentProbeRequest
from core.continuation_environment_probe import probe_retained_environment
from core.execution_env_context import (
    EnvironmentProbe,
    EnvironmentProbeRequest,
    Phase5ReferenceRequest,
)
from core.resource_manifest import (
    ResourceManifestStore,
    ResourceManifestUpdate,
)
from core.resource_manifest import build_phase5_reference
from core.resource_manifest_status import TerminalResourceStatus
from core.resource_retention import ContainerRetention
from core.terminal_continuation_models import PreparedTerminalContinuation
from . import e2e_test_v3 as target
from tests.terminal_run_continuation_hydration_support import create_hydration_parent
from tests.terminal_run_continuation_parent_scenarios import ParentRun

from .e2e_v3_runtime_fakes import ScriptedSessionManager, SessionScript

PHASE_ORDER = (
    "phase_0_detect",
    "phase_2_prepare",
    "phase_4_migrate",
    "phase_5_validation",
    "phase_6_report",
)


@dataclass(frozen=True, slots=True)
class ContinuationParentSpec:
    case_id: str
    status: str
    anchor: str
    phase_statuses: tuple[str, ...]
    canonical_count: int
    environment_required: bool = True
    phase5_environment_reference: bool = False
    parent_trace_payload: bytes | None = None


@dataclass(frozen=True, slots=True)
class ContinuationRunResult:
    exit_code: int
    report_dir: Path
    manager: ScriptedSessionManager


def create_runtime_parent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    spec: ContinuationParentSpec,
) -> ParentRun:
    root = tmp_path.parent / f"c-{spec.case_id}"
    root.mkdir()
    original_seal = ResourceManifestStore.seal

    def seal_with_environment(
        store: ResourceManifestStore,
        expected_revision: int,
        terminal_status: TerminalResourceStatus,
    ):
        if spec.parent_trace_payload is not None:
            trace_dir = store.path.parent / "trace"
            trace_dir.mkdir()
            _ = (trace_dir / "manifest.json").write_bytes(spec.parent_trace_payload)
        if not spec.environment_required:
            return original_seal(store, expected_revision, terminal_status)
        observed = probe_retained_environment(
            RetainedEnvironmentProbeRequest(interpreter_path=sys.executable)
        )
        captured = store.context._capture_environment_probe(
            EnvironmentProbeRequest(
                probe_id="probe-execution-python",
                environment_id="execution-python",
                namespace=observed.namespace,
                probe=EnvironmentProbe(
                    status="ok",
                    interpreter_realpath=observed.interpreter_realpath,
                    sys_executable=observed.sys_executable,
                    sys_prefix=observed.sys_prefix,
                    sys_base_prefix=observed.sys_base_prefix,
                    python_implementation=observed.python_implementation,
                    python_version=observed.python_version,
                    platform=observed.platform_system,
                    architecture=observed.platform_architecture,
                    package_inventory_hash=observed.package_inventory_hash,
                ),
            )
        )
        references = ()
        if spec.phase5_environment_reference:
            references = (
                build_phase5_reference(
                    Phase5ReferenceRequest(
                        attempt_id="phase_5_validation-attempt-1",
                        environment_id="execution-python",
                        namespace="host",
                    )
                ),
            )
        revised = store.write(
            ResourceManifestUpdate(
                expected_revision=expected_revision,
                environments=(captured.environment,),
                phase5_environment_references=references,
                probe_receipts=(captured.receipt,),
            )
        )
        return original_seal(store, revised.revision, terminal_status)

    monkeypatch.setattr(ResourceManifestStore, "seal", seal_with_environment)
    parent = create_hydration_parent(
        root,
        status=spec.status,
        anchor_phase=spec.anchor,
        phase_statuses=spec.phase_statuses,
        canonical_phase_ids=PHASE_ORDER[: spec.canonical_count],
        workflow_bytes=_workflow_bytes(),
    )
    monkeypatch.setattr(ResourceManifestStore, "seal", original_seal)
    return parent


def run_prepared_continuation(
    monkeypatch: pytest.MonkeyPatch,
    prepared: PreparedTerminalContinuation,
    *,
    session_script: SessionScript | None = None,
    save_agent_trace: bool | None = None,
) -> ContinuationRunResult:
    manager: ScriptedSessionManager | None = None

    def manager_factory(**kwargs) -> ScriptedSessionManager:
        nonlocal manager
        manager = ScriptedSessionManager(
            script=session_script or SessionScript(),
            **kwargs,
        )
        return manager

    monkeypatch.setattr(
        server_lifecycle,
        "resolve_server_url",
        lambda *_args, **_kwargs: ("http://opencode.test", None),
    )
    monkeypatch.setattr(target, "check_server_running", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        target, "log_server_diagnostics", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(manager_module, "SessionManager", manager_factory)
    exit_code = target.run_e2e_v3(
        base_url="http://opencode.test",
        max_phase5_iter=2,
        keep_temp_dir=True,
        agent_name=None,
        project_dir=None,
        server_auto_start=False,
        opencode_readiness="off",
        continuation=prepared,
        container_retention=ContainerRetention.RETAIN,
        save_agent_trace=save_agent_trace,
    )
    assert manager is not None
    return ContinuationRunResult(
        exit_code,
        prepared.evidence.namespace.report_dir,
        manager,
    )


def _workflow_bytes() -> bytes:
    command = json.dumps(
        shlex.join(
            [
                sys.executable,
                "-c",
                "from pathlib import Path; "
                "p=Path('continuation-count.txt'); "
                "p.write_text(str(int(p.read_text()) + 1) if p.exists() else '1')",
            ]
        )
    )
    return f"""\
name: continuation-runtime
version: '1.0'
globals:
  review_fail_closed: true
experience:
  enabled: false
phases:
  - id: phase_0_detect
    type: builtin
    operation: noop
    transitions: {{on_success: phase_2_prepare}}
  - id: phase_2_prepare
    type: builtin
    operation: noop
    transitions: {{on_success: phase_4_migrate}}
  - id: phase_4_migrate
    type: builtin
    operation: noop
    transitions: {{on_success: phase_5_validation}}
  - id: phase_5_validation
    type: loop
    sub_workflow: repair_loop
    input_mapping:
      entry_script: {command}
      project_dir: "${{PROJECT_DIR}}"
    transitions: {{on_success: phase_6_report, on_failure: failed}}
  - id: phase_6_report
    type: builtin
    operation: noop
    transitions: {{on_success: complete}}
terminals: [complete, failed]
sub_workflows:
  repair_loop:
    id: repair_loop
    type: loop
    max_iterations: 1
    stop_conditions:
      - condition: "$.script_exit_code == 0"
        status: success
    phases:
      - id: run_entry_script
        type: shell
        command: "${{loop_vars.entry_script}}"
        cwd: "${{loop_vars.project_dir}}"
        on_failure: continue
""".encode()
