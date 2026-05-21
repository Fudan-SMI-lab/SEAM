# pyright: reportArgumentType=false, reportMissingParameterType=false, reportUnannotatedClassAttribute=false, reportUnknownArgumentType=false, reportUnknownLambdaType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportUnusedParameter=false

import sys
import types
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.types import PhaseDefinition, WorkflowDefinition, ExecutionBackendConfig
from core.orchestrator import Orchestrator


class MockSessionManager:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def get_or_create(self, role: str, lifecycle: str) -> str:
        self.calls.append(("get_or_create", {"role": role, "lifecycle": lifecycle}))
        return "main-session"

    def send_command(self, session_id: str, command: str, timeout: int = 600) -> str:
        self.calls.append(("send_command", {"session_id": session_id, "command": command, "timeout": timeout}))
        return "Failure Summary\nObserved Evidence\nMost Likely Root Cause\nRecommended Fix\nRetry Decision\nstop"

    def cleanup_all(self) -> int:
        self.calls.append(("cleanup_all", None))
        return 1


def build_workflow() -> WorkflowDefinition:
    return WorkflowDefinition(
        name="mock-workflow",
        version="2.0",
        globals={"max_retry_per_phase": 2},
        phases=[
            PhaseDefinition("phase_0", "Phase 0", "", {}, None, {"on_success": "phase_1", "on_failure": "phase_error_recovery"}),
            PhaseDefinition("phase_1", "Phase 1", "", {}, None, {"on_success": "phase_2", "on_failure": "phase_error_recovery"}),
            PhaseDefinition("phase_2", "Phase 2", "", {}, None, {"on_success": "phase_3", "on_failure": "phase_error_recovery"}),
            PhaseDefinition("phase_3", "Phase 3", "", {}, None, {"on_success": "phase_4", "on_failure": "phase_error_recovery"}),
            PhaseDefinition("phase_4", "Phase 4", "", {}, None, {"on_success": "phase_5", "on_failure": "phase_error_recovery"}),
            PhaseDefinition("phase_5", "Phase 5", "", {}, None, {"on_success": "phase_6", "on_failure": "phase_error_recovery"}),
            PhaseDefinition("phase_6", "Phase 6", "", {}, None, {"on_success": "complete", "on_failure": "phase_error_recovery"}),
            PhaseDefinition("phase_error_recovery", "Error Recovery", "", {}, None, {"on_success": "failed", "on_failure": "failed"}),
        ],
        terminals=["complete", "failed"],
    )


