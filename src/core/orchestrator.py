"""Main workflow orchestrator for migration_utils."""

from __future__ import annotations

import json
import inspect
from pathlib import Path
from typing import Protocol, cast
from uuid import uuid4

from harness.session.manager import SessionManager

from core.accelerator_context import extract_accelerator_context
from core.artifact_store import ArtifactStore
from core.config import load_workflow
from core.config_loader import load_framework_config
from core.execution_backend import get_container_prompt_context, get_execution_environment_context
from core.phase_runner import PhaseRunner, SessionManagerLike as RunnerSessionManagerLike
from core.platform_policy import PlatformPolicy, resolve_policy
from core.prompt_loader import PromptLoader
from core.repair_loop import RepairLoopEngine, SessionManagerLike as RepairSessionManagerLike, get_timeout
from core.workflow_selector import is_selector_file, resolve_workflow_from_selector
from core.state_machine import StateMachine
from core.validator_engine import ValidatorEngine
from rule_strategies import create_migrator_resolved

# Kept as module-level references for test monkeypatch compatibility.
# The resolver uses importlib to instantiate migrators by strategy config.
from migrator.rule_based import RuleBasedMigrator  # noqa: F401
from migrator.rule_based_ppu import PPURuleBasedMigrator  # noqa: F401

JsonDict = dict[str, object]

_PHASE_GROUPS: tuple[frozenset[str], ...] = (
    frozenset({"phase_0", "phase_0_env_detect"}),
    frozenset({"phase_1", "phase_1_project_analysis"}),
    frozenset({"phase_2", "phase_2_venv_create"}),
    frozenset({"phase_3", "phase_3_entry_script"}),
    frozenset({"phase_35", "phase_35_static_validate"}),
    frozenset({"phase_4", "phase_4_rule_migration"}),
    frozenset({"phase_5", "phase_5_validation"}),
    frozenset({"phase_6", "phase_6_report"}),
)
_ERROR_RECOVERY_PHASES = frozenset({"error_recovery", "phase_error_recovery"})


class _Phase4RunnerWithProjectDir(Protocol):
    def __call__(
        self,
        project_dir: str,
        artifact_store: ArtifactStore,
        migrator: RuleBasedMigrator,
    ) -> dict[str, object]:
        ...


class _Phase4RunnerWithoutProjectDir(Protocol):
    def __call__(
        self,
        artifact_store: ArtifactStore,
        migrator: RuleBasedMigrator,
    ) -> dict[str, object]:
        ...


class _GetOrCreateCall(Protocol):
    def __call__(self, *, role: str, lifecycle: str) -> str:
        ...


class _SendCommandCall(Protocol):
    def __call__(self, session_id: str, command: str, timeout: int | None = None) -> str:
        ...


class _CleanupAllCall(Protocol):
    def __call__(self) -> int:
        ...


class _SessionManagerAdapter:
    backend: object

    def __init__(self, backend: object) -> None:
        self.backend = backend

    def get_or_create(self, role: str, lifecycle: str) -> str:
        get_or_create = cast(_GetOrCreateCall, getattr(self.backend, "get_or_create"))
        return get_or_create(role=role, lifecycle=lifecycle)

    def send_command(self, session_id: str, command: str, timeout: int | None = None) -> str:
        send_command = cast(_SendCommandCall, getattr(self.backend, "send_command"))
        return send_command(session_id, command, timeout=timeout)

    def cleanup_all(self) -> int:
        cleanup_all = cast(_CleanupAllCall, getattr(self.backend, "cleanup_all"))
        return cleanup_all()