def test_run_workflow_wires_components_and_executes_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    call_order: list[str] = []
    journal_entries: list[dict[str, object]] = []
    prompt_loader_dirs: list[str] = []
    phase_runner_init_args: list[tuple[WorkflowDefinition | None, dict[str, object] | None]] = []
    phase3_contracts: list[dict[str, object] | None] = []
    fw_config: dict[str, object] = {
        "runtime_skill_repo_root": "/tmp/runtime-skills",
        "framework": {"review": {"enabled": False, "max_review_iterations": 3}},
    }

    class FakeArtifactStore:
        def __init__(self, base_dir: str, run_id: str) -> None:
            call_order.append("artifact_store_init")
            self.base_dir = base_dir
            self.run_id = run_id
            self.saved: dict[str, dict[str, object]] = {}

        def write_journal(self, entry: dict[str, object]) -> str:
            journal_entries.append(entry)
            return "journal.jsonl"

        def load_phase_output(self, phase_id: str) -> dict[str, object] | None:
            return self.saved.get(phase_id)

        def save_phase_output(self, phase_id: str, data: dict[str, object], attempt: int = 0) -> str:
            self.saved[phase_id] = data
            return f"raw/{phase_id}-{attempt}.json"

        def mark_validated(self, phase_id: str, data: dict[str, object]) -> str:
            self.saved[phase_id] = data
            return f"validated/{phase_id}.json"

    class FakePromptLoader:
        def __init__(self, prompts_dir: str) -> None:
            call_order.append("prompt_loader_init")
            self.prompts_dir = prompts_dir
            prompt_loader_dirs.append(prompts_dir)

        def load_prompt(self, phase_id: str, context: dict[str, str] | None = None) -> str:
            return f"prompt:{phase_id}:{context}"

    class FakeValidatorEngine:
        def __init__(self) -> None:
            call_order.append("validator_engine_init")

    class FakeStateMachine:
        def __init__(self, workflow: WorkflowDefinition) -> None:
            call_order.append("state_machine_init")
            assert workflow.phases is not None
            self.transitions = {
                "phase_0": "phase_1",
                "phase_1": "phase_2",
                "phase_2": "phase_3",
                "phase_3": "phase_4",
                "phase_4": "phase_5",
                "phase_5": "phase_6",
                "phase_6": "complete",
                "phase_error_recovery": "failed",
            }
            self.current_phase = workflow.phases[0].id
            self.terminal = None

        def record_success(self, phase_id: str) -> tuple[bool, str | None]:
            call_order.append(f"record_success:{phase_id}")
            next_target = self.transitions[phase_id]
            if next_target in {"complete", "failed"}:
                self.current_phase = None
                self.terminal = next_target
            else:
                self.current_phase = next_target
            return True, next_target

        def record_failure(self, phase_id: str, error: str) -> tuple[bool, str | None]:
            call_order.append(f"record_failure:{phase_id}:{error}")
            self.current_phase = "phase_error_recovery"
            return False, "phase_error_recovery"

        def current_terminal(self) -> str | None:
            return self.terminal

    class FakePhaseRunner:
        def set_container_context(self, ctx) -> None: pass
        def set_execution_environment_context(self, ctx: str) -> None: pass
        def __init__(self, session_mgr, artifact_store, prompt_loader, validator, *, workflow=None, framework_config=None) -> None:
            call_order.append("phase_runner_init")
            phase_runner_init_args.append((workflow, framework_config))
        def set_container_context(self, ctx) -> None: pass
        def set_execution_environment_context(self, ctx: str) -> None: pass
        def run_phase_0_to_3(self, project_dir: str, session_mgr, artifact_store) -> dict[str, dict[str, object]]:
            call_order.append("run_phase_0_to_3")
            return {
                "phase_0_env_detect": {"platform": "linux"},
                "phase_1_project_analysis": {"project_dir": project_dir},
                "phase_2_venv_create": {"venv_path": f"{project_dir}/.venv"},
                "phase_3_entry_script": {"entry_script_path": f"{project_dir}/train.py", "run_command": "python train.py"},
            }

        def run_phase_0_to_1(self, project_dir: str, session_mgr, artifact_store, user_constraints: str = "") -> dict[str, dict[str, object]]:
            call_order.append("run_phase_0_to_1")
            return {
                "phase_0_env_detect": {"platform": "linux"},
                "phase_1_project_analysis": {"project_dir": project_dir},
            }

        def run_phase_1_5(self, main_session_id, session_mgr, artifact_store, *, project_dir, user_constraints, phase_1_output=None) -> str:
            call_order.append("run_phase_1_5")
            return "Rule 1: No CPU fallback"

        def run_phase_2_to_3(self, project_dir: str, session_mgr, artifact_store, prior_outputs, constraint_summary: str = "") -> dict[str, dict[str, object]]:
            call_order.append("run_phase_2_to_3")
            return {
                "phase_2_venv_create": {"venv_path": f"{project_dir}/.venv"},
                "phase_3_entry_script": {"entry_script_path": f"{project_dir}/train.py", "run_command": "python train.py"},
            }

        def run_phase_4(self, artifact_store, migrator) -> dict[str, object]:
            call_order.append("run_phase_4")
            return {"files_migrated": 1, "files_skipped": 0, "replacement_counts": {}, "total_replacements": 1}

        def run_phase_6(self, project_dir: str, artifact_store, session_mgr) -> dict[str, object]:
            call_order.append("run_phase_6")
            return {"phase_id": "phase_6_report", "report_paths": [], "migration_summary": {}}

        def run_review_check(self, review_session_id, session_mgr, phase_0_to_3_outputs, project_dir, repair_context) -> dict[str, object]:
            call_order.append("run_review_check")
            return {
                "verdict": "accept",
                "cpu_fallback_detected": False,
                "cpu_fallback_necessary": False,
                "alternative_suggestions": "",
                "reasoning": "",
            }

    class FakeRepairLoopEngine:
        def __init__(self, session_mgr, artifact_store, prompt_loader, validator, config=None, exec_backend=None) -> None:
            call_order.append("repair_loop_init")
            self._received_exec_backend = exec_backend

        @staticmethod
        def _format_history_summary(history: list[dict[str, object]]) -> str:
            return str(history)

        def run(
            self,
            entry_script: str,
            project_dir: str,
            review_callable=None,
            constraint_summary: str = "",
            env_context: dict[str, object] | None = None,
            phase3_contract: dict[str, object] | None = None,
            enable_review_gate: bool = False,
            max_review_iterations: int = 3,
        ) -> dict[str, object]:
            del env_context
            phase3_contracts.append(phase3_contract)
            call_order.append(f"run_phase_5:{entry_script}")
            if enable_review_gate and review_callable is not None:
                review_callable({})
            return {"success": True, "status": "success", "iteration_count": 1, "errors": []}

    class FakeRuleBasedMigrator:
        def __init__(self) -> None:
            call_order.append("rule_based_migrator_init")

    monkeypatch.setattr("core.orchestrator.load_workflow", lambda path: build_workflow())
    monkeypatch.setattr("core.orchestrator.load_framework_config", lambda path=None: fw_config)
    monkeypatch.setattr("core.orchestrator.uuid4", lambda: types.SimpleNamespace(hex="abc123"))
    monkeypatch.setattr("core.orchestrator.ArtifactStore", FakeArtifactStore)
    monkeypatch.setattr("core.orchestrator.PromptLoader", FakePromptLoader)
    monkeypatch.setattr("core.orchestrator.ValidatorEngine", FakeValidatorEngine)
    monkeypatch.setattr("core.orchestrator.StateMachine", FakeStateMachine)
    monkeypatch.setattr("core.orchestrator.PhaseRunner", FakePhaseRunner)
    monkeypatch.setattr("core.orchestrator.RepairLoopEngine", FakeRepairLoopEngine)
    monkeypatch.setattr("core.orchestrator.RuleBasedMigrator", FakeRuleBasedMigrator)

    session_mgr = MockSessionManager()
    orchestrator = Orchestrator(session_mgr=session_mgr, project_dir="/repo/project", workflow_path="workflow.yaml")

    result = orchestrator.run_workflow("/repo/project", user_constraints="Zero CPU fallback")

    assert result == {
        "run_id": "run-abc123",
        "workflow_name": "mock-workflow",
        "workflow_version": "2.0",
        "project_dir": "/repo/project",
        "phases": {
            "phase_0_env_detect": {"platform": "linux"},
            "phase_1_project_analysis": {"project_dir": "/repo/project"},
            "phase_2_venv_create": {"venv_path": "/repo/project/.venv"},
            "phase_3_entry_script": {"entry_script_path": "/repo/project/train.py", "run_command": "python train.py"},
            "phase_4_rule_migration": {"files_migrated": 1, "files_skipped": 0, "replacement_counts": {}, "total_replacements": 1},
            "phase_5_validation": {"success": True, "status": "success", "iteration_count": 1, "errors": []},
            "phase_6_report": {"phase_id": "phase_6_report", "report_paths": [], "migration_summary": {}},
            "constraint_summary": "Rule 1: No CPU fallback",
        },
        "terminal_state": "complete",
        "success": True,
        "main_session_id": "main-session",
    }
    assert call_order == [
        "artifact_store_init",
        "prompt_loader_init",
        "validator_engine_init",
        "state_machine_init",
        "phase_runner_init",
        "repair_loop_init",
        "rule_based_migrator_init",
        "run_phase_0_to_1",
        "record_success:phase_0",
        "record_success:phase_1",
        "run_phase_1_5",
        "run_phase_2_to_3",
        "record_success:phase_2",
        "record_success:phase_3",
        "run_phase_4",
        "record_success:phase_4",
        "run_phase_5:python train.py",
        "record_success:phase_5",
        "run_phase_6",
        "record_success:phase_6",
    ]
    assert session_mgr.calls == [
        ("get_or_create", {"role": "main_engineer", "lifecycle": "persistent"}),
        ("cleanup_all", None),
    ]
    assert journal_entries[-1]["status"] == "cleanup_completed"
    assert prompt_loader_dirs and prompt_loader_dirs[0].endswith("migration_utils/prompts")
    assert len(phase_runner_init_args) == 1
    assert phase_runner_init_args[0][0] is not None
    assert phase_runner_init_args[0][0].name == "mock-workflow"
    assert phase_runner_init_args[0][1] is fw_config
    assert phase3_contracts == [{"entry_script_path": "/repo/project/train.py", "run_command": "python train.py"}]


def test_run_workflow_stops_before_phase6_when_phase5_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    call_order: list[str] = []
    journal_entries: list[dict[str, object]] = []

    class FakeArtifactStore:
        def __init__(self, base_dir: str, run_id: str) -> None:
            self.saved: dict[str, dict[str, object]] = {}

        def write_journal(self, entry: dict[str, object]) -> str:
            journal_entries.append(entry)
            return "journal.jsonl"

        def load_phase_output(self, phase_id: str) -> dict[str, object] | None:
            return self.saved.get(phase_id)

        def save_phase_output(self, phase_id: str, data: dict[str, object], attempt: int = 0) -> str:
            self.saved[phase_id] = data
            return f"raw/{phase_id}.json"

        def mark_validated(self, phase_id: str, data: dict[str, object]) -> str:
            self.saved[phase_id] = data
            return f"validated/{phase_id}.json"

    class FakePromptLoader:
        def __init__(self, prompts_dir: str) -> None:
            pass

        def load_prompt(self, phase_id: str, context: dict[str, str] | None = None) -> str:
            return "recovery prompt"

    class FakeValidatorEngine:
        pass

    class FakeStateMachine:
        def __init__(self, workflow: WorkflowDefinition) -> None:
            self.current_phase = "phase_0"
            self.terminal = None

        def record_success(self, phase_id: str) -> tuple[bool, str | None]:
            call_order.append(f"record_success:{phase_id}")
            transitions = {
                "phase_0": "phase_1",
                "phase_1": "phase_2",
                "phase_2": "phase_3",
                "phase_3": "phase_4",
                "phase_4": "phase_5",
                "phase_5": "phase_6",
                "phase_6": "complete",
                "phase_error_recovery": "failed",
            }
            target = transitions[phase_id]
            if target in {"complete", "failed"}:
                self.current_phase = None
                self.terminal = target
            else:
                self.current_phase = target
            return True, target

        def record_failure(self, phase_id: str, error: str) -> tuple[bool, str | None]:
            call_order.append(f"record_failure:{phase_id}:{error}")
            self.current_phase = "phase_error_recovery"
            return False, "phase_error_recovery"

        def current_terminal(self) -> str | None:
            return self.terminal

    class FakePhaseRunner:
        def set_container_context(self, ctx) -> None: pass
        def set_execution_environment_context(self, ctx: str) -> None: pass
        def __init__(self, *args, **kwargs) -> None:
            pass

        def run_phase_0_to_1(self, project_dir: str, session_mgr, artifact_store, user_constraints: str = "") -> dict[str, dict[str, object]]:
            return {
                "phase_0_env_detect": {"platform": "linux"},
                "phase_1_project_analysis": {"project_dir": project_dir},
            }

        def run_phase_1_5(self, *args, **kwargs) -> str:
            return ""

        def run_phase_2_to_3(self, project_dir: str, session_mgr, artifact_store, prior_outputs, constraint_summary: str = "") -> dict[str, dict[str, object]]:
            return {
                "phase_2_venv_create": {"venv_path": f"{project_dir}/.venv"},
                "phase_3_entry_script": {"entry_script_path": f"{project_dir}/train.py", "run_command": "python train.py"},
            }

        def run_phase_4(self, artifact_store, migrator) -> dict[str, object]:
            return {"files_migrated": 1, "files_skipped": 0, "replacement_counts": {}, "total_replacements": 1}

        def run_phase_6(self, project_dir: str, artifact_store, session_mgr) -> dict[str, object]:
            call_order.append("run_phase_6")
            return {"phase_id": "phase_6_report"}

        def run_review_check(self, *args, **kwargs) -> dict[str, object]:
            return {"verdict": "accept"}

    class FakeRepairLoopEngine:
        def __init__(self, *args, **kwargs) -> None:
            pass

        @staticmethod
        def _format_history_summary(history: list[dict[str, object]]) -> str:
            return str(history)

        def run(self, *args, **kwargs) -> dict[str, object]:
            call_order.append("run_phase_5_failure")
            return {"success": False, "status": "max_iterations", "iteration_count": 3, "errors": ["still failing"]}

    class FakeRuleBasedMigrator:
        pass

    monkeypatch.setattr("core.orchestrator.load_workflow", lambda path: build_workflow())
    monkeypatch.setattr("core.orchestrator.load_framework_config", lambda path=None: {"framework": {"review": {"enabled": False}}})
    monkeypatch.setattr("core.orchestrator.uuid4", lambda: types.SimpleNamespace(hex="failed5"))
    monkeypatch.setattr("core.orchestrator.ArtifactStore", FakeArtifactStore)
    monkeypatch.setattr("core.orchestrator.PromptLoader", FakePromptLoader)
    monkeypatch.setattr("core.orchestrator.ValidatorEngine", FakeValidatorEngine)
    monkeypatch.setattr("core.orchestrator.StateMachine", FakeStateMachine)
    monkeypatch.setattr("core.orchestrator.PhaseRunner", FakePhaseRunner)
    monkeypatch.setattr("core.orchestrator.RepairLoopEngine", FakeRepairLoopEngine)
    monkeypatch.setattr("core.orchestrator.RuleBasedMigrator", FakeRuleBasedMigrator)

    result = Orchestrator(session_mgr=MockSessionManager(), project_dir="/repo/project", workflow_path="workflow.yaml").run_workflow("/repo/project")

    assert result["success"] is False
    assert result["failed_phase"] == "phase_5"
    phases = result["phases"]
    assert isinstance(phases, dict)
    assert "phase_6_report" not in phases
    assert "run_phase_6" not in call_order
    assert not any(entry.get("phase_id") == "phase_6_report" for entry in journal_entries)
    assert any(entry.get("phase_id") == "phase_5_validation" and entry.get("status") == "failed" for entry in journal_entries)