class Orchestrator:
    """Wire the workflow loader, state machine, and phase engines together."""

    session_mgr: _SessionManagerAdapter
    project_dir: str
    workflow_path: str
    _fw_config: dict[str, object] | None

    def __init__(self, session_mgr: SessionManager | object, project_dir: str, workflow_path: str) -> None:
        self.session_mgr = _SessionManagerAdapter(session_mgr)
        self.project_dir = project_dir
        self.workflow_path = workflow_path
        self._fw_config = None

    def run_workflow(
        self,
        project_dir: str,
        user_command: str | None = None,
        user_constraints: str = "",
        framework_config_path: str | None = None,
    ) -> dict[str, object]:
        active_project_dir = project_dir or self.project_dir

        run_id = f"run-{uuid4().hex}"
        artifact_store = ArtifactStore(active_project_dir, run_id)
        prompt_loader = PromptLoader(str(Path(__file__).resolve().parent.parent / "prompts"))

        # ── Workflow Selector resolution (before load_workflow) ──────────
        try:
            if is_selector_file(self.workflow_path):
                project_ctx = {
                    "project_path": active_project_dir,
                    "project_name": Path(active_project_dir).name,
                    "language": "Python",
                }
                materialized = resolve_workflow_from_selector(
                    self.workflow_path,
                    self.session_mgr,
                    prompt_loader,
                    project_context=project_ctx,
                    output_dir=artifact_store.base_dir,
                )
                self._journal(
                    artifact_store,
                    phase_id="orchestrator",
                    status="selector_resolved",
                    details={
                        "selector_path": self.workflow_path,
                        "resolved_workflow_path": str(materialized),
                    },
                )
                self.workflow_path = str(materialized)
        except FileNotFoundError:
            pass  # path will be validated by load_workflow below

        workflow = load_workflow(self.workflow_path)
        fw_config = load_framework_config(framework_config_path)
        self._fw_config = fw_config
        runner_session_mgr = cast(RunnerSessionManagerLike, cast(object, self.session_mgr))
        repair_session_mgr = cast(RepairSessionManagerLike, self.session_mgr)

        validator = ValidatorEngine()
        state_machine = StateMachine(workflow)
        runner = PhaseRunner(
            runner_session_mgr,
            artifact_store,
            prompt_loader,
            validator,
            workflow=workflow,
            framework_config=fw_config,
        )
        exec_backend = self._resolve_execution_backend(workflow, active_project_dir)
        container_ctx = self._preflight_and_probe(exec_backend)
        runner.set_container_context(container_ctx)
        exec_env_ctx = self._build_execution_environment_context(exec_backend, container_ctx)
        runner.set_execution_environment_context(exec_env_ctx)
        platform_policy = resolve_policy(
            getattr(workflow, "target_platform", None),
            workflow.name,
        )
        repair_engine = RepairLoopEngine(
            repair_session_mgr, artifact_store, prompt_loader, validator,
            config=fw_config, exec_backend=exec_backend,
            platform_policy=platform_policy,
        )
        migrator = Orchestrator._select_rule_based_migrator(platform_policy, workflow)
        phase_results: dict[str, object] = {}

        result: dict[str, object] = {
            "run_id": run_id,
            "workflow_name": workflow.name,
            "workflow_version": workflow.version,
            "project_dir": active_project_dir,
            "phases": phase_results,
            "terminal_state": None,
            "success": False,
        }

        self._journal(
            artifact_store,
            phase_id="orchestrator",
            status="workflow_loaded",
            details={"workflow_path": self.workflow_path, "workflow_name": workflow.name},
        )

        sid = self.session_mgr.get_or_create(role="main_engineer", lifecycle="persistent")
        result["main_session_id"] = sid
        self._journal(
            artifact_store,
            phase_id="orchestrator",
            status="main_session_ready",
            details={"session_id": sid},
        )

        def _review_fn(repair_ctx: dict[str, object]) -> dict[str, object]:
            history = repair_ctx.get("history", [])
            repair_history = RepairLoopEngine.format_history_summary(
                cast(list[dict[str, object]], history) if isinstance(history, list) else []
            )
            return runner.run_review_check(
                review_session_id=sid,
                session_mgr=runner_session_mgr,
                project_dir=active_project_dir,
                repair_history=repair_history,
                last_artifact_path=str(repair_ctx.get("last_artifact_path", "(no artifact available)")),
                attempt_log_content=str(repair_ctx.get("attempt_log_content", "(attempt log unavailable)")),
                execution_duration=str(repair_ctx.get("execution_duration", "(not available)")),
            )

        try:
            self._journal(artifact_store, phase_id="phase_0_to_1", status="started")
            phase_0_to_1_outputs = runner.run_phase_0_to_1(
                active_project_dir,
                runner_session_mgr,
                artifact_store,
                user_constraints=user_constraints,
            )
            self._advance_success_chain(state_machine, _PHASE_GROUPS[:2])
            self._journal(artifact_store, phase_id="phase_0_to_1", status="succeeded")

            constraint_summary = ""
            if user_constraints:
                self._journal(artifact_store, phase_id="phase_1_5", status="started")
                constraint_summary = runner.run_phase_1_5(
                    sid,
                    runner_session_mgr,
                    artifact_store,
                    project_dir=active_project_dir,
                    user_constraints=user_constraints,
                    phase_1_output=phase_0_to_1_outputs.get("phase_1_project_analysis"),
                )
                self._journal(artifact_store, phase_id="phase_1_5", status="succeeded")

            self._journal(artifact_store, phase_id="phase_2_to_3", status="started")
            phase_2_to_3_outputs = runner.run_phase_2_to_3(
                active_project_dir,
                runner_session_mgr,
                artifact_store,
                prior_outputs=phase_0_to_1_outputs,
                constraint_summary=constraint_summary,
            )
            self._advance_success_chain(state_machine, _PHASE_GROUPS[2:5])
            self._journal(artifact_store, phase_id="phase_2_to_3", status="succeeded")

            phase_0_to_3_outputs = {**phase_0_to_1_outputs, **phase_2_to_3_outputs}
            phase_results.update(phase_0_to_3_outputs)
            if constraint_summary:
                phase_results["constraint_summary"] = constraint_summary

            self._journal(artifact_store, phase_id="phase_4_rule_migration", status="started")
            phase_4_output = self._run_phase_4(runner, active_project_dir, artifact_store, migrator)
            phase_results["phase_4_rule_migration"] = phase_4_output
            self._advance_success_chain(state_machine, (_PHASE_GROUPS[5],))
            self._journal(artifact_store, phase_id="phase_4_rule_migration", status="succeeded")

            framework_section: dict[str, object] = {}
            fw = fw_config.get("framework", {})
            if isinstance(fw, dict):
                framework_section = cast(dict[str, object], fw)
            review_cfg: dict[str, object] = {}
            if "review" in framework_section:
                review_raw = framework_section.get("review")
                if isinstance(review_raw, dict):
                    review_cfg = cast(dict[str, object], review_raw)
            review_raw_enabled = review_cfg.get("enabled")
            review_enabled = review_raw_enabled if isinstance(review_raw_enabled, bool) else False
            review_raw_max = review_cfg.get("max_review_iterations")
            review_max_iter = review_raw_max if isinstance(review_raw_max, int) else 3
            entry_script = self._resolve_entry_script(phase_results, artifact_store, user_command)
            phase3_contract = self._resolve_phase3_contract(phase_results, artifact_store)
            env_context = self._build_env_context(
                phase_0_to_1_outputs.get("phase_0_env_detect", {}),
                phase_2_to_3_outputs.get("phase_2_venv_create", {}),
            )
            self._journal(
                artifact_store,
                phase_id="phase_5_validation",
                status="started",
                details={"entry_script": entry_script},
            )
            phase_5_output = repair_engine.run(
                entry_script,
                active_project_dir,
                review_callable=_review_fn,
                constraint_summary=constraint_summary,
                env_context=env_context,
                enable_review_gate=review_enabled,
                max_review_iterations=review_max_iter,
                phase3_contract=phase3_contract,
            )
            phase_results["phase_5_validation"] = phase_5_output
            if not self._phase_5_succeeded(phase_5_output):
                self._journal(
                    artifact_store,
                    phase_id="phase_5_validation",
                    status="failed",
                    details={"phase_5_output": phase_5_output},
                )
                raise RuntimeError("Phase 5 validation failed: " + self._serialize(phase_5_output))
            self._advance_success_chain(state_machine, (_PHASE_GROUPS[6],))
            self._journal(artifact_store, phase_id="phase_5_validation", status="succeeded")

            self._journal(artifact_store, phase_id="phase_6_report", status="started")
            phase_6_output = runner.run_phase_6(active_project_dir, artifact_store, runner_session_mgr)
            phase_results["phase_6_report"] = phase_6_output
            self._advance_success_chain(state_machine, (_PHASE_GROUPS[7],))
            self._journal(artifact_store, phase_id="phase_6_report", status="succeeded")

            result["terminal_state"] = state_machine.current_terminal()
            result["success"] = True
            return result
        except Exception as exc:
            failed_phase = self._handle_phase_failure(
                artifact_store=artifact_store,
                prompt_loader=prompt_loader,
                state_machine=state_machine,
                session_id=sid,
                phase_outputs=phase_results,
                project_dir=active_project_dir,
                error=exc,
            )
            result["failed_phase"] = failed_phase
            result["error"] = str(exc)
            result["terminal_state"] = state_machine.current_terminal()
            return result
        finally:
            cleaned = self.session_mgr.cleanup_all()
            self._cleanup_execution_backend(exec_backend)
            self._journal(
                artifact_store,
                phase_id="orchestrator",
                status="cleanup_completed",
                details={"cleaned_sessions": cleaned},
            )

    def _run_phase_4(
        self,
        runner: PhaseRunner,
        project_dir: str,
        artifact_store: ArtifactStore,
        migrator: object,
    ) -> dict[str, object]:
        phase_4_runner = runner.run_phase_4
        phase_4_signature = inspect.signature(phase_4_runner)
        if len(phase_4_signature.parameters) == 3:
            return cast(_Phase4RunnerWithProjectDir, phase_4_runner)(project_dir, artifact_store, migrator)
        return cast(_Phase4RunnerWithoutProjectDir, phase_4_runner)(artifact_store, migrator)

    @staticmethod
    def _select_rule_based_migrator(platform_policy: PlatformPolicy, workflow: object | None = None) -> object:
        """Select the appropriate rule-based migrator using configuration-driven resolution.

        Uses the strategy resolver with precedence:
        1. Workflow YAML ``params.backend`` (legacy)
        2. Workflow YAML ``rule_migration.strategy`` (new)
        3. ``PlatformPolicy.default_rule_migration_strategy``
        4. ``report_only`` safe fallback

        This keeps the legacy Orchestrator path aligned with WorkflowExecutor.
        """
        backend = Orchestrator._phase_4_backend_from_workflow(workflow)
        workflow_rule_migration = getattr(workflow, "rule_migration", None) if workflow is not None else None
        return create_migrator_resolved(
            workflow_params_backend=backend,
            workflow_rule_migration=workflow_rule_migration if isinstance(workflow_rule_migration, dict) else None,
            platform_policy_strategy=platform_policy.default_rule_migration_strategy,
        )

    @staticmethod
    def _phase_4_backend_from_workflow(workflow: object | None) -> str | None:
        phases = getattr(workflow, "phases", None)
        if not isinstance(phases, list):
            return None
        for phase in phases:
            operation = getattr(phase, "params", {}).get("operation") if isinstance(getattr(phase, "params", None), dict) else None
            phase_operation = getattr(phase, "operation", None) or operation
            is_rule_phase = getattr(phase, "id", "") == "phase_4_rule_migration" or phase_operation == "rule_based_migration"
            params = getattr(phase, "params", None)
            if is_rule_phase and isinstance(params, dict):
                backend = params.get("backend")
                if isinstance(backend, str) and backend.strip():
                    return backend.strip()
        return None

    @staticmethod
    def _phase_5_succeeded(phase_5_output: dict[str, object]) -> bool:
        if phase_5_output.get("success") is not True:
            return False
        status = str(phase_5_output.get("status", "")).strip().lower()
        return status in {"success", "succeeded", "pass", "passed"}

    def _handle_phase_failure(
        self,
        *,
        artifact_store: ArtifactStore,
        prompt_loader: PromptLoader,
        state_machine: StateMachine,
        session_id: str,
        phase_outputs: dict[str, object],
        project_dir: str,
        error: Exception,
    ) -> str:
        error_text = str(error)
        failed_phase = self._infer_failed_phase(state_machine.current_phase, error_text)
        failed_group = self._phase_group_for(failed_phase)
        self._advance_to_group(state_machine, failed_group)
        transition_target = self._force_failure_transition(state_machine, error_text)

        self._journal(
            artifact_store,
            phase_id=failed_phase,
            status="failed",
            details={"error": error_text, "transition_target": transition_target or ""},
        )

        if transition_target in _ERROR_RECOVERY_PHASES:
            recovery_output = self._run_error_recovery(
                artifact_store=artifact_store,
                prompt_loader=prompt_loader,
                session_id=session_id,
                phase_outputs=phase_outputs,
                project_dir=project_dir,
                failed_phase=failed_phase,
                failure_log=error_text,
            )
            phase_outputs["phase_error_recovery"] = recovery_output
            if state_machine.current_phase in _ERROR_RECOVERY_PHASES:
                _ = state_machine.record_success(state_machine.current_phase)
        return failed_phase

    def _run_error_recovery(
        self,
        *,
        artifact_store: ArtifactStore,
        prompt_loader: PromptLoader,
        session_id: str,
        phase_outputs: dict[str, object],
        project_dir: str,
        failed_phase: str,
        failure_log: str,
        env_context: dict[str, object] | None = None,
    ) -> JsonDict:
        self._journal(artifact_store, phase_id="phase_error_recovery", status="started")
        constraint_summary_val = phase_outputs.get("constraint_summary", "")
        if isinstance(constraint_summary_val, dict):
            constraint_summary_dict = cast(dict[str, object], constraint_summary_val)
            constraint_summary_val = str(constraint_summary_dict.get("constraint_summary", ""))

        env_context_val = env_context or {}
        if not env_context_val:
            p0 = phase_outputs.get("phase_0_env_detect", {})
            p2 = phase_outputs.get("phase_2_venv_create", {})
            phase_0_output = cast(dict[str, object], p0) if isinstance(p0, dict) else {}
            phase_2_output = cast(dict[str, object], p2) if isinstance(p2, dict) else {}
            env_context_val = self._build_env_context(
                phase_0_output,
                phase_2_output,
            )
        if isinstance(env_context_val, str):
            env_context_val = {}

        prompt = prompt_loader.load_prompt(
            "phase_error_recovery",
            {
                "phase_name": "phase_error_recovery",
                "project_dir": project_dir,
                "failed_phase": failed_phase,
                "previous_outputs": self._serialize(phase_outputs),
                "failure_log": failure_log,
                "constraint_summary": str(constraint_summary_val),
                "last_review": "(No review available)",
                "env_context": self._serialize(env_context_val),
            },
        )
        memo = self.session_mgr.send_command(session_id, prompt, timeout=get_timeout(getattr(self, '_fw_config', None), "session_timeout_repair"))
        recovery_output: JsonDict = {
            "failed_phase": failed_phase,
            "failure_log": failure_log,
            "recovery_memo": memo,
        }
        raw_path = artifact_store.save_phase_output("phase_error_recovery", recovery_output, attempt=1)
        canonical_path = artifact_store.mark_validated("phase_error_recovery", recovery_output)
        self._journal(
            artifact_store,
            phase_id="phase_error_recovery",
            status="succeeded",
            details={"raw_path": raw_path, "canonical_path": canonical_path},
        )
        return recovery_output

    def _resolve_phase3_contract(
        self,
        phase_outputs: dict[str, object],
        artifact_store: ArtifactStore,
    ) -> dict[str, object] | None:
        phase_3_output = phase_outputs.get("phase_3_entry_script")
        if not isinstance(phase_3_output, dict):
            loaded_output = artifact_store.load_phase_output("phase_3_entry_script")
            if isinstance(loaded_output, dict):
                phase_3_output = loaded_output
        if not isinstance(phase_3_output, dict):
            return None
        return dict(cast(dict[str, object], phase_3_output))

    def _resolve_entry_script(
        self,
        phase_outputs: dict[str, object],
        artifact_store: ArtifactStore,
        user_command: str | None,
    ) -> str:
        if user_command:
            return user_command

        phase_3_output = phase_outputs.get("phase_3_entry_script")
        if not isinstance(phase_3_output, dict):
            loaded_output = artifact_store.load_phase_output("phase_3_entry_script")
            if isinstance(loaded_output, dict):
                phase_3_output = loaded_output

        if isinstance(phase_3_output, dict):
            phase_3_output_dict = cast(dict[str, object], phase_3_output)
            run_command = phase_3_output_dict.get("run_command")
            if isinstance(run_command, str) and run_command.strip():
                return run_command

        raise ValueError("Phase 5 requires a non-empty run_command from Phase 3 or user_command")

    def _advance_success_chain(
        self,
        state_machine: StateMachine,
        phase_groups: tuple[frozenset[str], ...],
    ) -> None:
        for phase_group in phase_groups:
            current_phase = state_machine.current_phase
            if current_phase is None or current_phase not in phase_group:
                break
            _ = state_machine.record_success(current_phase)

    def _advance_to_group(self, state_machine: StateMachine, target_group: frozenset[str]) -> None:
        while state_machine.current_phase is not None and state_machine.current_phase not in target_group:
            current_phase = state_machine.current_phase
            if current_phase in _ERROR_RECOVERY_PHASES:
                break
            _ = state_machine.record_success(current_phase)

    def _force_failure_transition(self, state_machine: StateMachine, error_text: str) -> str | None:
        current_phase = state_machine.current_phase
        if current_phase is None:
            return state_machine.current_terminal()

        keep_retrying = True
        next_target: str | None = None
        while keep_retrying and state_machine.current_phase is not None:
            keep_retrying, next_target = state_machine.record_failure(current_phase, error_text)
        return next_target

    def _infer_failed_phase(self, current_phase: str | None, error_text: str) -> str:
        for phase_group in _PHASE_GROUPS:
            for phase_id in phase_group:
                if phase_id in error_text:
                    return phase_id
        return current_phase or "unknown_phase"

    @staticmethod
    def _phase_group_for(phase_id: str) -> frozenset[str]:
        for phase_group in _PHASE_GROUPS:
            if phase_id in phase_group:
                return phase_group
        return frozenset({phase_id})

    @staticmethod
    def _build_env_context(
        phase_0_output: dict[str, object],
        phase_2_output: dict[str, object],
    ) -> dict[str, object]:
        env = dict(phase_0_output)
        installed = phase_2_output.get("installed_packages", [])
        accel_ctx = extract_accelerator_context(installed)
        env["torch_npu_version"] = accel_ctx["torch_npu_version"]
        env["accelerator_packages"] = accel_ctx["accelerator_packages"]
        env["accelerator_package_versions"] = accel_ctx["accelerator_package_versions"]
        return env

    @staticmethod
    def _serialize(value: object) -> str:
        return value if isinstance(value, str) else json.dumps(value, indent=2, ensure_ascii=False, default=str)

    def _resolve_execution_backend(
        self,
        workflow: object,
        project_dir: str,
    ) -> object:
        """Create an execution backend from workflow config, mirroring WorkflowExecutor behavior."""
        eb_cfg = getattr(workflow, "execution_backend", None)
        if eb_cfg is None or eb_cfg.mode == "local":
            return None

        from core.execution_backend import ContainerBackend, auto_select_backend

        if eb_cfg.mode == "auto":
            eb_cfg = auto_select_backend(eb_cfg)
            eb_cfg = self._auto_select_image(eb_cfg)

        if eb_cfg.mode != "container":
            return None

        backend = ContainerBackend(eb_cfg)
        backend.set_project_dir(project_dir)
        return backend

    def _auto_select_image(self, config: object) -> object:
        """Run agent image-selection for ``mode=auto`` before container creation."""
        from core.execution_backend import ContainerBackend
        from core.types import ExecutionBackendConfig as _EBC

        if getattr(config, "mode", None) != "container":
            return config

        candidates: list[str] = []
        is_discovered = False

        cfg_list = getattr(config, "images", None) or []
        # Normalize: filter out None/"None" artifacts
        candidates = [c for c in cfg_list if str(c).strip() and str(c).strip() != "None"]

        if len(candidates) == 1:
            return config

        if not candidates:
            try:
                probe = ContainerBackend(config)
                discovered = probe._discover_local_images()
            except Exception as exc:
                import logging
                logging.getLogger(__name__).warning("Auto image discovery failed: %s", exc)
                discovered = []

            if discovered:
                candidates = discovered
                is_discovered = True
            else:
                import logging
                logging.getLogger(__name__).info("Auto mode: no images and no local images discovered; falling back to local")
                return _EBC(mode="local")

        selected = self._send_image_selection_prompt(candidates, is_discovered)
        if selected and selected in candidates:
            ordered_candidates = [selected] + [img for img in candidates if img != selected]
            config = _EBC(
                mode=config.mode,
                source=config.source,
                runtime=config.runtime,
                image=selected,
                images=ordered_candidates,
                container_name=config.container_name,
                container_name_prefix=config.container_name_prefix,
                devices=config.devices,
                volumes=config.volumes,
                env_vars=config.env_vars,
                required_env_vars=config.required_env_vars,
                required_devices=config.required_devices,
                container_workdir=config.container_workdir,
                network_mode=config.network_mode,
                runtime_flags=config.runtime_flags,
                timeout=config.timeout,
                cleanup=config.cleanup,
            )
            import logging
            logging.getLogger(__name__).info("Auto image selection chosen: %s", selected)
        else:
            import logging
            logging.getLogger(__name__).warning(
                "Auto image selection returned invalid value %r; falling back to local",
                selected,
            )
            return _EBC(mode="local")

        return config

    def _send_image_selection_prompt(
        self,
        candidates: list[str],
        is_discovered: bool = False,
    ) -> str | None:
        """Ask the main engineer session to select an image from the list."""
        from core.prompt_loader import PromptLoader
        from harness.session.manager import extract_json_response as _extract

        prompts_dir = (
            Path(__file__).resolve().parent.parent / "prompts"
        )
        prompt_loader = PromptLoader(prompts_dir)

        candidates_text = "\n".join(f"  {i+1}. {img}" for i, img in enumerate(candidates))

        guidance = (
            "Select the most appropriate image for running the migration workflow. "
            "Consider image suitability for Python, PyTorch, and target hardware."
        )
        if is_discovered:
            guidance = (
                "These are the images already available on the host. "
                + guidance
            )

        prompt_text = prompt_loader.load_prompt(
            "container_image_select",
            {
                "candidate_images": candidates_text,
                "discovered_images_section": (
                    "## Discovered Local Images\nThe following images were found on this host:\n"
                    + candidates_text
                    if is_discovered
                    else ""
                ),
                "selection_guidance": guidance,
            },
        )

        sid = self.session_mgr.get_or_create(role="main_engineer", lifecycle="persistent")
        try:
            raw = self.session_mgr.send_command(sid, prompt_text, timeout=120)
            parsed = _extract(raw)
            if isinstance(parsed, dict):
                selected = parsed.get("selected_image")
                return str(selected) if selected else None
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("Image selection prompt failed: %s", exc)

        return None

    @staticmethod
    def _preflight_and_probe(backend: object) -> dict[str, str]:
        if backend is None:
            return {}
        preflight = getattr(backend, "preflight", None)
        if callable(preflight):
            preflight()
        probe_fn = getattr(backend, "probe_environment", None)
        probe_facts: dict[str, object] | None = None
        if callable(probe_fn):
            probe_facts = probe_fn()
        return get_container_prompt_context(backend, probe_facts)

    @staticmethod
    def _build_execution_environment_context(backend: object, container_ctx: dict[str, str] | None = None) -> str:
        from core.execution_backend import LocalBackend as _LocalBackend
        probe_facts: dict[str, object] | None = None
        if container_ctx and "container_env_facts" in container_ctx:
            import json
            try:
                probe_facts = json.loads(container_ctx["container_env_facts"])
            except (json.JSONDecodeError, TypeError):
                pass
        if backend is None or isinstance(backend, _LocalBackend):
            return get_execution_environment_context(None, probe_facts)
        return get_execution_environment_context(backend, probe_facts or {})

    @staticmethod
    def _cleanup_execution_backend(backend: object) -> None:
        if backend is None:
            return
        try:
            backend.cleanup()
        except Exception as exc:
            # Cleanup failures are logged, never crash the workflow.
            import logging
            logger = logging.getLogger(__name__)
            logger.error("Execution backend cleanup failed: %s", exc)

    @staticmethod
    def _journal(
        artifact_store: ArtifactStore,
        *,
        phase_id: str,
        status: str,
        details: dict[str, object] | None = None,
    ) -> None:
        _ = artifact_store.write_journal(
            {
                "phase_id": phase_id,
                "status": status,
                "attempt": 0,
                "session_ref": "orchestrator",
                "raw_path": "",
                "canonical_path": "",
                "errors": [],
                "warnings": [],
                "details": details or {},
            }
        )


__all__ = ["Orchestrator"]