def _build_workflow_with_backend(backend_cfg=None) -> WorkflowDefinition:
    """Build a test workflow, optionally with an execution_backend config."""
    return WorkflowDefinition(
        name="mock-workflow",
        version="2.0",
        globals={"max_retry_per_phase": 2},
        phases=[
            PhaseDefinition("phase_0", "Phase 0", "", {}, None, {"on_success": "phase_1", "on_failure": "phase_error_recovery"}),
            PhaseDefinition("phase_1", "Phase 1", "", {}, None, {"on_success": "phase_2", "on_failure": "phase_error_recovery"}),
            PhaseDefinition("phase_2", "Phase 2", "", {}, None, {"on_success": "phase_3", "on_failure": "phase_error_recovery"}),
            PhaseDefinition("phase_3", "Phase 3", "", {}, None, {"on_success": "phase_4", "on_failure": "phase_error_recovery"}),
            PhaseDefinition("phase_4", "Phase 4", "", {}, None, {"on_success": "phase_5", "on_failure": "phase_error_recovery"}),
            PhaseDefinition("phase_5", "Phase 5", "", {}, None, {"on_success": "phase_6", "on_failure": "phase_error_recovery"}),
            PhaseDefinition("phase_6", "Phase 6", "", {}, None, {"on_success": "complete", "on_failure": "phase_error_recovery"}),
            PhaseDefinition("phase_error_recovery", "Error Recovery", "", {}, None, {"on_success": "failed", "on_failure": "failed"}),
        ],
        terminals=["complete", "failed"],
        execution_backend=backend_cfg,
    )


def test_orchestrator_passes_container_backend_to_repair_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    """When workflow.execution_backend is 'container', orchestrator creates ContainerBackend
    and passes it to RepairLoopEngine."""
    captured_backend: list[object] = []
    fw_config: dict[str, object] = {"framework": {"review": {"enabled": False}}}
    container_cfg = ExecutionBackendConfig.from_dict({
        "mode": "container",
        "image": "test:latest",
    })

    class FakeArtifactStore:
        def __init__(self, base_dir: str, run_id: str) -> None:
            self.saved: dict[str, dict[str, object]] = {}
        def write_journal(self, entry: dict[str, object]) -> str: return "j"
        def load_phase_output(self, phase_id: str) -> dict[str, object] | None: return None
        def save_phase_output(self, phase_id: str, data: dict[str, object], attempt: int = 0) -> str:
            return f"raw/{phase_id}"
        def mark_validated(self, phase_id: str, data: dict[str, object]) -> str: return f"v/{phase_id}"

    class FakePromptLoader:
        def __init__(self, d: str) -> None: pass
        def load_prompt(self, phase_id: str, context=None) -> str: return "prompt"

    class FakeValidatorEngine: pass

    class FakeStateMachine:
        def __init__(self, wf) -> None:
            self.current_phase = wf.phases[0].id; self.terminal = None
            self.transitions = {}
            for p in wf.phases or []:
                self.transitions[p.id] = p.transitions.get("on_success", "complete")
        def record_success(self, phase_id: str) -> tuple[bool, str | None]:
            t = self.transitions.get(phase_id, "complete")
            if t in ("complete", "failed"):
                self.current_phase = None; self.terminal = t
            else:
                self.current_phase = t
            return True, t
        def record_failure(self, phase_id: str, error: str) -> tuple[bool, str | None]:
            self.current_phase = "phase_error_recovery"; return False, "phase_error_recovery"
        def current_terminal(self) -> str | None: return self.terminal

    class FakePhaseRunner:
        def set_container_context(self, ctx) -> None: pass
        def set_execution_environment_context(self, ctx: str) -> None: pass
        def __init__(self, *a, **kw) -> None: pass
        def run_phase_0_to_1(self, *a, **kw) -> dict: return {"phase_0_env_detect": {}, "phase_1_project_analysis": {}}
        def run_phase_1_5(self, *a, **kw) -> str: return ""
        def run_phase_2_to_3(self, project_dir: str, *a, **kw) -> dict:
            return {"phase_2_venv_create": {}, "phase_3_entry_script": {"run_command": "python t.py"}}
        def run_phase_4(self, *a, **kw) -> dict: return {}
        def run_phase_6(self, *a, **kw) -> dict: return {}
        def run_review_check(self, *a, **kw) -> dict: return {"verdict": "accept"}

    class FakeRepairLoopEngine:
        def __init__(self, *a, exec_backend=None, **kw) -> None:
            captured_backend.append(exec_backend)
        @staticmethod
        def _format_history_summary(h): return ""
        def run(self, *a, **kw) -> dict: return {"success": True, "status": "success", "iteration_count": 1, "errors": []}

    class FakeMigrator: pass

    class FakeContainerBackend:
        def __init__(self, cfg) -> None: self._cfg = cfg
        def set_project_dir(self, d: str) -> None: pass
        def preflight(self) -> None: pass
        def probe_environment(self) -> dict: return {"status": "ok"}
        def cleanup(self) -> None: pass
        def run(self, *a, **kw) -> object: raise NotImplementedError
        def get_execution_context(self, **kw) -> dict: return {"execution_backend_mode": "container"}

    monkeypatch.setattr("core.orchestrator.load_workflow", lambda p: _build_workflow_with_backend(container_cfg))
    monkeypatch.setattr("core.orchestrator.load_framework_config", lambda p=None: fw_config)
    monkeypatch.setattr("core.orchestrator.uuid4", lambda: types.SimpleNamespace(hex="t1"))
    monkeypatch.setattr("core.execution_backend.ContainerBackend", FakeContainerBackend)
    monkeypatch.setattr("core.orchestrator.ArtifactStore", FakeArtifactStore)
    monkeypatch.setattr("core.orchestrator.PromptLoader", FakePromptLoader)
    monkeypatch.setattr("core.orchestrator.ValidatorEngine", FakeValidatorEngine)
    monkeypatch.setattr("core.orchestrator.StateMachine", FakeStateMachine)
    monkeypatch.setattr("core.orchestrator.PhaseRunner", FakePhaseRunner)
    monkeypatch.setattr("core.orchestrator.RepairLoopEngine", FakeRepairLoopEngine)
    monkeypatch.setattr("core.orchestrator.RuleBasedMigrator", FakeMigrator)

    Orchestrator(session_mgr=MockSessionManager(), project_dir="/tmp/p", workflow_path="wf.yaml").run_workflow("/tmp/p")

    assert len(captured_backend) == 1
    backend = captured_backend[0]
    assert backend is not None
    assert hasattr(backend, "cleanup")
    assert hasattr(backend, "run")


def test_orchestrator_passes_none_backend_for_local_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """When execution_backend is absent or mode='local', orchestrator passes exec_backend=None."""
    captured_backend: list[object] = []
    fw_config: dict[str, object] = {"framework": {"review": {"enabled": False}}}

    class FakeArtifactStore:
        def __init__(self, *a, **kw) -> None: self.saved = {}
        def write_journal(self, entry: dict[str, object]) -> str: return "j"
        def load_phase_output(self, phase_id: str) -> dict[str, object] | None: return None
        def save_phase_output(self, phase_id: str, data: dict[str, object], attempt: int = 0) -> str: return "r"
        def mark_validated(self, phase_id: str, data: dict[str, object]) -> str: return "v"
    class FakePromptLoader:
        def __init__(self, d: str) -> None: pass
        def load_prompt(self, *a, **kw) -> str: return "prompt"
    class FakeValidatorEngine: pass
    class FakeStateMachine:
        def __init__(self, wf) -> None:
            self.current_phase = wf.phases[0].id; self.terminal = None
        def record_success(self, phase_id: str) -> tuple[bool, str | None]:
            self.current_phase = None; self.terminal = "complete"; return True, "complete"
        def record_failure(self, *a): return False, None
        def current_terminal(self) -> str | None: return self.terminal
    class FakePhaseRunner:
        def set_container_context(self, ctx) -> None: pass
        def set_execution_environment_context(self, ctx: str) -> None: pass
        def __init__(self, *a, **kw) -> None: pass
        def run_phase_0_to_1(self, *a, **kw) -> dict: return {}
        def run_phase_1_5(self, *a, **kw) -> str: return ""
        def run_phase_2_to_3(self, project_dir: str, *a, **kw) -> dict:
            return {"phase_3_entry_script": {"run_command": "python t.py"}}
        def run_phase_4(self, *a, **kw) -> dict: return {}
        def run_phase_6(self, *a, **kw) -> dict: return {}
        def run_review_check(self, *a, **kw) -> dict: return {"verdict": "accept"}
    class FakeRepairLoopEngine:
        def __init__(self, *a, exec_backend=None, **kw) -> None: captured_backend.append(exec_backend)
        @staticmethod
        def _format_history_summary(h): return ""
        def run(self, *a, **kw) -> dict: return {"success": True, "status": "success", "iteration_count": 1, "errors": []}
    class FakeMigrator: pass

    # Test with absent backend
    monkeypatch.setattr("core.orchestrator.load_workflow", lambda p: _build_workflow_with_backend(None))
    monkeypatch.setattr("core.orchestrator.load_framework_config", lambda p=None: fw_config)
    monkeypatch.setattr("core.orchestrator.uuid4", lambda: types.SimpleNamespace(hex="t2"))
    monkeypatch.setattr("core.orchestrator.ArtifactStore", FakeArtifactStore)
    monkeypatch.setattr("core.orchestrator.PromptLoader", FakePromptLoader)
    monkeypatch.setattr("core.orchestrator.ValidatorEngine", FakeValidatorEngine)
    monkeypatch.setattr("core.orchestrator.StateMachine", FakeStateMachine)
    monkeypatch.setattr("core.orchestrator.PhaseRunner", FakePhaseRunner)
    monkeypatch.setattr("core.orchestrator.RepairLoopEngine", FakeRepairLoopEngine)
    monkeypatch.setattr("core.orchestrator.RuleBasedMigrator", FakeMigrator)

    Orchestrator(session_mgr=MockSessionManager(), project_dir="/tmp/p", workflow_path="wf.yaml").run_workflow("/tmp/p")
    assert captured_backend[-1] is None


def test_orchestrator_cleans_up_backend_in_finally(monkeypatch: pytest.MonkeyPatch) -> None:
    """Orchestrator calls cleanup() on the execution backend in the finally block."""
    cleanup_calls: list[None] = []
    fw_config: dict[str, object] = {"framework": {"review": {"enabled": False}}}
    container_cfg = ExecutionBackendConfig.from_dict({
        "mode": "container",
        "image": "test:latest",
    })

    class FakeBackend:
        def __init__(self) -> None: pass
        def set_project_dir(self, d: str) -> None: pass
        def cleanup(self) -> None: cleanup_calls.append(None)

    class FakeContainerBackend:
        def __init__(self, cfg) -> None: pass
        def set_project_dir(self, d: str) -> None: pass
        def cleanup(self) -> None: cleanup_calls.append(None)

    class FakeArtifactStore:
        def __init__(self, *a, **kw) -> None: self.saved = {}
        def write_journal(self, entry: dict[str, object]) -> str: return "j"
        def load_phase_output(self, phase_id: str) -> dict[str, object] | None: return None
        def save_phase_output(self, phase_id: str, data: dict[str, object], attempt: int = 0) -> str: return "r"
        def mark_validated(self, phase_id: str, data: dict[str, object]) -> str: return "v"
    class FakePromptLoader:
        def __init__(self, d: str) -> None: pass
        def load_prompt(self, *a, **kw) -> str: return "prompt"
    class FakeValidatorEngine: pass
    class FakeStateMachine:
        def __init__(self, wf) -> None:
            self.current_phase = wf.phases[0].id; self.terminal = None
        def record_success(self, phase_id: str) -> tuple[bool, str | None]:
            self.current_phase = None; self.terminal = "complete"; return True, "complete"
        def record_failure(self, *a): return False, None
        def current_terminal(self) -> str | None: return self.terminal
    class FakePhaseRunner:
        def set_container_context(self, ctx) -> None: pass
        def set_execution_environment_context(self, ctx: str) -> None: pass
        def __init__(self, *a, **kw) -> None: pass
        def run_phase_0_to_1(self, *a, **kw) -> dict: return {}
        def run_phase_1_5(self, *a, **kw) -> str: return ""
        def run_phase_2_to_3(self, project_dir: str, *a, **kw) -> dict:
            return {"phase_3_entry_script": {"run_command": "python t.py"}}
        def run_phase_4(self, *a, **kw) -> dict: return {}
        def run_phase_6(self, *a, **kw) -> dict: return {}
        def run_review_check(self, *a, **kw) -> dict: return {"verdict": "accept"}
    class FakeRepairLoopEngine:
        def __init__(self, *a, exec_backend=None, **kw) -> None: pass
        @staticmethod
        def _format_history_summary(h): return ""
        def run(self, *a, **kw) -> dict: return {"success": True, "status": "success", "iteration_count": 1, "errors": []}
    class FakeMigrator: pass

    monkeypatch.setattr("core.orchestrator.load_workflow", lambda p: _build_workflow_with_backend(container_cfg))
    monkeypatch.setattr("core.orchestrator.load_framework_config", lambda p=None: fw_config)
    monkeypatch.setattr("core.orchestrator.uuid4", lambda: types.SimpleNamespace(hex="t3"))
    monkeypatch.setattr("core.execution_backend.ContainerBackend", FakeContainerBackend)
    monkeypatch.setattr("core.orchestrator.ArtifactStore", FakeArtifactStore)
    monkeypatch.setattr("core.orchestrator.PromptLoader", FakePromptLoader)
    monkeypatch.setattr("core.orchestrator.ValidatorEngine", FakeValidatorEngine)
    monkeypatch.setattr("core.orchestrator.StateMachine", FakeStateMachine)
    monkeypatch.setattr("core.orchestrator.PhaseRunner", FakePhaseRunner)
    monkeypatch.setattr("core.orchestrator.RepairLoopEngine", FakeRepairLoopEngine)
    monkeypatch.setattr("core.orchestrator.RuleBasedMigrator", FakeMigrator)

    Orchestrator(session_mgr=MockSessionManager(), project_dir="/tmp/p", workflow_path="wf.yaml").run_workflow("/tmp/p")
    assert len(cleanup_calls) == 1


def test_orchestrator_calls_preflight_for_container_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    preflight_calls: list[None] = []
    probe_calls: list[None] = []
    cleanup_calls: list[None] = []
    set_container_ctxs: list[dict] = []
    fw_config: dict[str, object] = {"framework": {"review": {"enabled": False}}}
    container_cfg = ExecutionBackendConfig.from_dict({"mode": "container", "image": "test:latest"})

    class FakeBackend:
        def __init__(self) -> None: pass
        def set_project_dir(self, d: str) -> None: pass
        def preflight(self) -> None: preflight_calls.append(None)
        def cleanup(self) -> None: cleanup_calls.append(None)

    class FakeContainerBackend:
        def __init__(self, cfg) -> None: pass
        def set_project_dir(self, d: str) -> None: pass
        def preflight(self) -> None: preflight_calls.append(None)
        def probe_environment(self) -> dict:
            probe_calls.append(None)
            return {"container_id": "c1", "status": "ok", "python_version": "3.10.0"}
        def cleanup(self) -> None: cleanup_calls.append(None)
        def get_execution_context(self, **kw) -> dict:
            return {"execution_backend_mode": "container", "container_name_or_id": "c1"}

    class FakeArtifactStore:
        def __init__(self, *a, **kw) -> None: self.saved = {}
        def write_journal(self, entry: dict[str, object]) -> str: return "j"
        def load_phase_output(self, phase_id: str) -> dict[str, object] | None: return None
        def save_phase_output(self, phase_id: str, data: dict[str, object], attempt: int = 0) -> str: return "r"
        def mark_validated(self, phase_id: str, data: dict[str, object]) -> str: return "v"
    class FakePromptLoader:
        def __init__(self, d: str) -> None: pass
        def load_prompt(self, *a, **kw) -> str: return "prompt"
    class FakeValidatorEngine: pass
    class FakeStateMachine:
        def __init__(self, wf) -> None:
            self.current_phase = wf.phases[0].id; self.terminal = None
        def record_success(self, phase_id: str) -> tuple[bool, str | None]:
            self.current_phase = None; self.terminal = "complete"; return True, "complete"
        def record_failure(self, *a): return False, None
        def current_terminal(self) -> str | None: return self.terminal

    class FakePhaseRunner:
        def set_container_context(self, ctx) -> None: pass
        def set_execution_environment_context(self, ctx: str) -> None: pass
        def __init__(self, *a, **kw) -> None: pass
        def set_container_context(self, ctx) -> None: set_container_ctxs.append(dict(ctx))
        def set_execution_environment_context(self, ctx: str) -> None: pass
        def run_phase_0_to_1(self, *a, **kw) -> dict: return {}
        def run_phase_1_5(self, *a, **kw) -> str: return ""
        def run_phase_2_to_3(self, project_dir: str, *a, **kw) -> dict:
            return {"phase_3_entry_script": {"run_command": "python t.py"}}
        def run_phase_4(self, *a, **kw) -> dict: return {}
        def run_phase_6(self, *a, **kw) -> dict: return {}
        def run_review_check(self, *a, **kw) -> dict: return {"verdict": "accept"}

    class FakeRepairLoopEngine:
        def __init__(self, *a, exec_backend=None, **kw) -> None: pass
        @staticmethod
        def _format_history_summary(h): return ""
        def run(self, *a, **kw) -> dict: return {"success": True, "status": "success", "iteration_count": 1, "errors": []}
    class FakeMigrator: pass

    monkeypatch.setattr("core.orchestrator.load_workflow", lambda p: _build_workflow_with_backend(container_cfg))
    monkeypatch.setattr("core.orchestrator.load_framework_config", lambda p=None: fw_config)
    monkeypatch.setattr("core.orchestrator.uuid4", lambda: types.SimpleNamespace(hex="pf1"))
    monkeypatch.setattr("core.execution_backend.ContainerBackend", FakeContainerBackend)
    monkeypatch.setattr("core.orchestrator.ArtifactStore", FakeArtifactStore)
    monkeypatch.setattr("core.orchestrator.PromptLoader", FakePromptLoader)
    monkeypatch.setattr("core.orchestrator.ValidatorEngine", FakeValidatorEngine)
    monkeypatch.setattr("core.orchestrator.StateMachine", FakeStateMachine)
    monkeypatch.setattr("core.orchestrator.PhaseRunner", FakePhaseRunner)
    monkeypatch.setattr("core.orchestrator.RepairLoopEngine", FakeRepairLoopEngine)
    monkeypatch.setattr("core.orchestrator.RuleBasedMigrator", FakeMigrator)

    Orchestrator(session_mgr=MockSessionManager(), project_dir="/tmp/p", workflow_path="wf.yaml").run_workflow("/tmp/p")
    assert len(preflight_calls) == 1
    assert len(probe_calls) == 1
    assert len(set_container_ctxs) == 1
    assert "execution_backend_mode" in set_container_ctxs[0]
    assert "container_env_facts" in set_container_ctxs[0]
    assert len(cleanup_calls) == 1


def test_orchestrator_preflight_failure_stops_early(monkeypatch: pytest.MonkeyPatch) -> None:
    fw_config: dict[str, object] = {"framework": {"review": {"enabled": False}}}
    container_cfg = ExecutionBackendConfig.from_dict({"mode": "container", "image": "test:latest"})

    class FakeContainerBackend:
        def __init__(self, cfg) -> None: pass
        def set_project_dir(self, d: str) -> None: pass
        def preflight(self) -> None: raise RuntimeError("preflight failed")
        def cleanup(self) -> None: pass

    class FakeArtifactStore:
        def __init__(self, *a, **kw) -> None: self.saved = {}
        def write_journal(self, entry: dict[str, object]) -> str: return "j"
        def load_phase_output(self, phase_id: str) -> dict[str, object] | None: return None
        def save_phase_output(self, phase_id: str, data: dict[str, object], attempt: int = 0) -> str: return "r"
        def mark_validated(self, phase_id: str, data: dict[str, object]) -> str: return "v"
    class FakePromptLoader:
        def __init__(self, d: str) -> None: pass
        def load_prompt(self, *a, **kw) -> str: return "prompt"
    class FakeValidatorEngine: pass
    class FakeStateMachine:
        def __init__(self, wf) -> None: self.current_phase = wf.phases[0].id; self.terminal = None
        def record_success(self, phase_id: str) -> tuple[bool, str | None]:
            self.current_phase = None; self.terminal = "complete"; return True, "complete"
        def record_failure(self, *a): return False, None
        def current_terminal(self) -> str | None: return self.terminal
    class FakePhaseRunner:
        def set_container_context(self, ctx) -> None: pass
        def set_execution_environment_context(self, ctx: str) -> None: pass
        def __init__(self, *a, **kw) -> None: pass
    class FakeRepairLoopEngine:
        def __init__(self, *a, exec_backend=None, **kw) -> None: pass
        @staticmethod
        def _format_history_summary(h): return ""
        def run(self, *a, **kw) -> dict: return {"success": True, "status": "success", "iteration_count": 1, "errors": []}
    class FakeMigrator: pass

    monkeypatch.setattr("core.orchestrator.load_workflow", lambda p: _build_workflow_with_backend(container_cfg))
    monkeypatch.setattr("core.orchestrator.load_framework_config", lambda p=None: fw_config)
    monkeypatch.setattr("core.orchestrator.uuid4", lambda: types.SimpleNamespace(hex="pf2"))
    monkeypatch.setattr("core.execution_backend.ContainerBackend", FakeContainerBackend)
    monkeypatch.setattr("core.orchestrator.ArtifactStore", FakeArtifactStore)
    monkeypatch.setattr("core.orchestrator.PromptLoader", FakePromptLoader)
    monkeypatch.setattr("core.orchestrator.ValidatorEngine", FakeValidatorEngine)
    monkeypatch.setattr("core.orchestrator.StateMachine", FakeStateMachine)
    monkeypatch.setattr("core.orchestrator.PhaseRunner", FakePhaseRunner)
    monkeypatch.setattr("core.orchestrator.RepairLoopEngine", FakeRepairLoopEngine)
    monkeypatch.setattr("core.orchestrator.RuleBasedMigrator", FakeMigrator)

    with pytest.raises(RuntimeError, match="preflight failed"):
        Orchestrator(session_mgr=MockSessionManager(), project_dir="/tmp/p", workflow_path="wf.yaml").run_workflow("/tmp/p")

