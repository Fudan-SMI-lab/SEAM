"""YAML-driven workflow execution engine with 7 phase types, condition evaluation,
transitions, hooks, telemetry, loop engine, review gate, dispatch routing,
variable passing, and stagnation detection."""

from __future__ import annotations

import json
import logging
import importlib
import inspect
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
import traceback
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

from core.types import (
    PhaseDefinition,
    WorkflowDefinition,
    PhaseHooks,
    SubWorkflowDefinition,
    TransitionDefinition,
    PhaseType,
    HookDefinition,
    HookResult,
    RuntimeSkillsConfig,
    ExecutionBackendConfig,
)
from core.runtime_skill_resolver import RuntimeSkillBundle, RuntimeSkillResolver
from core.variable_resolver import VariableResolver
from core.session_registry import SessionRegistry
from core.accelerator_context import extract_accelerator_context
from core.hook_manager import HookManager
from core.paths import resolve_relative_path, workspace_root
from core.custom_op_opp_preflight import ensure_opp_source_evidence, format_custom_op_opp_preflight_failure, has_custom_op_contract, validate_custom_op_opp_preflight
from core.custom_op_variants import (
    EXPANDED_VARIANT_CONTRACT_FIELDS,
    apply_expanded_variant_contract,
    ensure_strict_expanded_variant_validation_script,
    ensure_strict_non_variant_custom_op_validation_script,
    expanded_variant_contract_from_outputs,
    normalize_phase1_project_analysis,
)
from core.routes import (
    CUSTOM_OP,
    CUSTOM_OP_WITH_VARIANTS,
    SERVING_ROUTES,
    normalize_serving_phase1_surface,
    normalize_serving_phase3_contract,
    serving_route_from_contract,
)
from core.phase_boundary import inject_phase_boundary
from core.execution_backend import ContainerBackend, get_execution_context as _get_exec_ctx, get_execution_environment_context as _get_exec_env_ctx
from harness.session.manager import extract_json_response
from migrator.rule_based import RuleBasedMigrator
from migrator.rule_based_ppu import PPURuleBasedMigrator
from migrator.rule_based_report_only import ReportOnlyRuleBasedMigrator
from core.runtime_artifacts import write_operator_repair_context_artifact, write_repair_runtime_artifacts
from core.phase6_fallback import build_phase6_fallback_report, collect_phase6_prior_artifacts, collect_phase6_prior_state, resolve_phase6_timeout
from core.validation_correction import (
    build_validation_correction_prompt,
    expected_output_format,
    extract_output_format_from_prompt,
    extract_missing_fields,
)
from core.repair_loop import (
    _operator_custom_op_guidance,
    _operator_custom_op_progress_block,
    _operator_generic_guidance,
    _operator_repair_has_custom_op_contract,
    force_custom_op_operator_routing_if_needed,
)
from core.platform_policy import resolve_policy, PlatformPolicy
from validators.validate_entry_script import validate as validate_entry_script, _extract_env_prefix
from validators.validate_validation_final import (
    validate_custom_op_final_gate,
    validate_serving_final_gate,
)
from rule_strategies import create_migrator_resolved, resolve_rule_migration_strategy

logger = logging.getLogger(__name__)
_CUSTOM_OP_GATE_REPORT_MAX_BYTES = 5 * 1024 * 1024

SUB_WORKFLOW_REPAIR_PHASE_IDS = {
    "fix_dependency",
    "fix_code",
    "fix_operator",
    "imp_fix_dependency",
    "imp_fix_code",
    "imp_fix_operator",
}
SUB_WORKFLOW_REPAIR_PHASE_ORDER = (
    "fix_dependency",
    "fix_code",
    "fix_operator",
    "imp_fix_dependency",
    "imp_fix_code",
    "imp_fix_operator",
)
SUB_WORKFLOW_REPAIR_TIMEOUT_DEFAULT: int | None = 4 * 60 * 60
SUB_WORKFLOW_ANALYZE_TIMEOUT_DEFAULT: int | None = SUB_WORKFLOW_REPAIR_TIMEOUT_DEFAULT
RETRYABLE_SUB_WORKFLOW_SESSION_ERRORS = {
    "empty session response",
    "compaction response is incomplete",
}
CUSTOM_OP_OPERATOR_STAGNATION_THRESHOLD_DEFAULT = 100


def _rewrite_container_to_host_path(
    path_str: str,
    project_dir: str,
    container_workdir: str,
) -> str:
    """Convert a container-visible path to its host-visible equivalent."""
    if not path_str:
        return path_str
    safe = container_workdir.rstrip("/")
    if not safe:
        return path_str
    if not (path_str == safe or path_str.startswith(safe + "/")):
        return path_str
    rel = path_str[len(safe):].lstrip("/")
    if not rel:
        return project_dir
    return str(Path(project_dir) / rel)


CUSTOM_OP_REQUIRED_TERMS = (
    "custom_op",
    "custom-op",
    "custom operator",
    "custom operators",
    "自定义算子",
    "CUDAExtension",
    "cpp_extension",
    "ctypes.CDLL",
    "torch.ops",
    "pybind",
    "custom_op_full_validation",
)

CUSTOM_OP_NEGATIVE_PATTERNS = (
    re.compile(r"\bno\s+(?:cuda\s+|c\+\+\s+|cpp\s+)?custom[-_\s]+operators?\b", re.IGNORECASE),
    re.compile(r"\bno\s+custom[-_\s]+operators?\s+(?:found|detected|present)\b", re.IGNORECASE),
    re.compile(r"\bcustom[-_\s]+operators?\s*[:=]\s*(?:false|none|no)\b", re.IGNORECASE),
    re.compile(r"\bcustom_op_detected\s*[:=]\s*false\b", re.IGNORECASE),
)

CUSTOM_OP_CONTRACT_KEYS = frozenset(
    {
        "entry_script_kind",
        "reports_dir",
        "required_report_paths",
        "required_checks",
        "operator_discovery_sources",
        "operator_inventory_schema",
        "performance_report_schema",
        "validation_obligations",
        "phase5_entry_script_revision_allowed",
    }
)


class SessionCommandError(RuntimeError):
    def __init__(self, message: str, payload: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.payload = payload or {"ok": False, "error": message}

# ---------------------------------------------------------------------------
# Safe boolean-expression evaluator (no exec/eval of untrusted code)
# ---------------------------------------------------------------------------

_ALLOWED_OPS = frozenset(
    ("==", "!=", ">", "<", ">=", "<=", "and", "or", "not", "in")
)


def _safe_eval_bool(expr: str, env: dict[str, Any]) -> bool:
    """Evaluate a simple boolean expression using a restricted tokenizer.

    Supports: comparison operators, logical and/or/not, membership (in),
    parentheses, string/number/bool literals, and variable references (resolved
    from *env*).

    Grammar (simplified):
        expr  := term ( ('and'|'or') term )*
        term  := 'not' term | comparison
        comparison := primary ( ('=='|'!='|'>'|'<'|'>='|'<='|'in') primary )?
        primary := '(' expr ')' | literal | IDENT
    """
    tokens = _tokenize(expr)
    pos = 0

    def peek() -> str | None:
        return tokens[pos] if pos < len(tokens) else None

    def consume(expected: str | None = None) -> str:
        nonlocal pos
        tok = tokens[pos]
        pos += 1
        if expected and tok != expected:
            raise ValueError(f"Expected '{expected}', got '{tok}'")
        return tok

    def parse_expr() -> Any:
        left = parse_term()
        while peek() in ("and", "or"):
            op = consume()
            right = parse_term()
            if op == "and":
                left = bool(left) and bool(right)
            else:
                left = bool(left) or bool(right)
        return left

    def parse_term() -> Any:
        if peek() == "not":
            consume()
            val = parse_term()
            return not bool(val)
        return parse_comparison()

    def parse_comparison() -> Any:
        left = parse_primary()
        op = peek()
        if op in ("==", "!=", ">", "<", ">=", "<=", "in"):
            consume()
            right = parse_primary()
            if op == "==":
                return left == right
            if op == "!=":
                return left != right
            if op == ">":
                return left > right
            if op == "<":
                return left < right
            if op == ">=":
                return left >= right
            if op == "<=":
                return left <= right
            if op == "in":
                return left in right
        return left

    def parse_primary() -> Any:
        tok = peek()
        if tok == "(":
            consume("(")
            val = parse_expr()
            consume(")")
            return val
        # Literals
        if tok is None:
            raise ValueError("Unexpected end of expression")
        if tok == "true":
            consume()
            return True
        if tok == "false":
            consume()
            return False
        if tok == "null" or tok == "none":
            consume()
            return None
        # Number
        try:
            float(tok)
            consume()
            v = float(tok)
            return int(v) if v == int(v) else v
        except (ValueError, TypeError):
            pass
        # Quoted string
        if (tok.startswith('"') and tok.endswith('"')) or \
           (tok.startswith("'") and tok.endswith("'")):
            consume()
            return tok[1:-1]
        # Variable lookup
        consume()
        if tok in env:
            return env[tok]
        return tok  # fallback: treat as string

    if not tokens:
        return False
    result = parse_expr()
    return bool(result)


def _tokenize(expr: str) -> list[str]:
    """Split a boolean expression into tokens."""
    tokens: list[str] = []
    i = 0
    expr = expr.strip()
    while i < len(expr):
        # Skip whitespace
        if expr[i].isspace():
            i += 1
            continue
        # Quoted strings
        if expr[i] in ('"', "'"):
            quote = expr[i]
            j = i + 1
            while j < len(expr) and expr[j] != quote:
                if expr[j] == '\\':
                    j += 1
                j += 1
            tokens.append(expr[i:j + 1])
            i = j + 1
            continue
        # Multi-char operators
        if expr[i:i + 2] in ('==', '!=', '>=', '<='):
            tokens.append(expr[i:i + 2])
            i += 2
            continue
        # Single-char operators / parens
        if expr[i] in ('(', ')', '>', '<'):
            tokens.append(expr[i])
            i += 1
            continue
        # Words / numbers
        j = i
        while j < len(expr) and (expr[j].isalnum() or expr[j] in '._-'):
            j += 1
        if j > i:
            tokens.append(expr[i:j])
            i = j
            continue
        # Skip unknown chars
        i += 1
    return tokens


class WorkflowExecutor:
    """Core YAML-driven workflow execution engine.

    Supports 7 phase types: llm, shell, builtin, python, review, dispatch, loop.
    Handles condition evaluation, transitions, hooks, telemetry, loop engine,
    review gate, dispatch routing, variable passing, and stagnation detection.
    """

    # ── Constructor ─────────────────────────────────────────────────────

    def __init__(
        self,
        workflow: WorkflowDefinition,
        session_mgr,
        artifact_store,
        prompt_loader,
        validator_engine,
        telemetry_observer: Any = None,
        framework_config: dict[str, Any] | None = None,
        project_dir: str = ".",
        output_dir: str = ".",
        user_constraints: str = "",
        telemetry_bridge: Any = None,
        hook_manager: HookManager | None = None,
        experience_store=None,
        exec_backend: Any = None,
    ) -> None:
        self.workflow = workflow
        self.session_mgr = session_mgr
        self.artifact_store = artifact_store
        self.prompt_loader = prompt_loader
        self.validator_engine = validator_engine
        self.project_dir = project_dir
        self.output_dir = output_dir
        self.user_constraints = user_constraints
        self.framework_config = framework_config or {}
        self.resolver = VariableResolver()
        self.session_registry: SessionRegistry | None = (
            SessionRegistry(workflow.agents, session_mgr) if workflow.agents else None
        )
        self.hook_manager = hook_manager or HookManager(workflow.hooks, output_dir=output_dir)
        self.telemetry_bridge = telemetry_bridge
        self.telemetry_observer = telemetry_observer
        self.experience_store = experience_store
        self.exec_backend = exec_backend
        self._initialize_execution_backend()
        self._container_env_probe = getattr(self, "_container_env_probe", None)
        self._runtime_skill_resolver: RuntimeSkillResolver | None = None

        # Resolve platform policy from workflow definition
        self.platform_policy: PlatformPolicy = resolve_policy(
            getattr(workflow, "target_platform", None),
            workflow.name,
        )

        # Execution state
        self.phase_results: dict[str, dict[str, Any]] = {}   # phase_id -> {status, duration, ...}
        self.state: dict[str, dict[str, Any]] = {}           # phase_id -> canonical output
        self.phase_index: dict[str, int] = {}      # phase_id -> index in workflow.phases

        for i, p in enumerate(self.workflow.phases or []):
            self.phase_index[p.id] = i

    def _initialize_execution_backend(self) -> None:
        """Create execution backend from workflow config when needed."""
        if self.exec_backend is not None:
            return
        eb = getattr(self.workflow, "execution_backend", None)
        if eb is None or eb.mode == "local":
            return

        from core.execution_backend import (
            ContainerBackend,
            auto_select_backend,
        )

        if eb.mode == "auto":
            eb = auto_select_backend(eb)
            # Auto image selection before container creation
            eb = self._auto_select_image(eb)

        if eb.mode != "container":
            return

        backend = ContainerBackend(eb)
        backend.set_project_dir(self.project_dir)
        if not backend.is_available():
            runtime_cmd = getattr(backend, "_runtime_cmd", eb.runtime)
            self._container_env_probe = {
                "status": "container_runtime_unavailable",
                "runtime": eb.runtime,
                "runtime_cmd": runtime_cmd,
                "deferred_until_execution": True,
                "error": (
                    f"Container runtime command '{runtime_cmd}' is not available; "
                    "Phase 0/1 analysis may continue, but container execution will fail "
                    "until the runtime is installed or exposed on PATH."
                ),
            }
            self.exec_backend = backend
            logger.warning(
                "Container runtime %s is unavailable; deferring hard failure until container execution",
                runtime_cmd,
            )
            return
        backend.preflight()
        self._container_env_probe = backend.probe_environment()
        self.exec_backend = backend

    def _auto_select_image(
        self, config: ExecutionBackendConfig,
    ) -> ExecutionBackendConfig:
        """Run agent image-selection for ``mode=auto`` before container creation."""

        if config.mode != "container":
            return config

        candidates: list[str] = []
        is_discovered = False
        cfg_list = getattr(config, "images", None) or []

        # Normalize: filter out None/"None" artifacts
        candidates = [c for c in cfg_list if str(c).strip() and str(c).strip() != "None"]

        # Multiple configured candidates → always do agent selection
        # Single configured candidate → no selection needed
        if len(candidates) == 1:
            return config

        if not candidates:
            try:
                probe = ContainerBackend(config)
                discovered = probe._discover_local_images()
            except Exception as exc:
                logger.warning("Auto image discovery failed: %s", exc)
                discovered = []

            if discovered:
                candidates = discovered
                is_discovered = True
            else:
                logger.info("Auto mode: no configured images and no local images discovered; falling back to local")
                return ExecutionBackendConfig(mode="local")

        # Send selection prompt to agent
        selected = self._send_image_selection_prompt(candidates, is_discovered)
        if selected and selected in candidates:
            config = ExecutionBackendConfig(
                mode=config.mode,
                source=config.source,
                runtime=config.runtime,
                image=selected,
                images=candidates,
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
            logger.info("Auto image selection chosen: %s", selected)
        else:
            logger.warning(
                "Auto image selection returned invalid value %r; falling back to local",
                selected,
            )
            return ExecutionBackendConfig(mode="local")

        return config

    def _send_image_selection_prompt(
        self,
        candidates: list[str],
        is_discovered: bool = False,
    ) -> str | None:
        """Ask an agent to select an image from the given list."""
        from harness.session.manager import extract_json_response as _extract

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

        prompt_text = self.prompt_loader.load_prompt(
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

        # Determine which session to use
        agent_id = "main_engineer"
        try:
            if self.session_registry:
                sid = self.session_registry.resolve(agent_id)
            else:
                sid = self.session_mgr.get_or_create(
                    role="image_selector", lifecycle="ephemeral"
                )
        except KeyError:
            sid = self.session_mgr.get_or_create(
                role="image_selector", lifecycle="ephemeral"
            )

        try:
            raw = self.session_mgr.send_command(sid, prompt_text, timeout=120)
            parsed = _extract(raw)
            if isinstance(parsed, dict):
                selected = parsed.get("selected_image")
                return str(selected) if selected else None
        except Exception as exc:
            logger.warning("Image selection prompt failed: %s", exc)

        return None

    def _cleanup_execution_backend(self) -> None:
        if self.exec_backend is None:
            return
        try:
            self.exec_backend.cleanup()
        except Exception as exc:
            logger.error("Execution backend cleanup failed: %s", exc)

    def _set_telemetry_active_phase(self, phase_id: str | None) -> None:
        setter = getattr(self.telemetry_observer, "set_active_phase", None)
        if callable(setter):
            setter(phase_id)

    # ── Main entry point ────────────────────────────────────────────────

    def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        """Execute the full workflow lifecycle.

        Args:
            context: User-supplied context dict.

        Returns:
            Dict with keys: state, phase_results, status.
        """
        # 1. Merge defaults
        ctx: dict[str, Any] = {
            "PROJECT_DIR": self.project_dir,
            "USER_CONSTRAINTS": self.user_constraints,
        }
        ctx.update(context)

        # 2. workflow_start hooks
        try:
            self.hook_manager.execute("workflow_start", ctx)
        except Exception as exc:
            logger.error("workflow_start hook failed: %s", exc)

        # 3. Iterate through phases
        phases = self.workflow.phases or []
        terminals = set(self.workflow.terminals or [])
        current_phase_id: str | None = phases[0].id if phases else None

        while current_phase_id and current_phase_id not in terminals:
            phase = self._find_phase_by_id(current_phase_id)
            if phase is None:
                logger.warning("Phase '%s' not found, terminating.", current_phase_id)
                break

            logger.info(">>> Executing phase: %s (%s)", phase.id, phase.type)

            # Skip Phase 7 when experience.phase7_enabled is false
            if phase.id in ("phase_7a_evaluate", "phase_7b_refine"):
                p7_cfg = getattr(getattr(self.workflow, 'experience', None), 'phase7_enabled', True)
                if not p7_cfg:
                    logger.info("Phase '%s' skipped (phase7_enabled=false)", phase.id)
                    self.phase_results[phase.id] = {
                        "status": "skipped",
                        "duration": 0,
                        "reason": "phase7_disabled",
                    }
                    idx = self.phase_index.get(phase.id, -1)
                    phases_list = self.workflow.phases or []
                    if idx >= 0 and idx + 1 < len(phases_list):
                        current_phase_id = phases_list[idx + 1].id
                    else:
                        current_phase_id = "complete"
                    continue

            # Evaluate condition
            if phase.condition:
                cond_met = self._evaluate_condition(
                    phase.condition, self.state, ctx
                )
                if not cond_met:
                    logger.info("Phase '%s' condition FALSE → skipped", phase.id)
                    self.phase_results[phase.id] = {
                        "status": "skipped",
                        "duration": 0,
                        "reason": "condition_false",
                    }
                    next_id = self._get_next_phase_id(phase, "skipped", self.state, ctx)
                    current_phase_id = next_id
                    continue

            # Execute phase based on type
            phase_type = (phase.type or "llm").lower()
            start_t = time.time()
            status: str = "success"
            output: Any = {}
            self._set_telemetry_active_phase(phase.id)

            try:
                if phase_type == "llm":
                    status, output = self._execute_llm_phase(phase, self.state, ctx)
                elif phase_type == "shell":
                    status, output = self._execute_shell_phase(phase, self.state, ctx)
                elif phase_type == "builtin":
                    status, output = self._execute_builtin_phase(phase, self.state, ctx)
                elif phase_type == "python":
                    status, output = self._execute_python_phase(phase, self.state, ctx)
                elif phase_type == "review":
                    result = self._execute_review_phase(
                        phase, self.state, ctx,
                        loop_vars={}, loop_state={}, loop_history=[],
                        sub_workflow_def=None, verdicts_cfg={},
                    )
                    status = result.get("status", "success")
                    output = result
                elif phase_type == "dispatch":
                    next_id = self._execute_dispatch_phase(
                        phase, self.state, ctx,
                        loop_vars={}, loop_state={}, step_outputs={},
                    )
                    if next_id:
                        current_phase_id = next_id
                        self.phase_results[phase.id] = {
                            "status": "dispatched",
                            "duration": time.time() - start_t,
                            "target": next_id,
                        }
                        self._set_telemetry_active_phase(None)
                        continue
                    status = "success"
                    output = {"dispatched_to": None}
                elif phase_type == "loop":
                    result = self._execute_loop_phase(phase, self.state, ctx)
                    status = result.get("status", "success")
                    output = result
                elif phase_type == "orchestration":
                    result = self._execute_orchestration_phase(phase, self.state, ctx)
                    status = result.get("status", "success")
                    output = result
                else:
                    logger.warning("Unknown phase type '%s' for phase '%s'", phase_type, phase.id)
                    status = "failure"
                    output = {"error": f"unknown_phase_type:{phase_type}"}

            except Exception as exc:
                logger.exception("Phase '%s' raised exception: %s", phase.id, exc)
                status = "failure"
                output = {"error": str(exc), "traceback": traceback.format_exc()}

            duration = time.time() - start_t

            # Record results
            self.phase_results[phase.id] = {
                "status": status,
                "duration": round(duration, 3),
                "output_summary": str(output)[:500] if output else "",
            }

            # Update state
            if isinstance(output, dict):
                key = phase.output_as or phase.id
                self.state[key] = output

            # Save to artifact store
            if isinstance(output, dict) and status == "success":
                try:
                    self.artifact_store.save_phase_output(phase.id, output)
                    self.artifact_store.mark_validated(phase.id, output)
                except Exception as exc:
                    logger.warning("Failed to save artifact for %s: %s", phase.id, exc)

            # Journal entry
            try:
                self.artifact_store.write_journal({
                    "phase_id": phase.id,
                    "status": status,
                    "duration": duration,
                    "timestamp": time.time(),
                })
            except Exception:
                pass

            # Determine next phase
            next_id = self._get_next_phase_id(phase, status, self.state, ctx)
            self._set_telemetry_active_phase(None)
            current_phase_id = next_id

        self._set_telemetry_active_phase(None)

        # 4. workflow_end hooks
        try:
            end_ctx = {**ctx, "state": self.state, "phase_results": self.phase_results}
            self.hook_manager.execute("workflow_end", end_ctx)
        except Exception as exc:
            logger.error("workflow_end hook failed: %s", exc)

        # 6. Cleanup container execution backend (if configured)
        self._cleanup_execution_backend()

        # 7. Return final result
        return {
            "state": self.state,
            "phase_results": self.phase_results,
            "status": "complete",
        }

    # ── Phase lookup ────────────────────────────────────────────────────

    def _find_phase_by_id(self, phase_id: str) -> PhaseDefinition | None:
        """Find a PhaseDefinition by its id in the workflow."""
        for p in self.workflow.phases or []:
            if p.id == phase_id:
                return p
        return None

    # ── Runtime skill prompt assembly ───────────────────────────────────

    def _runtime_skill_repo_root(self) -> Path:
        configured_root = self.framework_config.get("runtime_skill_repo_root")
        if not configured_root:
            runtime_skills_cfg = self.framework_config.get("runtime_skills")
            if isinstance(runtime_skills_cfg, dict):
                configured_root = runtime_skills_cfg.get("repo_root")
        if configured_root:
            return resolve_relative_path(Path(str(configured_root)))
        return workspace_root()

    def _get_runtime_skill_resolver(self) -> RuntimeSkillResolver:
        if self._runtime_skill_resolver is None:
            self._runtime_skill_resolver = RuntimeSkillResolver(
                self._runtime_skill_repo_root()
            )
        return self._runtime_skill_resolver

    def _runtime_skill_names(self, value: Any, location: str) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError(
                f"{location} must be a list of skill names, got {type(value).__name__}"
            )
        names: list[str] = []
        for index, item in enumerate(value):
            if not isinstance(item, str) or not item.strip():
                raise ValueError(f"{location}[{index}] must be a non-empty string")
            names.append(item.strip())
        return names

    def _coerce_runtime_skills_config(
        self,
        raw: Any,
        location: str,
    ) -> RuntimeSkillsConfig | None:
        if raw is None or isinstance(raw, RuntimeSkillsConfig):
            return raw
        if isinstance(raw, list):
            return RuntimeSkillsConfig(include=self._runtime_skill_names(raw, location))
        if not isinstance(raw, dict):
            raise ValueError(
                f"{location} must be a list or mapping, got {type(raw).__name__}"
            )

        merge = str(raw.get("merge", "append"))
        if merge not in {"append", "replace", "none"}:
            raise ValueError(
                f"{location}.merge must be one of ['append', 'none', 'replace'], got '{merge}'"
            )
        missing = str(raw.get("missing", "warn"))
        if missing not in {"warn", "error", "ignore"}:
            raise ValueError(
                f"{location}.missing must be one of ['error', 'ignore', 'warn'], got '{missing}'"
            )

        return RuntimeSkillsConfig(
            include=self._runtime_skill_names(raw.get("include", []), f"{location}.include"),
            exclude=self._runtime_skill_names(raw.get("exclude", []), f"{location}.exclude"),
            merge=merge,
            missing=missing,
            inject_full=bool(raw.get("inject_full", False)),
            exclude_dynamic_duplicates=bool(raw.get("exclude_dynamic_duplicates", True)),
        )

    def _agent_runtime_skill_config(self, agent_id: str) -> RuntimeSkillsConfig | None:
        agent_cfg = (self.workflow.agents or {}).get(agent_id)
        if not isinstance(agent_cfg, dict):
            return None
        return self._coerce_runtime_skills_config(
            agent_cfg.get("runtime_skills"),
            f"agents.{agent_id}.runtime_skills",
        )

    def _phase_runtime_skill_config(
        self,
        phase: PhaseDefinition,
    ) -> RuntimeSkillsConfig | None:
        return self._coerce_runtime_skills_config(
            getattr(phase, "runtime_skills", None),
            f"phases[{phase.id}].runtime_skills",
        )

    def _resolve_runtime_skill_bundle(
        self,
        phase: PhaseDefinition,
        agent_id: str,
    ) -> RuntimeSkillBundle | None:
        agent_config = self._agent_runtime_skill_config(agent_id)
        phase_config = self._phase_runtime_skill_config(phase)
        if agent_config is None and phase_config is None:
            return None

        bundle = self._get_runtime_skill_resolver().resolve(
            agent_config=agent_config,
            phase_config=phase_config,
        )
        for warning in bundle.warnings:
            logger.warning("Runtime skill resolution for phase '%s': %s", phase.id, warning)
        return bundle

    def _append_explicit_runtime_skill_markdown(
        self,
        prompt_text: str,
        phase: PhaseDefinition,
        agent_id: str,
    ) -> tuple[str, RuntimeSkillBundle | None]:
        bundle = self._resolve_runtime_skill_bundle(phase, agent_id)
        if not bundle or not bundle.markdown:
            return prompt_text, bundle

        if prompt_text.endswith("\n\n"):
            separator = ""
        elif prompt_text.endswith("\n"):
            separator = "\n"
        else:
            separator = "\n\n"
        prompt_text = f"{prompt_text}{separator}{bundle.markdown}"
        logger.info(
            "[INJECT RUNTIME SKILLS %s] Skills=%s",
            phase.id,
            ", ".join(bundle.names),
        )
        return prompt_text, bundle

    def _append_dynamic_experience_markdown(
        self,
        prompt_text: str,
        phase: PhaseDefinition,
        state: dict[str, Any],
        context: dict[str, Any],
        explicit_skill_bundle: RuntimeSkillBundle | None,
        step_outputs: dict[str, Any] | None = None,
        loop_history: list[Any] | None = None,
        log_phase_id: str | None = None,
    ) -> str:
        if not getattr(phase, 'retrieve_experience', False) or not self.experience_store:
            return prompt_text

        exp_cfg = getattr(getattr(self.workflow, 'experience', None), 'enabled', True)
        if not exp_cfg:
            return prompt_text

        phase_id = log_phase_id or phase.id
        try:
            from core.experience_query import ExperienceQuerier
            from core.experience_injector import ExperienceInjector

            querier = ExperienceQuerier(self.experience_store, self.session_mgr)
            query_ctx = self._build_experience_query_context(
                phase, state, context, step_outputs, loop_history
            )
            query_result = querier.query(query_ctx)
            query_result = self._dedupe_dynamic_experiences(
                query_result, explicit_skill_bundle, phase_id
            )
            injector = ExperienceInjector()
            action_cards = injector.action_cards(query_result)
            selected_ids = self._experience_ids(query_result.get("selected_experiences", []))
            if step_outputs is not None:
                self._store_dynamic_experience_result(
                    step_outputs, phase_id, query_result, action_cards
                )
            self._record_experience_usage(selected_ids=selected_ids)
            self._emit_experience_event(
                "experience_selected",
                phase_id=phase_id,
                agent_id=phase.agent or "main_engineer",
                selected_count=len(selected_ids),
                selected_ids=selected_ids,
                selected_experiences=self._compact_selected_experiences(
                    query_result.get("selected_experiences", [])
                ),
                action_card_count=len(action_cards),
                action_cards=self._compact_action_cards(action_cards),
                injected=bool(query_result.get("selected_experiences")),
                summary=query_result.get("summary", ""),
                warning=query_result.get("warning", ""),
            )

            injected_text = ""
            if query_result.get("selected_experiences"):
                injected_text = injector.inject(phase, query_result)
                prompt_text += injected_text
                logger.info(
                    "[INJECT EXP %s] Length=%d\n%s",
                    phase_id,
                    len(injected_text),
                    injected_text,
                )
            else:
                logger.info("[INJECT EXP %s] No experiences selected", phase_id)
        except Exception as exc:
            logger.warning("Experience retrieval failed for phase '%s': %s", phase.id, exc)
        return prompt_text


    def _store_dynamic_experience_result(
        self,
        step_outputs: dict[str, Any],
        phase_id: str,
        query_result: dict[str, Any],
        action_cards: list[str],
    ) -> None:
        selected = query_result.get("selected_experiences", [])
        if not isinstance(selected, list):
            selected = []

        stored_result = dict(query_result)
        stored_result["experience_action_cards"] = action_cards
        by_phase = step_outputs.setdefault("experience_query_results", {})
        if isinstance(by_phase, dict):
            by_phase[phase_id] = stored_result
        step_outputs[f"{phase_id}_selected_experiences"] = selected
        step_outputs[f"{phase_id}_selected_experience_ids"] = self._experience_ids(selected)
        step_outputs[f"{phase_id}_experience_action_cards"] = action_cards

        if phase_id == "analyze_error":
            step_outputs["selected_experiences"] = selected
            step_outputs["selected_experience_ids"] = self._experience_ids(selected)
            step_outputs["experience_action_cards"] = action_cards

    def _append_inherited_experience_markdown(
        self,
        prompt_text: str,
        phase_id: str,
        step_outputs: dict[str, Any],
    ) -> str:
        if self._is_slim_repair_prompt_phase(phase_id):
            return prompt_text
        if phase_id not in {"fix_dependency", "fix_code", "fix_operator"}:
            return prompt_text
        cards = step_outputs.get("experience_action_cards") or step_outputs.get(
            "analyze_error_experience_action_cards"
        )
        if not isinstance(cards, list) or not cards:
            return prompt_text

        inherited = "\n\n## Analyzer-Selected Experience Action Cards\n"
        inherited += (
            "These cards were selected during analyze_error. Read applicable paths yourself "
            "before acting. At the end of your response JSON, include exactly these "
            "experience-report fields even when empty: `used_experience_ids`, "
            "`experience_actions_taken`, `ignored_experience_ids`, and `ignored_reasons`. "
            "Use an experience only when its contents match this failure; otherwise ignore it "
            "and explain why.\n\n"
        )
        inherited += "\n".join(str(card) for card in cards)
        return f"{prompt_text}{inherited}"

    def _dedupe_dynamic_experiences(
        self,
        query_result: dict[str, Any],
        explicit_skill_bundle: RuntimeSkillBundle | None,
        phase_id: str,
    ) -> dict[str, Any]:
        if not explicit_skill_bundle or not explicit_skill_bundle.exclude_dynamic_duplicates:
            return query_result

        selected = query_result.get("selected_experiences")
        if not isinstance(selected, list) or not selected:
            return query_result

        explicit_names = self._explicit_runtime_skill_name_keys(explicit_skill_bundle)
        explicit_paths = {
            path_key
            for path_key in (
                self._normalized_path_key(path) for path in explicit_skill_bundle.paths
            )
            if path_key
        }
        if not explicit_names and not explicit_paths:
            return query_result

        filtered: list[Any] = []
        skipped = 0
        for experience in selected:
            if isinstance(experience, dict) and self._is_duplicate_dynamic_experience(
                experience, explicit_names, explicit_paths
            ):
                skipped += 1
                continue
            filtered.append(experience)

        if skipped == 0:
            return query_result

        logger.info(
            "[INJECT EXP %s] Skipped %d duplicate experience(s) already covered by explicit runtime skills",
            phase_id,
            skipped,
        )
        filtered_result = dict(query_result)
        filtered_result["selected_experiences"] = filtered
        return filtered_result

    def _explicit_runtime_skill_name_keys(self, bundle: RuntimeSkillBundle) -> set[str]:
        keys: set[str] = set()
        for name in bundle.names:
            key = self._runtime_skill_name_key(name)
            if key:
                keys.add(key)
        for path in bundle.paths:
            path_obj = Path(str(path))
            for candidate in (path_obj.parent.name, path_obj.stem):
                key = self._runtime_skill_name_key(candidate)
                if key and key not in {"skill", "skill_data"}:
                    keys.add(key)
        return keys

    def _is_duplicate_dynamic_experience(
        self,
        experience: dict[str, Any],
        explicit_names: set[str],
        explicit_paths: set[str],
    ) -> bool:
        for field_name in ("skill_name", "name"):
            key = self._runtime_skill_name_key(experience.get(field_name))
            if key and key in explicit_names:
                return True

        experience_id = self._runtime_skill_name_key(experience.get("id"))
        if experience_id and self._experience_id_matches_explicit_skill(
            experience_id, explicit_names
        ):
            return True

        file_path = experience.get("file_path") or experience.get("path")
        if not file_path:
            return False

        path_key = self._normalized_path_key(file_path)
        if path_key and path_key in explicit_paths:
            return True

        file_path_obj = Path(str(file_path))
        for candidate in (file_path_obj.name, file_path_obj.stem, file_path_obj.parent.name):
            key = self._runtime_skill_name_key(candidate)
            if key and key in explicit_names:
                return True
        return False

    def _experience_id_matches_explicit_skill(
        self,
        experience_id: str,
        explicit_names: set[str],
    ) -> bool:
        if experience_id in explicit_names:
            return True
        if experience_id.startswith("promoted-"):
            return experience_id[len("promoted-"):] in explicit_names
        if "-exp-" in experience_id:
            return experience_id.rsplit("-exp-", 1)[-1] in explicit_names
        return any(
            experience_id == f"promoted-{name}"
            or experience_id.endswith(f"-exp-{name}")
            for name in explicit_names
        )

    def _runtime_skill_name_key(self, value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip().lower()

    def _normalized_path_key(self, value: Any) -> str:
        if value is None:
            return ""
        try:
            return str(Path(str(value)).expanduser().resolve(strict=False))
        except (OSError, RuntimeError, TypeError, ValueError):
            return os.path.abspath(str(value))

    # ── Condition evaluation ────────────────────────────────────────────

    def _evaluate_condition(
        self,
        condition: str,
        state: dict[str, Any],
        context: dict[str, Any],
        loop_vars: dict[str, Any] | None = None,
        loop_state: dict[str, Any] | None = None,
        step_outputs: dict[str, Any] | None = None,
    ) -> bool:
        """Evaluate a condition expression.

        Supports:
          - ${...} template resolution via VariableResolver
          - $.field_name shorthand for loop_state / step_outputs lookup
          - Boolean operators: ==, !=, >, <, >=, <=, and, or, not, in
        """
        # Step 1: Resolve ${...} templates
        resolved = self.resolver.resolve(
            condition,
            state=state,
            globals=self.workflow.globals,
            context=context,
            loop_vars=loop_vars,
            loop_state=loop_state,
            step_outputs=step_outputs,
        )
        if not isinstance(resolved, str):
            return bool(resolved)

        # Step 2: Sanitize dangling comparison operators after ${} resolution.
        # When a leading ${VAR} resolves to empty string, the expression may start
        # with a bare operator (e.g. " != ''").  Prepending '' makes it valid Python.
        expr = resolved
        expr = re.sub(r'^\s*(!=|==|>=|<=|>|<)\s', r"'' \1 ", expr)

        # Step 3: Handle $.field_name shorthand (not ${} format)
        if "$." in expr:
            def dollar_repl(m: re.Match[str]) -> str:
                field = m.group(1)
                # Lookup order: step_outputs (current iter) → globals → context → loop_state (outer, stale-safe)
                # step_outputs first so current-iteration script_exit_code wins over previous iteration's value in outer loop_state
                for src in (step_outputs or {}, self.workflow.globals or {},
                            context or {}, loop_state or {}):
                    if field in src:
                        val = src[field]
                        return json.dumps(val) if not isinstance(val, str) else val
                return repr(field)
            expr = re.sub(r'\$\.(\w+)', dollar_repl, expr)

        # If entire expression was a single ${...} and resolved to a bool-like
        # value, shortcut
        if expr in (True, False):
            return bool(expr)
        if expr.lower() in ("true", "1"):
            return True
        if expr.lower() in ("false", "0", ""):
            return False

        # Step 3: Safe boolean evaluation
        env: dict[str, Any] = {}
        # Seed environment from state summaries
        for k, v in state.items():
            if isinstance(v, dict):
                env[k] = v
            else:
                env[k] = v
        env.update(self.workflow.globals or {})
        env.update(context or {})
        if loop_state:
            env.update(loop_state)
        if loop_vars:
            env.update(loop_vars)
        if step_outputs:
            env.update(step_outputs)

        try:
            return _safe_eval_bool(expr, env)
        except Exception as exc:
            logger.warning("Condition eval failed '%s' → %s (treating as True)", condition, exc)
            return True  # default to proceed

    # ── Input mapping resolution ────────────────────────────────────────

    def _resolve_input_mapping(
        self,
        phase: PhaseDefinition,
        state: dict[str, Any],
        context: dict[str, Any],
        loop_vars: dict[str, Any] | None = None,
        loop_state: dict[str, Any] | None = None,
        loop_history: list[Any] | None = None,
        step_outputs: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Resolve phase.input_mapping into a context dict."""
        resolved_ctx: dict[str, Any] = {}
        for key, value in (phase.input_mapping or {}).items():
            resolved_ctx[key] = self.resolver.resolve(
                value,
                state=state,
                globals=self.workflow.globals,
                context=context,
                loop_vars=loop_vars,
                loop_state=loop_state,
                loop_history=loop_history,
                step_outputs=step_outputs,
            )
        return resolved_ctx

    # ── LLM phase ──────────────────────────────────────────────────────

    def _execute_llm_phase(
        self,
        phase: PhaseDefinition,
        state: dict[str, Any],
        context: dict[str, Any],
        session_id: str | None = None,
        loop_vars: dict[str, Any] | None = None,
        loop_state: dict[str, Any] | None = None,
        step_outputs: dict[str, Any] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Execute an LLM-type phase: resolve agent, send prompt, validate."""
        # 1. Resolve agent / session
        agent_id = phase.agent or "main_engineer"
        if self.session_registry:
            try:
                sid = self.session_registry.resolve(agent_id)
            except KeyError:
                sid = session_id or self.session_mgr.get_or_create(
                    role=agent_id, lifecycle="persistent"
                )
        else:
            sid = session_id or self.session_mgr.get_or_create(
                role=agent_id, lifecycle="persistent"
            )

        # 2. Build prompt context — replicate PhaseRunner._build_prompt_context behavior
        input_ctx = self._resolve_input_mapping(
            phase, state, context,
            loop_vars=loop_vars, loop_state=loop_state,
            step_outputs=step_outputs,
        )
        self._inject_llm_baseline_context(input_ctx, phase, state)
        self._inject_llm_phase_specific_context(input_ctx, phase, state)

        prompt_text = self.prompt_loader.load_prompt(phase.prompt_template, input_ctx)
        prompt_text, explicit_skill_bundle = self._append_explicit_runtime_skill_markdown(
            prompt_text, phase, agent_id
        )
        prompt_text = self._append_dynamic_experience_markdown(
            prompt_text, phase, state, context, explicit_skill_bundle
        )
        prompt_text = inject_phase_boundary(prompt_text, framework_config=self.framework_config)
        timeout = self._llm_timeout_for_phase(phase)

        # 4. Send command
        try:
            send_kwargs = {"timeout": timeout}
            if phase.id == "phase_6_report":
                send_kwargs["retries"] = 0
            raw_response = self.session_mgr.send_command(sid, prompt_text, **send_kwargs)
        except (TimeoutError, RuntimeError, ConnectionRefusedError) as exc:
            if phase.id == "phase_6_report":
                output = self._phase_6_fallback_output(input_ctx, state, str(exc))
                return "success", output
            raise

        # 5. Parse JSON
        output = extract_json_response(raw_response)
        if phase.id == "phase_6_report" and self._is_session_error_response(output):
            reason = str(output.get("error") or "Phase 6 LLM call failed")
            output = self._phase_6_fallback_output(input_ctx, state, reason)
            return "success", output
        self._raise_for_session_error_output(output, phase.id)

        output_format = expected_output_format(phase.output_schema, prompt_text)

        parse_attempt = 0
        max_parse_retries = 2
        while not output and parse_attempt < max_parse_retries:
            if phase.id == "phase_6_report":
                output = self._phase_6_fallback_output(
                    input_ctx, state, "Phase 6 LLM response was empty or malformed",
                )
                return "success", output
            parse_attempt += 1
            parse_correction = self._build_validation_correction_prompt(
                "Your response did not contain a valid JSON object.",
                output_format_example=output_format,
                is_parse_failure=True,
                phase_name=phase.id,
            )
            raw_response = self.session_mgr.send_command(sid, parse_correction, timeout=timeout)
            output = extract_json_response(raw_response)
            self._raise_for_session_error_output(output, phase.id)
        if not output:
            output = {"raw_response": raw_response}
        elif phase.id == "phase_6_report" and not self._phase_6_output_complete(output):
            output = self._phase_6_fallback_output(
                input_ctx, state, "Phase 6 LLM response omitted required report fields",
            )
            return "success", output

        # 6. Normalize and validate with retries
        output = self._normalize_llm_output(phase, output, input_ctx, state)
        max_retries = 3
        if phase.validator or phase.validate_only:
            validation_passed = False
            validation_errors: list[str] = []
            for attempt in range(1, max_retries + 1):
                validation_result = self.validator_engine.validate(
                    phase.validator or phase.id, output
                )
                if getattr(validation_result, "passed", True):
                    validation_passed = True
                    break
                validation_errors = [str(error) for error in getattr(validation_result, "errors", ["unknown"])]
                if attempt >= max_retries:
                    break
                error_msg = "; ".join(validation_errors)
                correction_prompt = self._build_validation_correction_prompt(
                    error_msg,
                    output_format_example=output_format,
                    phase_name=phase.id,
                )
                raw_response = self.session_mgr.send_command(sid, correction_prompt, timeout=timeout)
                output = extract_json_response(raw_response)
                self._raise_for_session_error_output(output, phase.id)
                if not output:
                    parse_correction = self._build_validation_correction_prompt(
                        "Your response did not contain a valid JSON object.",
                        output_format_example=output_format,
                        is_parse_failure=True,
                        phase_name=phase.id,
                    )
                    raw_response = self.session_mgr.send_command(sid, parse_correction, timeout=timeout)
                    output = extract_json_response(raw_response)
                    self._raise_for_session_error_output(output, phase.id)
                    if not output:
                        output = {"raw_response": raw_response}
                output = self._normalize_llm_output(phase, output, input_ctx, state)
            if not validation_passed:
                try:
                    self.artifact_store.save_phase_output(
                        phase.id,
                        {**output, "validation_errors": validation_errors},
                    )
                except Exception as exc:
                    logger.warning("Artifact save failed for invalid %s: %s", phase.id, exc)
                return "failure", {**output, "validation_errors": validation_errors}

        # 8. Save to artifact store
        try:
            self.artifact_store.save_phase_output(phase.id, output)
            self.artifact_store.mark_validated(phase.id, output)
        except Exception as exc:
            logger.warning("Artifact save failed for %s: %s", phase.id, exc)

        # 9. Apply output_as
        status = "success"
        return status, output

    @staticmethod
    def _is_session_error_response(output: Any) -> bool:
        if not isinstance(output, dict):
            return False
        return output.get("ok") is False and bool(output.get("error"))

    @staticmethod
    def _raise_for_session_error_output(output: Any, phase_id: str) -> None:
        if not WorkflowExecutor._is_session_error_response(output):
            return
        assert isinstance(output, dict)
        error = str(output.get("error") or "session command failed")
        raise SessionCommandError(f"Session command failed for {phase_id}: {error}", dict(output))

    def _artifact_report_dir(self) -> str:
        artifact_dir = getattr(self.artifact_store, "artifact_dir", None)
        if isinstance(artifact_dir, str):
            return os.path.join(artifact_dir, "reports")
        if isinstance(artifact_dir, Path):
            return str(artifact_dir / "reports")
        return str(Path(self.project_dir) / ".sm-artifacts" / "reports")

    def _phase_6_fallback_output(
        self,
        input_ctx: dict[str, Any],
        state: dict[str, Any],
        reason: str,
    ) -> dict[str, Any]:
        prior_outputs = collect_phase6_prior_artifacts(self.artifact_store)
        prior_outputs.update(collect_phase6_prior_state(state))
        report_dir = str(input_ctx.get("report_dir") or self._artifact_report_dir())
        return build_phase6_fallback_report(
            project_dir=self.project_dir,
            report_dir=report_dir,
            prior_outputs=prior_outputs,
            reason=reason,
        )

    def _llm_timeout_for_phase(self, phase: PhaseDefinition) -> int | None:
        if phase.timeout is not None:
            return phase.timeout
        if phase.id != "phase_6_report":
            return None
        return resolve_phase6_timeout(self.framework_config, phase.timeout, logger)

    @staticmethod
    def _phase_6_output_complete(output: dict[str, Any]) -> bool:
        report_paths = output.get("report_paths")
        migration_summary = output.get("migration_summary")
        return isinstance(report_paths, list) and isinstance(migration_summary, dict)

    @staticmethod
    def _extract_output_format_from_prompt(prompt_text: object) -> str | None:
        return extract_output_format_from_prompt(prompt_text)

    @staticmethod
    def _build_validation_correction_prompt(
        error_msg: str,
        *,
        output_format_example: str | None = None,
        is_parse_failure: bool = False,
        phase_name: str = "",
    ) -> str:
        return build_validation_correction_prompt(
            error_msg,
            output_format_example=output_format_example,
            is_parse_failure=is_parse_failure,
            phase_name=phase_name,
            missing_fields=extract_missing_fields([error_msg]),
        )

    def _resolve_sub_workflow_llm_timeout(self, phase: PhaseDefinition) -> int | None:
        if phase.timeout is not None:
            return phase.timeout
        if phase.id == "analyze_error":
            return self._resolve_configured_sub_workflow_timeout(
                phase,
                (
                    "session_timeout_analyze_error",
                    "session_timeout_analyzer",
                    "session_timeout_repair",
                ),
                SUB_WORKFLOW_ANALYZE_TIMEOUT_DEFAULT,
            )
        if phase.id not in SUB_WORKFLOW_REPAIR_PHASE_IDS:
            return None

        return self._resolve_configured_sub_workflow_timeout(
            phase,
            ("session_timeout_repair",),
            SUB_WORKFLOW_REPAIR_TIMEOUT_DEFAULT,
        )

    def _resolve_configured_sub_workflow_timeout(
        self,
        phase: PhaseDefinition,
        config_keys: tuple[str, ...],
        default_timeout: int | None,
    ) -> int | None:
        for config_key in config_keys:
            raw_timeout = self.framework_config.get(config_key)
            if raw_timeout is None:
                continue
            try:
                return int(raw_timeout)
            except (TypeError, ValueError):
                logger.warning(
                    "Invalid %s=%r for sub-phase '%s'; using default %s",
                    config_key,
                    raw_timeout,
                    phase.id,
                    default_timeout,
                )
                return default_timeout
        return default_timeout

    def _send_sub_workflow_llm_command(
        self,
        *,
        phase_id: str,
        agent_id: str,
        session_id: str,
        prompt_text: str,
        timeout: int | None,
    ) -> str:
        logger.info(
            "Sending sub-phase LLM command: phase_id=%s agent_id=%s session_id=%s timeout=%s prompt_length=%s",
            phase_id,
            agent_id,
            session_id,
            timeout,
            len(prompt_text),
        )
        raw_response = self.session_mgr.send_command(session_id, prompt_text, timeout=timeout)
        retry_error = self._retryable_sub_workflow_session_error(raw_response)
        if retry_error:
            retry_session_id = self._create_sub_workflow_retry_session(agent_id, phase_id)
            logger.warning(
                "Retrying sub-phase LLM command in fresh session after session error: "
                "phase_id=%s agent_id=%s old_session_id=%s retry_session_id=%s error=%s",
                phase_id,
                agent_id,
                session_id,
                retry_session_id,
                retry_error,
            )
            raw_response = self.session_mgr.send_command(retry_session_id, prompt_text, timeout=timeout)
        logger.info(
            "Received sub-phase LLM response: phase_id=%s raw_response_length=%s",
            phase_id,
            len(raw_response or ""),
        )
        return raw_response

    def _retryable_sub_workflow_session_error(self, raw_response: str) -> str:
        output = extract_json_response(raw_response)
        if not self._is_session_error_response(output):
            return ""
        error = str(output.get("error") or "").strip()
        if error.lower() in RETRYABLE_SUB_WORKFLOW_SESSION_ERRORS:
            return error
        return ""

    def _create_sub_workflow_retry_session(self, agent_id: str, phase_id: str) -> str:
        retry_role = f"{agent_id}_{phase_id}_retry"
        create_session = getattr(self.session_mgr, "create_session", None)
        if callable(create_session):
            try:
                return str(create_session(
                    role=retry_role,
                    agent=agent_id,
                    lifecycle="ephemeral",
                    title=f"migration-{retry_role}",
                    working_dir=self.project_dir,
                ))
            except TypeError:
                pass
        return str(self.session_mgr.get_or_create(role=retry_role, lifecycle="ephemeral"))

    @staticmethod
    def _is_retryable_session_error_text(error_text: str) -> bool:
        normalized = error_text.strip().lower()
        return any(token in normalized for token in RETRYABLE_SUB_WORKFLOW_SESSION_ERRORS)

    @staticmethod
    def _is_operator_repair_phase(phase_id: str) -> bool:
        return phase_id in {"fix_operator", "imp_fix_operator"}

    @classmethod
    def _operator_repair_communication_failure(cls, phase_id: str, output: object) -> bool:
        if not cls._is_operator_repair_phase(phase_id):
            return False
        if not isinstance(output, dict):
            return False
        output_map = cast(dict[str, object], output)
        if output_map.get("retryable") is True:
            return True
        error = str(output_map.get("error") or "").strip()
        return bool(error) and cls._is_retryable_session_error_text(error)

    @classmethod
    def _operator_repair_output_claims_full_pass(cls, phase_id: str, phase_output: dict[str, Any]) -> bool:
        if not cls._is_operator_repair_phase(phase_id):
            return False
        text_parts = [
            str(phase_output.get("status") or ""),
            str(phase_output.get("summary") or ""),
            str(phase_output.get("agent_diagnostics") or ""),
            str(phase_output.get("full_migration_status") or ""),
        ]
        verification = phase_output.get("verification")
        if isinstance(verification, dict):
            verification_map = cast(dict[str, object], verification)
            text_parts.append(str(verification_map.get("status") or ""))
            result = verification_map.get("result")
            if isinstance(result, dict):
                result_map = cast(dict[str, object], result)
                text_parts.append(str(result_map.get("status") or ""))
        text = "\n".join(text_parts).lower()
        return "full_pass" in text and ("pass" in text or "success" in text)

    @staticmethod
    def _prompt_template_for_llm_phase(
        *,
        phase_id: str,
        default_template: str,
        state: dict[str, Any],
    ) -> str:
        if phase_id in {"fix_operator", "imp_fix_operator"}:
            phase3_contract = state.get("phase_3_entry_script")
            if isinstance(phase3_contract, dict) and _operator_repair_has_custom_op_contract(
                cast(dict[str, object], phase3_contract)
            ):
                return "repair_custom_op_variant_service"
        return default_template

    def _recover_operator_repair_from_current_final_gate(
        self,
        *,
        phase_id: str,
        state: dict[str, Any],
        context: dict[str, Any],
        loop_vars: dict[str, Any] | None,
        command_started_at: float | None,
        require_current_run: bool = True,
    ) -> dict[str, object] | None:
        if not self._is_operator_repair_phase(phase_id):
            return None
        contract = state.get("phase_3_entry_script")
        if not isinstance(contract, dict) or not has_custom_op_contract(cast(dict[str, object], contract)):
            return None
        reports_dir = self._resolve_custom_op_reports_dir(cast(dict[str, Any], contract), context, loop_vars)
        gate_path = reports_dir / "custom_op_final_gate.json"
        try:
            stat_result = gate_path.stat()
        except OSError:
            return None
        if stat_result.st_size > _CUSTOM_OP_GATE_REPORT_MAX_BYTES:
            return None
        if require_current_run and command_started_at is not None and stat_result.st_mtime + 1.0 < command_started_at:
            return None
        try:
            with gate_path.open("r", encoding="utf-8") as handle:
                gate_data = cast(object, json.load(handle))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(gate_data, dict):
            return None
        gate_map = cast(dict[str, object], gate_data)
        validation = validate_custom_op_final_gate(
            gate_map,
            project_root=reports_dir.parent,
            platform_policy=self.platform_policy,
        )
        if validation.get("passed") is not True or gate_map.get("full_migration_status") != "FULL_PASS":
            return None
        return {
            "fixed": True,
            "status": "success",
            "summary": "Recovered from operator-fixer response after validating current custom_op_final_gate FULL_PASS.",
            "agent_diagnostics": "migration_reports/custom_op_final_gate.json validated FULL_PASS",
            "custom_op_final_gate_recovered": True,
            "custom_op_final_gate_path": str(gate_path),
            "custom_op_final_gate": {
                "passed": True,
                "path": str(gate_path),
                "summary": {
                    "inventory_count": gate_map.get("inventory_count"),
                    "manifest_entries": gate_map.get("manifest_entries"),
                    "closed_pass_entries": gate_map.get("closed_pass_entries"),
                    "remaining_entries": gate_map.get("remaining_entries"),
                    "full_migration_status": gate_map.get("full_migration_status"),
                },
            },
        }

    def _recover_operator_repair_from_claimed_full_pass(
        self,
        *,
        phase_id: str,
        phase_output: dict[str, Any],
        state: dict[str, Any],
        context: dict[str, Any],
        loop_vars: dict[str, Any] | None,
    ) -> dict[str, object] | None:
        if not self._operator_repair_output_claims_full_pass(phase_id, phase_output):
            return None
        return self._recover_operator_repair_from_current_final_gate(
            phase_id=phase_id,
            state=state,
            context=context,
            loop_vars=loop_vars,
            command_started_at=None,
            require_current_run=False,
        )

    # ── Phase-aware previous_outputs whitelist ────────────────────────
    # Maps prompt_id patterns to a whitelist of state keys that should appear
    # in the serialized `previous_outputs` context.  An empty list means the
    # phase receives no previous_outputs at all.  A missing key falls back to
    # the legacy "all state" behaviour so we stay backward-compatible.

    _PREVIOUS_OUTPUTS_WHITELIST: dict[str, list[str]] = {
        # Early phases: no prior outputs needed.
        "phase_0_env_detect": [],
        "phase_1_project_analysis": [],
        "phase_2_venv_create": [],
        # Phase 1.5 gets `phase_1_context` separately; do not duplicate.
        "phase_1_5_constraint_summary": [],
        # Phase 3 only needs its own input mapping; no prior outputs required.
        "phase_3_entry_script": [],
        # Phase 3.5 needs ONLY Phase 3 entry script output, not Phase 0/1/1.5/2 noise.
        "phase_35_static_validate": ["phase_3_entry_script"],
        # Phase 6/report still receives all prior outputs (full context required).
        # No entry → falls through to legacy "all" behaviour.
    }

    def _filter_previous_outputs(self, phase: PhaseDefinition, state: dict[str, Any]) -> dict[str, Any]:
        """Return only the whitelisted state keys as `previous_outputs` for a phase."""
        pid = phase.id
        pt = phase.prompt_template or ""
        for key in (pid, pt):
            if key in self._PREVIOUS_OUTPUTS_WHITELIST:
                allowed = self._PREVIOUS_OUTPUTS_WHITELIST[key]
                if not allowed:
                    return {}
                return {k: v for k, v in state.items() if k in allowed}
        return dict(state)

    def _inject_llm_baseline_context(
        self,
        input_ctx: dict[str, Any],
        phase: PhaseDefinition,
        state: dict[str, Any],
    ) -> None:
        input_ctx.setdefault("phase_name", phase.id)
        input_ctx.setdefault("project_dir", self.project_dir)
        input_ctx.setdefault("workspace_root", str(workspace_root()))
        input_ctx.setdefault("user_constraints", self.user_constraints)
        constraint_summary = self._resolve_constraint_summary(state)
        input_ctx.setdefault("constraint_summary", constraint_summary)
        input_ctx.setdefault("platform", self.platform_policy.id)
        input_ctx.setdefault("platform_display_name", self.platform_policy.display_name)
        input_ctx.setdefault("platform_guidance", (
            f"Target accelerator: {self.platform_policy.display_name}. "
            f"Use {self.platform_policy.guidance_native_framework}."
        ))

        filtered_state = self._filter_previous_outputs(phase, state)
        serialized_state = {}
        for k, v in filtered_state.items():
            if isinstance(v, dict):
                sanitized = {kk: vv for kk, vv in v.items()
                             if isinstance(vv, (str, int, float, bool, list))}
                serialized_state[k] = sanitized
            elif isinstance(v, (str, int, float, bool, list)):
                serialized_state[k] = v
        input_ctx.setdefault("previous_outputs", json.dumps(serialized_state, indent=2, ensure_ascii=False))

        for key, value in _get_exec_ctx(self.exec_backend).items():
            input_ctx.setdefault(key, value)
        self._inject_container_env_context(input_ctx)
        self._inject_execution_environment_context(input_ctx)

    def _inject_execution_environment_context(self, input_ctx: dict[str, Any]) -> None:
        if "execution_environment_context" in input_ctx:
            return
        probe = getattr(self, "_container_env_probe", None)
        input_ctx["execution_environment_context"] = _get_exec_env_ctx(self.exec_backend, probe)

    def _inject_llm_phase_specific_context(
        self,
        input_ctx: dict[str, Any],
        phase: PhaseDefinition,
        state: dict[str, Any],
    ) -> None:
        pid = phase.id
        if "phase_1_5" in pid or "constraint_summary" in pid:
            ph1 = state.get("phase_1_project_analysis", {})
            if isinstance(ph1, dict) and ph1:
                input_ctx.setdefault("phase_1_context", json.dumps(ph1, indent=2, ensure_ascii=False))
            else:
                input_ctx.setdefault("phase_1_context", "(No phase 1 context available)")
        if "phase_35" in pid or "static_validate" in pid:
            ph3 = state.get("phase_3_entry_script", {})
            if isinstance(ph3, dict):
                input_ctx.setdefault("entry_script_path",
                                     ph3.get("entry_script_path", "(not available)"))
        if "phase_3_entry_script" in pid or "phase_3" in pid:
            ph35 = state.get("phase_35_static_validate", {})
            if isinstance(ph35, dict) and ph35.get("validation_passed") is False:
                fix_plan = ph35.get("fix_plan", "")
                issues = ph35.get("issues", [])
                issues_str = "\n".join(f"  - {i}" for i in issues) if isinstance(issues, list) else str(issues)
                feedback = (
                    "=== Phase 3.5 VALIDATION FAILED - FIX THE FOLLOWING BEFORE RETRYING ===\n"
                    f"Issues found by Phase 3.5:\n{issues_str}\n\n"
                    f"Fix plan:\n{fix_plan}"
                )
                current = input_ctx.get("constraint_summary", "")
                input_ctx["constraint_summary"] = f"{current}\n\n{feedback}" if current else feedback
        if "phase_6" in pid:
            input_ctx.setdefault("report_dir", self._artifact_report_dir())

    def _inject_container_env_context(self, input_ctx: dict[str, Any]) -> None:
        if not isinstance(self.exec_backend, ContainerBackend):
            return
        probe = self._container_env_probe
        if not probe:
            return

        backend_ctx = _get_exec_ctx(self.exec_backend)
        for k, v in backend_ctx.items():
            input_ctx.setdefault(k, v)

        input_ctx.setdefault(
            "container_env_facts",
            json.dumps(probe, ensure_ascii=False, indent=2, default=str),
        )
        for key in ("interpreter_path", "python_version", "platform", "platform_machine", "cwd", "torch_version"):
            if key in probe:
                input_ctx.setdefault(f"container_{key}", str(probe[key]))

    def _inject_sub_workflow_context(
        self,
        input_ctx: dict[str, Any],
        phase_id: str,
        step_outputs: dict[str, Any],
        loop_vars: dict[str, Any],
        state: dict[str, Any],
        loop_history: list[Any] | None,
    ) -> None:
        if loop_history is None:
            loop_history = []
        error_analysis = step_outputs.get("error_analysis")
        if not isinstance(error_analysis, dict):
            error_analysis = state.get("error_analysis", {}) if isinstance(state, dict) else {}
        if not isinstance(error_analysis, dict):
            error_analysis = {}
        script_stderr = step_outputs.get("script_stderr", "")
        entry_script = loop_vars.get("entry_script", "")
        env_ctx = self._build_env_context(state)
        env_ctx_str = json.dumps(env_ctx, ensure_ascii=False) if env_ctx else "(No environment context available)"
        artifact_base = self.artifact_store.artifact_dir
        raw_files = self._list_attempt_files()
        constraint = self._resolve_constraint_summary(state)
        hist_summary = self._format_history_summary(loop_history)

        # Inject container execution context for Phase 5 sub-workflow phases
        es = str(entry_script)
        exec_cmd: str | list[str] = shlex.split(es) if isinstance(self.exec_backend, ContainerBackend) else es
        exec_ctx = _get_exec_ctx(self.exec_backend, command=exec_cmd)
        input_ctx.update(exec_ctx)

        if phase_id in ("fix_dependency", "fix_code", "fix_operator"):
            if phase_id in {"fix_dependency", "fix_operator"}:
                default_role = "dependency_fixer" if phase_id == "fix_dependency" else "operator_fixer"
                runtime_error_path, runtime_card_path = self._write_repair_runtime_artifacts(
                    project_dir=self.project_dir,
                    entry_script=entry_script,
                    error_text=script_stderr,
                    category=str(error_analysis.get("category", "unknown")),
                    root_cause=str(error_analysis.get("root_cause", "")),
                    suggested_fix=str(error_analysis.get("suggested_fix", "")),
                    repair_role=str(error_analysis.get("repair_role", default_role)),
                    experience_action_cards=step_outputs.get("experience_action_cards", []),
                )
                input_ctx.update({
                    "runtime_error_artifact_path": runtime_error_path,
                    "runtime_card_artifact_path": runtime_card_path,
                })
                if phase_id == "fix_operator":
                    phase3_contract = state.get("phase_3_entry_script") if isinstance(state.get("phase_3_entry_script"), dict) else None
                    if _operator_repair_has_custom_op_contract(phase3_contract):
                        operator_context_path = self._write_operator_repair_context_artifact(
                            project_dir=self.project_dir,
                            entry_script=str(entry_script),
                            phase3_contract=phase3_contract,
                        )
                        input_ctx["phase1_phase3_repair_scope"] = self._custom_op_phase1_phase3_repair_scope(phase3_contract)
                        input_ctx["operator_repair_progress_block"] = _operator_custom_op_progress_block(
                            phase3_contract, self.project_dir
                        )
                        input_ctx["strict_custom_op_acceptance_contract"] = (
                            "For active custom-op contracts, success requires current project-local migration reports "
                            "and strict custom_op_final_gate FULL_PASS; agent text alone is not accepted."
                        )
                        input_ctx["operator_custom_op_guidance"] = _operator_custom_op_guidance(
                            operator_context_path,
                            project_dir=self.project_dir,
                            entry_script=str(entry_script),
                            platform_policy=self.platform_policy,
                        )
                    else:
                        input_ctx["operator_custom_op_guidance"] = _operator_generic_guidance(
                            project_dir=self.project_dir,
                            entry_script=str(entry_script),
                            platform_policy=self.platform_policy,
                        )
            input_ctx.update({
                "error_text": script_stderr,
                "category": str(error_analysis.get("category", "unknown")),
                "root_cause": str(error_analysis.get("root_cause", "")),
                "suggested_fix": str(error_analysis.get("suggested_fix", "")),
                "repair_role": str(error_analysis.get("repair_role", "")),
                "history_summary": hist_summary,
                "entry_script": entry_script,
                "last_review": self._serialize_last_review(step_outputs) or "(No review available)",
                "env_context": env_ctx_str,
                "artifact_base_path": artifact_base,
                "raw_attempt_files": raw_files,
                "constraint_summary": constraint,
                "selected_experiences": json.dumps(
                    step_outputs.get("selected_experiences", []), ensure_ascii=False
                ),
                "experience_action_cards": "\n".join(
                    str(card) for card in step_outputs.get("experience_action_cards", [])
                ) or "(No analyzer-selected experience cards)",
                "experience_usage_report_schema": self._experience_usage_report_schema_text(),
            })

        elif phase_id in ("imp_fix_dependency", "imp_fix_code", "imp_fix_operator"):
            imp_plan = step_outputs.get("improvement_plan", {})
            review_verdict = step_outputs.get("review_verdict", {})
            if phase_id in {"imp_fix_dependency", "imp_fix_operator"}:
                default_role = "dependency_fixer" if phase_id == "imp_fix_dependency" else "operator_fixer"
                runtime_error_path, runtime_card_path = self._write_repair_runtime_artifacts(
                    project_dir=self.project_dir,
                    entry_script=entry_script,
                    error_text=script_stderr,
                    category=str(imp_plan.get("category", "quality_improvement")),
                    root_cause=str(imp_plan.get("suggested_direction", "")),
                    suggested_fix=str(imp_plan.get("suggested_direction", "")),
                    repair_role=str(imp_plan.get("repair_role", default_role)),
                    experience_action_cards=step_outputs.get("experience_action_cards", []),
                )
                input_ctx.update({
                    "runtime_error_artifact_path": runtime_error_path,
                    "runtime_card_artifact_path": runtime_card_path,
                })
                if phase_id == "imp_fix_operator":
                    phase3_contract = state.get("phase_3_entry_script") if isinstance(state.get("phase_3_entry_script"), dict) else None
                    if _operator_repair_has_custom_op_contract(phase3_contract):
                        operator_context_path = self._write_operator_repair_context_artifact(
                            project_dir=self.project_dir,
                            entry_script=str(entry_script),
                            phase3_contract=phase3_contract,
                        )
                        input_ctx["phase1_phase3_repair_scope"] = self._custom_op_phase1_phase3_repair_scope(phase3_contract)
                        input_ctx["operator_repair_progress_block"] = _operator_custom_op_progress_block(
                            phase3_contract, self.project_dir
                        )
                        input_ctx["strict_custom_op_acceptance_contract"] = (
                            "For active custom-op contracts, success requires current project-local migration reports "
                            "and strict custom_op_final_gate FULL_PASS; agent text alone is not accepted."
                        )
                        input_ctx["operator_custom_op_guidance"] = _operator_custom_op_guidance(
                            operator_context_path,
                            project_dir=self.project_dir,
                            entry_script=str(entry_script),
                            platform_policy=self.platform_policy,
                        )
                    else:
                        input_ctx["operator_custom_op_guidance"] = _operator_generic_guidance(
                            project_dir=self.project_dir,
                            entry_script=str(entry_script),
                            platform_policy=self.platform_policy,
                        )
            input_ctx.update({
                "error_text": script_stderr,
                "category": str(imp_plan.get("category", "quality_improvement")),
                "root_cause": str(imp_plan.get("suggested_direction", "")),
                "suggested_fix": str(imp_plan.get("suggested_direction", "")),
                "repair_role": str(imp_plan.get("repair_role", "code_adapter")),
                "history_summary": hist_summary,
                "entry_script": entry_script,
                "constraint_summary": constraint,
                "last_review": json.dumps({"verdict": "reject", "reasoning": review_verdict.get("reasoning", "")},
                                          ensure_ascii=False) or "(No review available)",
                "env_context": env_ctx_str,
                "artifact_base_path": artifact_base,
                "raw_attempt_files": raw_files,
                "experience_usage_report_schema": self._experience_usage_report_schema_text(),
            })

        elif phase_id == "improvement_plan":
            review_verdict = step_outputs.get("review_verdict", {})
            reject_reasons = [str(h.get("status", "")) for h in loop_history if h.get("status") == "reject"]
            input_ctx.update({
                "phase_name": "phase_5_validation",
                "last_review_json": json.dumps({"verdict": "reject",
                                                "reasoning": review_verdict.get("reasoning", "")},
                                               ensure_ascii=False),
                "improvement_history": "\n".join(f"- {r}" for r in reject_reasons) if reject_reasons else "(none)",
                "constraint_summary": constraint,
            })

        elif phase_id == "analyze_error":
            input_ctx.update({
                "failed_phase": "phase_5_validation",
                "entry_script": entry_script,
                "entry_script_contract": self._serialize_entry_script_contract(state),
                "failure_log": script_stderr,
                "previous_outputs": self._format_error_analyzer_history(
                    loop_history, step_outputs, state
                ),
                "last_review": self._serialize_last_review(step_outputs) or "(No review available)",
                "env_context": env_ctx_str,
                "artifact_base_path": artifact_base,
                "raw_attempt_files": raw_files,
                "constraint_summary": constraint,
            })

        self._inject_container_env_context(input_ctx)
        self._inject_execution_environment_context(input_ctx)

    def _resolve_constraint_summary(self, state: dict[str, Any]) -> str:
        ph = state.get("phase_1_5_constraint_summary", {})
        if isinstance(ph, dict):
            return str(ph.get("constraint_summary", ""))
        return ""

    def _custom_op_phase1_phase3_repair_scope(self, phase3_contract: dict[str, object] | None) -> str:
        phase1 = self.state.get("phase_1_project_analysis") if isinstance(self.state, dict) else None
        scope = {
            "phase_1_project_analysis": phase1 if isinstance(phase1, dict) else {},
            "phase_3_entry_script": phase3_contract if isinstance(phase3_contract, dict) else {},
        }
        return json.dumps(scope, indent=2, ensure_ascii=False, default=str)

    def _serialize_entry_script_contract(self, state: dict[str, Any]) -> str:
        contract = state.get("phase_3_entry_script", {}) if isinstance(state, dict) else {}
        if not isinstance(contract, dict) or not contract:
            return "(No Phase 3 entry-script contract available)"
        return json.dumps(contract, indent=2, ensure_ascii=False)

    @staticmethod
    def _experience_usage_report_schema_text() -> str:
        return (
            "End your JSON with experience reporting fields: "
            "used_experience_ids (list), experience_actions_taken (list or object), "
            "ignored_experience_ids (list), ignored_reasons (object keyed by id or list). "
            "Return empty lists/objects when no experience was used or ignored."
        )

    def _build_env_context(self, state: dict[str, Any]) -> dict[str, Any]:
        env: dict[str, object] = {}
        ph0 = state.get("phase_0_env_detect", {})
        if isinstance(ph0, dict):
            env.update({k: v for k, v in ph0.items()
                        if isinstance(v, (str, int, float, bool))})
        ph2 = state.get("phase_2_venv_create", {})
        installed: object = []
        if isinstance(ph2, dict):
            installed = ph2.get("installed_packages", [])
        accel_ctx = extract_accelerator_context(installed)
        env["torch_npu_version"] = accel_ctx["torch_npu_version"]
        env["accelerator_packages"] = accel_ctx["accelerator_packages"]
        env["accelerator_package_versions"] = accel_ctx["accelerator_package_versions"]
        return env

    def _format_history_summary(self, loop_history: list[Any]) -> str:
        if not loop_history:
            return "(No previous repair attempts)"
        lines = ["| Iteration | Status | Duration | Summary | Agent Diagnostics |", "|---|---|---|---|---|"]
        for entry in loop_history:
            idx = entry.get("iteration", "?")
            stat = entry.get("status", "?")
            dur = entry.get("duration", "?")
            fixer_out = entry.get("fixer_outputs", {}) if isinstance(entry.get("fixer_outputs"), dict) else {}
            row_summary = ""
            row_diag = ""
            if fixer_out:
                summaries = []
                diags = []
                for meta in fixer_out.values():
                    if isinstance(meta, dict):
                        s = meta.get("summary", "")
                        if s:
                            summaries.append(s)
                        ad = meta.get("agent_diagnostics", "")
                        if ad:
                            if isinstance(ad, dict):
                                diags.append(json.dumps(ad, ensure_ascii=False))
                            else:
                                diags.append(str(ad))
                row_summary = "; ".join(summaries)[:100] if summaries else ""
                row_diag = "; ".join(diags)[:100] if diags else ""
            lines.append(f"| {idx} | {stat} | {dur} | {row_summary or '(none)'} | {row_diag or '(none)'} |")
        return "\n".join(lines)

    def _format_error_analyzer_history(
        self, loop_history: list[Any], step_outputs: dict[str, Any], state: dict[str, Any],
    ) -> str:
        if not loop_history:
            return "(No previous repair attempts — this is the first failure)"

        lines = [
            "| Iter | Status | Duration | Last Category | Last Repair Role | Summary | Agent Diagnostics |",
            "|------|--------|----------|---------------|------------------|---------|-------------------|",
        ]
        latest_category = "unknown"
        latest_repair_role = ""
        fixer_details: list[dict[str, Any]] = []
        for h in loop_history:
            if not isinstance(h, dict):
                continue
            row_category = str(h.get("error_category") or "unknown")
            row_repair_role = str(h.get("repair_role") or "")
            if "error_category" in h or "repair_role" in h:
                latest_category = row_category
                latest_repair_role = row_repair_role
            fixer_out = h.get("fixer_outputs", {}) if isinstance(h.get("fixer_outputs"), dict) else {}
            row_summary = ""
            row_diag = ""
            if fixer_out:
                summaries = []
                diags = []
                for pid, meta in fixer_out.items():
                    if isinstance(meta, dict):
                        s = meta.get("summary", "")
                        if s:
                            summaries.append(s)
                        ad = meta.get("agent_diagnostics", "")
                        if ad:
                            if isinstance(ad, dict):
                                diags.append(json.dumps(ad, ensure_ascii=False))
                            else:
                                diags.append(str(ad))
                        if meta.get("modified_files"):
                            fixer_details.append({
                                "iteration": h.get("iteration", "?"),
                                "phase": pid,
                                "summary": s,
                                "modified_files": meta["modified_files"],
                                "agent_diagnostics": ad,
                            })
                row_summary = "; ".join(summaries)[:120] if summaries else ""
                row_diag = "; ".join(diags)[:120] if diags else ""
            lines.append(
                f"| Iter {h.get('iteration', '?')} | {h.get('status', '?')} | "
                f"{h.get('duration', '?')} | {row_category} | {row_repair_role or '(none)'} | "
                f"{row_summary or '(none)'} | {row_diag or '(none)'} |"
            )

        if latest_category == "unknown" and not latest_repair_role:
            prev_error_analysis = state.get("error_analysis", {}) if isinstance(state, dict) else {}
            if isinstance(prev_error_analysis, dict):
                latest_category = str(prev_error_analysis.get("category") or "unknown")
                latest_repair_role = str(prev_error_analysis.get("repair_role") or "")

        lines.append(
            f"\nLatest error category: {latest_category}"
            f"{' (repair role: ' + latest_repair_role + ')' if latest_repair_role else ''}"
        )

        fix_roles = {k for k in ("fix_dependency", "fix_code", "fix_operator") if k in state}
        if fix_roles:
            lines.append(f"Previous repair roles used: {', '.join(sorted(fix_roles))}")

        if fixer_details:
            lines.append("\n## Previous Fixer Outputs")
            for fd in fixer_details:
                lines.append(f"\nIteration {fd['iteration']}, phase `{fd['phase']}`:")
                if fd.get("summary"):
                    lines.append(f"  Summary: {fd['summary']}")
                if fd.get("modified_files"):
                    lines.append(f"  Modified files: {', '.join(fd['modified_files'])}")
                diag = fd.get("agent_diagnostics")
                if diag:
                    if isinstance(diag, dict):
                        lines.append(f"  Agent Diagnostics: {json.dumps(diag, ensure_ascii=False)}")
                    else:
                        lines.append(f"  Agent Diagnostics: {diag}")

        return "\n".join(lines)

    def _collect_fixer_outputs(self, step_outputs: dict[str, Any]) -> dict[str, Any] | None:
        result: dict[str, Any] = {}
        for pid in SUB_WORKFLOW_REPAIR_PHASE_ORDER:
            out = step_outputs.get(pid)
            if not isinstance(out, dict):
                continue
            entry: dict[str, Any] = {}
            if out.get("summary"):
                entry["summary"] = str(out["summary"])
            if out.get("modified_files"):
                mf = out["modified_files"]
                entry["modified_files"] = list(mf) if isinstance(mf, list) else [str(mf)]
            if out.get("agent_diagnostics"):
                ad = out["agent_diagnostics"]
                if isinstance(ad, dict):
                    entry["agent_diagnostics"] = {str(k): str(v) for k, v in ad.items()}
                else:
                    entry["agent_diagnostics"] = str(ad)
            if entry:
                result[pid] = entry
        return result if result else None

    def _serialize_last_review(self, step_outputs: dict[str, Any]) -> str | None:
        review = step_outputs.get("review_verdict")
        if isinstance(review, dict):
            out = {"verdict": review.get("verdict", "unknown"),
                    "reasoning": review.get("reasoning", "")}
            return json.dumps(out, ensure_ascii=False)
        return None

    def _resolve_last_artifact_path(self) -> str:
        raw_dir = self.artifact_store.raw_dir
        if not os.path.isdir(raw_dir):
            return "(no artifact available)"
        existing = sorted(f for f in os.listdir(raw_dir)
                          if (f.startswith("phase_5_validation_attempt") or f.startswith("phase_run_entry_script_attempt"))
                          and f.endswith(".json"))
        if existing:
            return os.path.join(raw_dir, existing[-1])
        return "(no artifact available)"

    def _list_attempt_files(self) -> str:
        raw_dir = self.artifact_store.raw_dir
        if not os.path.isdir(raw_dir):
            return "[]"
        files = [f for f in os.listdir(raw_dir)
                 if ("phase_5_validation_attempt" in f or "phase_run_entry_script_attempt" in f)
                 and f.endswith(".json")]
        return json.dumps(files)

    def _normalize_llm_output(
        self,
        phase: PhaseDefinition,
        output: dict[str, Any],
        prompt_context: dict[str, Any],
        state: dict[str, Any],
    ) -> dict[str, Any]:
        """Inject missing fields replicating PhaseRunner._normalize_output logic."""
        normalized = dict(output)
        phase_id = phase.id

        # phase_0_env_detect: inject python_version
        if "env_detect" in phase_id or phase_id == "phase_0":
            if "python_version" not in normalized:
                normalized["python_version"] = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"

        # phase_1_project_analysis: bind project_dir to framework context and normalize route/variant signals.
        if "project_analysis" in phase_id or phase_id == "phase_1":
            normalized["project_dir"] = prompt_context.get("project_dir", self.project_dir)
            self._normalize_project_analysis_variant_count(normalized)
            normalize_serving_phase1_surface(normalized, platform_policy=self.platform_policy)

        # phase_2_venv_create: fill venv_path / python_path from filesystem when LLM omits them.
        if "venv" in phase_id or phase_id == "phase_2":
            self._normalize_phase2_venv_output(normalized)

        # phase_3_entry_script: inject entry_script_path and route-specific contracts.
        if "entry_script" in phase_id or phase_id == "phase_3":
            ph1 = state.get("phase_1_project_analysis") or state.get("phase_1")
            if "entry_script_path" not in normalized:
                if isinstance(ph1, dict) and ph1.get("entry_script"):
                    normalized["entry_script_path"] = ph1["entry_script"]
                elif prompt_context.get("entry_script"):
                    normalized["entry_script_path"] = prompt_context["entry_script"]
            workflow_globals = getattr(getattr(self, "workflow", None), "globals", None) or {}
            if self._custom_op_route_disabled(workflow_globals):
                normalized = self._strip_custom_op_contract_fields(normalized)
            else:
                if isinstance(ph1, dict) and ph1.get("migration_route") in SERVING_ROUTES:
                    normalize_serving_phase3_contract(
                        normalized,
                        route=str(ph1["migration_route"]),
                        project_dir=str(self.project_dir),
                        phase1_output=ph1,
                        platform_policy=self.platform_policy,
                    )
                elif (
                    isinstance(ph1, dict)
                    and ph1.get("migration_route") in {CUSTOM_OP, CUSTOM_OP_WITH_VARIANTS}
                ) or normalized.get("entry_script_kind") == "custom_op_full_validation" or self._custom_op_required_signal(state, prompt_context):
                    _ = normalized.setdefault("entry_script_kind", "custom_op_full_validation")
                    normalized["project_dir"] = str(self.project_dir)
                    if isinstance(ph1, dict) and ph1.get("migration_route") in {CUSTOM_OP, CUSTOM_OP_WITH_VARIANTS}:
                        normalized["migration_route"] = str(ph1["migration_route"])
                variant_overlay = expanded_variant_contract_from_outputs(state)
                if variant_overlay:
                    apply_expanded_variant_contract(normalized, variant_overlay, include_required_checks=True)
                    ensure_strict_expanded_variant_validation_script(
                        normalized,
                        variant_overlay,
                        project_dir=str(self.project_dir),
                    )
                else:
                    for field in EXPANDED_VARIANT_CONTRACT_FIELDS:
                        normalized.pop(field, None)
                    ensure_strict_non_variant_custom_op_validation_script(
                        normalized,
                        project_dir=str(self.project_dir),
                    )
            normalized = self._normalize_phase3_container_paths(
                normalized, prompt_context,
            )

        if "phase_35" in phase_id or "static_validate" in phase_id:
            phase_3_output = state.get("phase_3_entry_script")
            workflow_globals = getattr(getattr(self, "workflow", None), "globals", None) or {}
            if (
                not self._custom_op_route_disabled(workflow_globals)
                and isinstance(phase_3_output, dict)
                and phase_3_output.get("entry_script_kind") == "custom_op_full_validation"
            ):
                normalized["custom_op_static_required"] = True
                normalized["entry_script_kind"] = "custom_op_full_validation"

        if (
            not self._custom_op_route_disabled(getattr(getattr(self, "workflow", None), "globals", None) or {})
            and (phase_id == "analyze_error" or normalized.get("repair_role") in {"dependency_fixer", "code_adapter", "operator_fixer"})
        ):
            history_text = str(prompt_context.get("previous_outputs", ""))
            normalized = force_custom_op_operator_routing_if_needed(
                normalized,
                error_text=str(prompt_context.get("failure_log", "")),
                history=[history_text] if history_text else [],
                prompt_context=prompt_context,
            )

        return normalized

    def _normalize_project_analysis_variant_count(self, output: dict[str, Any]) -> None:
        normalize_phase1_project_analysis(cast(dict[str, object], output), project_dir=self.project_dir)

    def _normalize_phase2_venv_output(self, output: dict[str, Any]) -> None:
        project_dir = self.project_dir
        venv_dir = os.path.join(project_dir, ".venv")
        if not os.path.isdir(venv_dir):
            return
        if not isinstance(output.get("venv_path"), str) or not output.get("venv_path", "").strip():
            output["venv_path"] = venv_dir
        if not isinstance(output.get("python_path"), str) or not output.get("python_path", "").strip():
            for candidate in ("python3", "python"):
                candidate_path = os.path.join(venv_dir, "bin", candidate)
                if os.path.isfile(candidate_path):
                    output["python_path"] = candidate_path
                    break
        installed = output.get("installed_packages")
        if not isinstance(installed, list):
            output["installed_packages"] = []

    @staticmethod
    def _custom_op_route_disabled(workflow_globals: Mapping[str, object]) -> bool:
        if workflow_globals.get("custom_op_route_enabled") is False:
            return True
        return workflow_globals.get("disable_custom_op_contract_injection") is True

    @staticmethod
    def _strip_custom_op_contract_fields(output: dict[str, Any]) -> dict[str, Any]:
        stripped = dict(output)
        for field in CUSTOM_OP_CONTRACT_KEYS:
            stripped.pop(field, None)
        return stripped

    @classmethod
    def _custom_op_required_signal(cls, *values: object) -> bool:
        for value in values:
            signal = cls._custom_op_signal(value)
            if signal is not None:
                return signal
        return False

    @classmethod
    def _value_has_custom_op_signal(cls, value: object) -> bool:
        return cls._custom_op_signal(value) is True

    @classmethod
    def _custom_op_signal(cls, value: object) -> bool | None:
        if isinstance(value, str):
            if any(pattern.search(value) for pattern in CUSTOM_OP_NEGATIVE_PATTERNS):
                return None
            lowered = value.lower()
            if any(term.lower() in lowered for term in CUSTOM_OP_REQUIRED_TERMS):
                return True
            return None
        if isinstance(value, dict):
            if value.get("entry_script_kind") == "custom_op_full_validation":
                return True
            if value.get("custom_op_detected") is True:
                return True
            if value.get("custom_op_detected") is False:
                return False
            if any(key in value for key in CUSTOM_OP_CONTRACT_KEYS):
                return True
            custom_op_surface = value.get("custom_op_surface")
            if isinstance(custom_op_surface, dict):
                if custom_op_surface.get("custom_op_detected") is True:
                    return True
                if custom_op_surface.get("custom_op_detected") is False:
                    return False
                return cls._custom_op_signal_from_iterable(
                    item for key, item in value.items() if key not in {"_meta", "custom_op_surface"}
                )
            return cls._custom_op_signal_from_iterable(
                item for key, item in value.items() if key != "_meta"
            )
        if isinstance(value, list):
            return cls._custom_op_signal_from_iterable(value)
        if isinstance(value, tuple):
            return cls._custom_op_signal_from_iterable(value)
        if isinstance(value, set):
            return cls._custom_op_signal_from_iterable(value)
        return None

    @classmethod
    def _custom_op_signal_from_iterable(cls, values: Iterable[object]) -> bool | None:
        for item in values:
            signal = cls._custom_op_signal(item)
            if signal is not None:
                return signal
        return None

    def _normalize_phase3_container_paths(
        self,
        output: dict[str, Any],
        prompt_context: dict[str, Any],
    ) -> dict[str, Any]:
        """Rewrite host-visible path fields when the model returns container paths.

        Only targets ``entry_script_path`` and ``reports_dir``.  ``run_command``
        is NOT rewritten.
        """
        from pathlib import Path

        project_dir = prompt_context.get("project_dir") or getattr(self, "project_dir", None)
        container_workdir = (
            prompt_context.get("container_workdir")
            or prompt_context.get("container_project_dir")
        )
        if not project_dir or not container_workdir:
            return output

        if not project_dir.startswith("/"):
            try:
                project_dir = str(Path(project_dir).resolve())
            except OSError:
                return output

        normalized = dict(output)

        entry = normalized.get("entry_script_path")
        if isinstance(entry, str) and entry.strip():
            normalized["entry_script_path"] = _rewrite_container_to_host_path(
                entry, project_dir, container_workdir,
            )

        reports = normalized.get("reports_dir")
        if isinstance(reports, str) and reports.strip():
            normalized["reports_dir"] = _rewrite_container_to_host_path(
                reports, project_dir, container_workdir,
            )

        return normalized

    # ── Shell phase ─────────────────────────────────────────────────────

    _MAX_TAIL = 500_000  # 500 KB

    def _execute_shell_phase(
        self,
        phase: PhaseDefinition,
        state: dict[str, Any],
        context: dict[str, Any],
        loop_vars: dict[str, Any] | None = None,
        loop_state: dict[str, Any] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Execute a shell command with OOM-safe output tailing."""
        from core.execution_backend import ContainerBackend

        # 1. Resolve command
        cmd = self.resolver.resolve(
            getattr(phase, "command", "") or "",
            state=state,
            globals=self.workflow.globals,
            context=context,
            loop_vars=loop_vars,
            loop_state=loop_state,
        )

        # 2. Resolve cwd
        cwd = self.project_dir
        raw_cwd = getattr(phase, "cwd", None)
        if isinstance(raw_cwd, str) and raw_cwd.strip():
            cwd = str(self.resolver.resolve(
                raw_cwd,
                state=state,
                globals=self.workflow.globals,
                context=context,
                loop_vars=loop_vars,
                loop_state=loop_state,
            ))
        elif isinstance(cmd, dict) and isinstance(cmd.get("cwd"), str):
            cwd = cmd["cwd"]

        entry_script_command = self._is_phase5_entry_script_command(phase, loop_vars)
        variant_validate_script = self._is_variant_validate_script_command(cmd, state)
        if entry_script_command or variant_validate_script:
            cmd = self._refresh_expanded_variant_entry_script_command(cmd, state, context, loop_vars)
        entry_script_command = entry_script_command or variant_validate_script
        timeout = phase.timeout

        # Container backend path
        if isinstance(self.exec_backend, ContainerBackend):
            return self._execute_shell_phase_container(
                phase, cmd, cwd, entry_script_command, timeout, state, context,
                loop_vars=loop_vars, loop_state=loop_state,
            )

        # Local path (existing code, unchanged)
        return self._execute_shell_phase_local(
            phase, cmd, cwd, entry_script_command, timeout, state, context,
            loop_vars=loop_vars, loop_state=loop_state,
        )

    def _refresh_expanded_variant_entry_script_command(
        self,
        cmd: object,
        state: dict[str, Any],
        context: dict[str, Any],
        loop_vars: dict[str, Any] | None,
    ) -> object:
        contract = state.get("phase_3_entry_script")
        if not isinstance(contract, dict):
            return cmd
        overlay = expanded_variant_contract_from_outputs({"phase_3_entry_script": contract})
        if not overlay:
            return cmd
        refreshed = dict(contract)
        project_dir = str(loop_vars.get("project_dir") or context.get("PROJECT_DIR") or self.project_dir) if isinstance(loop_vars, dict) else str(context.get("PROJECT_DIR") or self.project_dir)
        refreshed["project_dir"] = project_dir
        apply_expanded_variant_contract(refreshed, overlay, include_required_checks=True)
        ensure_strict_expanded_variant_validation_script(refreshed, overlay, project_dir=project_dir)
        command = refreshed.get("run_command")
        if not isinstance(command, str) or not command.strip():
            return cmd
        state["phase_3_entry_script"] = refreshed
        if loop_vars is not None:
            loop_vars["entry_script"] = command
        return command

    def _execute_shell_phase_container(
        self,
        phase: PhaseDefinition,
        cmd: Any,
        cwd: str,
        entry_script_command: bool,
        timeout: int | None,
        state: dict[str, Any],
        context: dict[str, Any],
        *,
        loop_vars: dict[str, Any] | None = None,
        loop_state: dict[str, Any] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        backend: ContainerBackend = self.exec_backend
        run_cmd: str | list[str]
        run_env: dict[str, str] | None = None
        if entry_script_command:
            tokens = shlex.split(str(cmd))
            run_env, stripped = _extract_env_prefix(str(cmd))
            if stripped:
                run_cmd = shlex.split(stripped)
            else:
                run_cmd = tokens
        else:
            run_cmd = str(cmd)

        preflight_result = self._custom_op_opp_preflight_for_entry_script(
            state,
            context,
            loop_vars,
            entry_script_command or getattr(phase, "id", "") == "run_entry_script",
        )
        if preflight_result is not None and preflight_result.get("passed") is not True:
            stderr = format_custom_op_opp_preflight_failure(preflight_result)
            captured = {
                "exit_code": 1,
                "stdout": "",
                "stderr": stderr,
                "duration": 0,
                "command": str(cmd),
                "custom_op_opp_preflight": preflight_result,
            }
            if loop_state is not None:
                loop_state["script_exit_code"] = 1
                loop_state["script_stdout"] = ""
                loop_state["script_stderr"] = stderr
                loop_state["script_duration"] = 0
                loop_state["custom_op_opp_preflight"] = preflight_result
            on_failure = phase.on_failure if hasattr(phase, "on_failure") else "continue"
            if on_failure != "break":
                return ("success", captured)
            return ("failure", captured)

        try:
            result = backend.run(
                run_cmd, cwd=cwd, env=run_env or None, timeout=timeout,
            )
            exit_code = result.exit_code
            stdout = result.stdout
            stderr = result.stderr
            duration = result.duration
        except subprocess.TimeoutExpired:
            exit_code = 124
            duration = timeout if timeout else 0
            stdout = ""
            stderr = f"Execution timed out after {timeout}s"
        except Exception as exc:
            exit_code = 1
            duration = 0
            stdout = ""
            stderr = str(exc)

        captured = {
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "duration": round(duration, 3),
            "command": str(cmd),
        }

        if loop_state is not None:
            loop_state["script_exit_code"] = exit_code
            loop_state["script_stdout"] = stdout
            loop_state["script_stderr"] = stderr
            loop_state["script_duration"] = captured["duration"]

        on_failure = phase.on_failure if hasattr(phase, "on_failure") else "continue"
        if exit_code != 0 and on_failure != "break":
            return ("success", captured)
        if exit_code != 0:
            return ("failure", captured)
        return ("success", captured)

    def _execute_shell_phase_local(
        self,
        phase: PhaseDefinition,
        cmd: Any,
        cwd: str,
        entry_script_command: bool,
        timeout: int | None,
        state: dict[str, Any],
        context: dict[str, Any],
        *,
        loop_vars: dict[str, Any] | None = None,
        loop_state: dict[str, Any] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        run_cmd: str | list[str]
        run_shell = not entry_script_command
        run_env: dict[str, str] | None = None
        if entry_script_command:
            tokens = shlex.split(str(cmd))
            run_env, stripped = _extract_env_prefix(str(cmd))
            if stripped:
                run_cmd = shlex.split(stripped)
            else:
                run_cmd = tokens
            run_shell = False
        else:
            run_cmd = str(cmd)

        preflight_result = self._custom_op_opp_preflight_for_entry_script(
            state,
            context,
            loop_vars,
            entry_script_command or getattr(phase, "id", "") == "run_entry_script",
        )
        if preflight_result is not None and preflight_result.get("passed") is not True:
            stderr = format_custom_op_opp_preflight_failure(preflight_result)
            captured = {
                "exit_code": 1,
                "stdout": "",
                "stderr": stderr,
                "duration": 0,
                "command": str(cmd),
                "custom_op_opp_preflight": preflight_result,
            }
            if loop_state is not None:
                loop_state["script_exit_code"] = 1
                loop_state["script_stdout"] = ""
                loop_state["script_stderr"] = stderr
                loop_state["script_duration"] = 0
                loop_state["custom_op_opp_preflight"] = preflight_result
            on_failure = phase.on_failure if hasattr(phase, "on_failure") else "continue"
            if on_failure != "break":
                return ("success", captured)
            return ("failure", captured)

        out_path = err_path = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".out", delete=False) as out_f, \
                 tempfile.NamedTemporaryFile(mode="w", suffix=".err", delete=False) as err_f:
                out_path = out_f.name
                err_path = err_f.name

            start_t = time.time()
            env_for_subprocess = None
            if run_env:
                env_for_subprocess = {**os.environ, **run_env}
            result = subprocess.run(
                run_cmd, shell=run_shell, cwd=cwd, env=env_for_subprocess,
                stdout=open(out_path, "w"), stderr=open(err_path, "w"),
                timeout=timeout,
            )
            duration = time.time() - start_t

            exit_code = result.returncode

            stdout = self._read_tail(out_path)
            stderr = self._read_tail(err_path)

        except subprocess.TimeoutExpired:
            exit_code = 124
            duration = timeout if timeout is not None else 0
            stdout = self._read_tail(out_path) if out_path else ""
            stderr = self._read_tail(err_path) if err_path else ""
        except Exception as exc:
            exit_code = 1
            duration = 0.0
            stdout = ""
            stderr = str(exc)

        finally:
            for p in (out_path, err_path):
                if p and os.path.exists(p):
                    try:
                        os.unlink(p)
                    except OSError:
                        pass

        captured = {
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "duration": round(duration, 3),
            "command": str(cmd),
        }

        if loop_state is not None:
            loop_state["script_exit_code"] = exit_code
            loop_state["script_stdout"] = stdout
            loop_state["script_stderr"] = stderr
            loop_state["script_duration"] = captured["duration"]

        on_failure = phase.on_failure if hasattr(phase, "on_failure") else "continue"
        if exit_code != 0 and on_failure != "break":
            return ("success", captured)
        if exit_code != 0:
            return ("failure", captured)
        return ("success", captured)

    @staticmethod
    def _is_phase5_entry_script_command(phase: PhaseDefinition, loop_vars: dict[str, Any] | None) -> bool:
        if getattr(phase, "id", "") != "run_entry_script":
            return False
        raw_command = getattr(phase, "command", "")
        if raw_command == "${loop_vars.entry_script}":
            return True
        return bool(loop_vars and str(loop_vars.get("entry_script", "")) == str(raw_command))

    @staticmethod
    def _is_variant_validate_script_command(cmd: object, state: dict[str, Any]) -> bool:
        cmd_str = str(cmd) if not isinstance(cmd, dict) else ""
        if "validate_custom_ops_full.py" not in cmd_str:
            return False
        contract = state.get("phase_3_entry_script")
        if not isinstance(contract, dict):
            return False
        return contract.get("entry_script_kind") == "custom_op_full_validation"

    def _custom_op_opp_preflight_for_entry_script(
        self,
        state: dict[str, Any],
        context: dict[str, Any],
        loop_vars: dict[str, Any] | None,
        entry_script_command: bool,
    ) -> dict[str, object] | None:
        if not entry_script_command:
            return None
        contract = state.get("phase_3_entry_script")
        if not isinstance(contract, dict) or not has_custom_op_contract(contract):
            return None
        contract_map = cast(dict[str, object], contract)
        if not self._requires_custom_op_opp_preflight(contract_map):
            return None
        project_dir = self.project_dir
        if loop_vars and isinstance(loop_vars.get("project_dir"), str):
            project_dir = str(loop_vars["project_dir"])
        elif isinstance(context.get("PROJECT_DIR"), str):
            project_dir = str(context["PROJECT_DIR"])
        project_path = Path(project_dir)
        if project_path.is_dir():
            ensure_opp_source_evidence(project_path)
        return validate_custom_op_opp_preflight(contract_map, project_dir)

    def _requires_custom_op_opp_preflight(self, contract: dict[str, object]) -> bool:
        if self.platform_policy.id != "npu_ascend":
            return False
        policy = contract.get("custom_op_evidence_policy")
        if isinstance(policy, str) and "require_real_ascend_cann_acl_opp_native_artifacts" in policy.lower():
            return True
        variant_overlay = expanded_variant_contract_from_outputs({"phase_3_entry_script": contract})
        if variant_overlay:
            return True
        strict_fields = (
            "strict_expanded_variant_validation",
            "strict_expanded_variant_closure",
            "expanded_variant_static_required",
            "expanded_variant_runtime_required",
            "expanded_variant_contract",
        )
        return any(contract.get(field) not in (None, False, "", [], {}) for field in strict_fields)

    def _read_tail(self, path: str, max_bytes: int = _MAX_TAIL) -> str:
        """Read at most last *max_bytes* of a file."""
        try:
            with open(path, "rb") as f:
                f.seek(max(0, os.path.getsize(path) - max_bytes))
                return f.read().decode("utf-8", errors="replace")
        except (OSError, IOError):
            return ""

    # ── Builtin phase ───────────────────────────────────────────────────

    def _execute_builtin_phase(
        self,
        phase: PhaseDefinition,
        state: dict[str, Any],
        context: dict[str, Any],
        loop_vars: dict[str, Any] | None = None,
        loop_state: dict[str, Any] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Execute a builtin operation."""
        _params: dict[str, Any] = getattr(phase, "params", {}) or {}
        operation = _params.get("operation", "")
        if not isinstance(operation, str):
            operation = ""

        if operation == "stagnation_check":
            error_output = ""
            if loop_state:
                error_output = loop_state.get("script_stderr", "") or loop_state.get("last_error", "")
            error_sig = self._normalize_error_signature(error_output)
            if loop_state:
                loop_state["last_error_signature"] = error_sig
            return ("success", {"operation": operation, "error_signature": error_sig})

        if operation == "rule_based_migration":
            backend = _params.get("backend", "").lower() if isinstance(_params.get("backend"), str) else ""
            workflow_rule_migration = getattr(self.workflow, "rule_migration", None)
            platform_strategy = self.platform_policy.default_rule_migration_strategy

            migrator = cast(Any, create_migrator_resolved(
                workflow_params_backend=backend if backend else None,
                workflow_rule_migration=workflow_rule_migration,
                platform_policy_strategy=platform_strategy,
            ))
            result = migrator.migrate_directory(
                self.project_dir,
                pattern=str(_params.get("pattern", "*.py")),
            )
            strategy_id = resolve_rule_migration_strategy(
                workflow_params_backend=backend if backend else None,
                workflow_rule_migration=workflow_rule_migration,
                platform_policy_strategy=platform_strategy,
            )
            return ("success", {"operation": operation, "result": result, "backend": backend or None, "strategy": strategy_id})

        if operation == "ppu_rule_based_migration":
            pattern = _params.get("pattern", "*.py")
            migrator = PPURuleBasedMigrator()
            result = migrator.migrate_directory(self.project_dir, pattern=str(pattern))
            return ("success", {"operation": operation, "result": result, "backend": "ppu"})

        if operation == "custom_op_final_gate":
            return self._execute_custom_op_final_gate(state, context, loop_vars, loop_state)

        if operation == "serving_final_gate":
            return self._execute_serving_final_gate(state, context, loop_vars, loop_state)

        # Generic: just return
        if not operation:
            return (
                "failure",
                {"error": f"Builtin phase '{phase.id}' is missing required operation", "operation": ""},
            )

        return ("success", {"operation": operation, "result": {}})

    def _execute_custom_op_final_gate(
        self,
        state: dict[str, Any],
        context: dict[str, Any],
        loop_vars: dict[str, Any] | None,
        loop_state: dict[str, Any] | None,
    ) -> tuple[str, dict[str, Any]]:
        contract = state.get("phase_3_entry_script")
        if not isinstance(contract, dict) or not self._has_custom_op_contract(contract):
            result = {"operation": "custom_op_final_gate", "skipped": True, "passed": True}
            if loop_state is not None:
                loop_state["custom_op_final_gate"] = result
            return "success", result

        reports_dir = self._resolve_custom_op_reports_dir(contract, context, loop_vars)
        gate_path = reports_dir / "custom_op_final_gate.json"
        result: dict[str, Any] = {
            "operation": "custom_op_final_gate",
            "skipped": False,
            "path": str(gate_path),
            "passed": False,
            "errors": [],
        }

        if not gate_path.exists():
            result["errors"] = [f"custom-op final gate report missing: {gate_path}"]
            self._record_custom_op_gate_failure(loop_state, result)
            return "success", result
        try:
            gate_size = gate_path.stat().st_size
        except OSError as exc:
            result["errors"] = [f"custom-op final gate report could not be stat'ed: {exc}"]
            self._record_custom_op_gate_failure(loop_state, result)
            return "success", result
        if gate_size > _CUSTOM_OP_GATE_REPORT_MAX_BYTES:
            result["errors"] = [f"custom-op final gate report too large: {gate_path}"]
            self._record_custom_op_gate_failure(loop_state, result)
            return "success", result

        try:
            with gate_path.open("r", encoding="utf-8") as handle:
                gate_data = cast(object, json.load(handle))
        except (OSError, json.JSONDecodeError) as exc:
            result["errors"] = [f"custom-op final gate report could not be read: {exc}"]
            self._record_custom_op_gate_failure(loop_state, result)
            return "success", result

        if not isinstance(gate_data, dict):
            result["errors"] = ["custom-op final gate report must be a JSON object"]
            self._record_custom_op_gate_failure(loop_state, result)
            return "success", result

        gate_map = cast(dict[str, object], gate_data)
        validation = validate_custom_op_final_gate(
            gate_map, project_root=reports_dir.parent,
            platform_policy=self.platform_policy,
        )
        result["passed"] = validation["passed"]
        result["errors"] = validation["errors"]
        result["summary"] = {
            "inventory_count": gate_map.get("inventory_count"),
            "manifest_entries": gate_map.get("manifest_entries"),
            "closed_pass_entries": gate_map.get("closed_pass_entries"),
            "remaining_entries": gate_map.get("remaining_entries"),
            "full_migration_status": gate_map.get("full_migration_status"),
        }
        if loop_state is not None:
            loop_state["custom_op_final_gate"] = result
        if not validation["passed"]:
            self._record_custom_op_gate_failure(loop_state, result)
        return "success", result

    def _execute_serving_final_gate(
        self,
        state: dict[str, Any],
        context: dict[str, Any],
        loop_vars: dict[str, Any] | None,
        loop_state: dict[str, Any] | None,
    ) -> tuple[str, dict[str, Any]]:
        contract = state.get("phase_3_entry_script")
        if not isinstance(contract, dict):
            result = {"operation": "serving_final_gate", "skipped": True, "passed": True, "route": None}
            if loop_state is not None:
                loop_state["serving_final_gate"] = result
            return "success", result

        route = serving_route_from_contract(cast(dict[str, object], contract))
        if route not in SERVING_ROUTES:
            result = {"operation": "serving_final_gate", "skipped": True, "passed": True, "route": route}
            if loop_state is not None:
                loop_state["serving_final_gate"] = result
            return "success", result

        gate_path = self._resolve_serving_final_gate_path(cast(dict[str, Any], contract), context, loop_vars)
        result: dict[str, Any] = {
            "operation": "serving_final_gate",
            "skipped": False,
            "path": str(gate_path),
            "passed": False,
            "route": route,
            "errors": [],
        }

        if not gate_path.exists():
            result["errors"] = [f"serving final gate report missing: {gate_path}"]
            self._record_serving_gate_failure(loop_state, result)
            return "success", result
        try:
            gate_size = gate_path.stat().st_size
        except OSError as exc:
            result["errors"] = [f"serving final gate report could not be stat'ed: {exc}"]
            self._record_serving_gate_failure(loop_state, result)
            return "success", result
        if gate_size > _CUSTOM_OP_GATE_REPORT_MAX_BYTES:
            result["errors"] = [f"serving final gate report too large: {gate_path}"]
            self._record_serving_gate_failure(loop_state, result)
            return "success", result

        try:
            with gate_path.open("r", encoding="utf-8") as handle:
                gate_data = cast(object, json.load(handle))
        except (OSError, json.JSONDecodeError) as exc:
            result["errors"] = [f"serving final gate report could not be read: {exc}"]
            self._record_serving_gate_failure(loop_state, result)
            return "success", result

        if not isinstance(gate_data, dict):
            result["errors"] = ["serving final gate report must be a JSON object"]
            self._record_serving_gate_failure(loop_state, result)
            return "success", result

        gate_map = cast(dict[str, object], gate_data)
        validation = validate_serving_final_gate(gate_map, expected_route=route, platform_policy=self.platform_policy)
        result["passed"] = validation["passed"]
        result["errors"] = validation["errors"]
        result["summary"] = {
            "migration_route": gate_map.get("migration_route"),
            "serving_framework": gate_map.get("serving_framework"),
            "serving_backend": gate_map.get("serving_backend"),
            "full_migration_status": gate_map.get("full_migration_status"),
        }
        if loop_state is not None:
            loop_state["serving_final_gate"] = result
        if not validation["passed"]:
            self._record_serving_gate_failure(loop_state, result)
        return "success", result

    @staticmethod
    def _has_custom_op_contract(contract: dict[str, Any]) -> bool:
        return any(
            field in contract
            for field in (
                "entry_script_kind",
                "reports_dir",
                "required_report_paths",
                "required_checks",
            )
        )

    def _resolve_custom_op_reports_dir(
        self,
        contract: dict[str, Any],
        context: dict[str, Any],
        loop_vars: dict[str, Any] | None,
    ) -> Path:
        project_dir = None
        if loop_vars and isinstance(loop_vars.get("project_dir"), str):
            project_dir = loop_vars["project_dir"]
        elif isinstance(context.get("PROJECT_DIR"), str):
            project_dir = context["PROJECT_DIR"]
        else:
            project_dir = self.project_dir
        return Path(str(project_dir)).resolve() / "migration_reports"

    def _resolve_serving_final_gate_path(
        self,
        contract: dict[str, Any],
        context: dict[str, Any],
        loop_vars: dict[str, Any] | None,
    ) -> Path:
        project_dir = None
        if loop_vars and isinstance(loop_vars.get("project_dir"), str):
            project_dir = loop_vars["project_dir"]
        elif isinstance(context.get("PROJECT_DIR"), str):
            project_dir = context["PROJECT_DIR"]
        else:
            project_dir = self.project_dir

        project_path = Path(str(project_dir)).resolve()
        reports_root = (project_path / "migration_reports").resolve()

        def resolve_under_reports(candidate: Path) -> Path | None:
            resolved = candidate.resolve()
            try:
                _ = resolved.relative_to(reports_root)
            except ValueError:
                return None
            return resolved

        report_paths = contract.get("required_report_paths")
        if isinstance(report_paths, list):
            for item in cast(list[object], report_paths):
                if isinstance(item, str) and "serving_final_gate" in item:
                    candidate = Path(item).expanduser()
                    resolved = resolve_under_reports(candidate if candidate.is_absolute() else project_path / candidate)
                    if resolved is not None:
                        return resolved

        reports_dir = contract.get("serving_reports_dir")
        if isinstance(reports_dir, str) and reports_dir.strip():
            candidate = Path(reports_dir).expanduser()
            base = candidate if candidate.is_absolute() else project_path / candidate
            resolved_base = resolve_under_reports(base)
            if resolved_base is not None:
                return resolved_base / "serving_final_gate.json"

        return reports_root / "serving_final_gate.json"

    @staticmethod
    def _record_custom_op_gate_failure(loop_state: dict[str, Any] | None, result: dict[str, Any]) -> None:
        if loop_state is None:
            return
        loop_state["script_exit_code"] = 1
        errors = result.get("errors")
        if isinstance(errors, list) and errors:
            concise = "; ".join(str(error) for error in errors[:5])
        else:
            concise = "custom-op final gate failed"
        gate_message = f"Custom-op final evidence gate failed: {concise}"
        existing_stderr = str(loop_state.get("script_stderr") or "")
        loop_state["script_stderr"] = f"{existing_stderr}\n{gate_message}".strip()
        loop_state["custom_op_final_gate"] = result

    @staticmethod
    def _record_serving_gate_failure(loop_state: dict[str, Any] | None, result: dict[str, Any]) -> None:
        if loop_state is None:
            return
        loop_state["script_exit_code"] = 1
        errors = result.get("errors")
        if isinstance(errors, list) and errors:
            concise = "; ".join(str(error) for error in errors[:5])
        else:
            concise = "serving final gate failed"
        gate_message = f"Serving final evidence gate failed: {concise}"
        existing_stderr = str(loop_state.get("script_stderr") or "")
        loop_state["script_stderr"] = f"{existing_stderr}\n{gate_message}".strip()
        loop_state["serving_final_gate"] = result

    # ── Python phase ────────────────────────────────────────────────────

    _WHITELISTED_PYTHON_OPS = frozenset(
        {"snapshot_project", "copy_artifacts", "write_summary"}
    )

    def _execute_python_phase(
        self,
        phase: PhaseDefinition,
        state: dict[str, Any],
        context: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        """Execute a whitelisted Python builtin operation."""
        params = getattr(phase, "params", {}) or {}
        operation = params.get("operation", "")

        if operation not in self._WHITELISTED_PYTHON_OPS:
            return ("failure", {"error": f"Operation '{operation}' not whitelisted",
                                "allowed": list[Any](self._WHITELISTED_PYTHON_OPS)})

        hook_ctx = {**context, "state": state, "phase_results": self.phase_results,
                    "telemetry_bridge": self.telemetry_bridge}
        hook_params = {"project_dir": self.project_dir, **params}
        try:
            result = self.hook_manager._dispatch_builtin(operation, hook_params, hook_ctx)
            return ("success", result)
        except Exception as exc:
            return ("failure", {"error": str(exc), "operation": operation})

    # ── Review phase ────────────────────────────────────────────────────

    def _execute_review_phase(
        self,
        phase: PhaseDefinition,
        state: dict[str, Any],
        context: dict[str, Any],
        loop_vars: dict[str, Any],
        loop_state: dict[str, Any],
        loop_history: list[Any],
        sub_workflow_def: SubWorkflowDefinition | None,
        verdicts_cfg: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute a review gate: get verdict, route accept/reject."""
        max_retry = 2  # retry_json_parse

        # 1. Get review session
        agent_id = phase.agent or "main_engineer"
        if self.session_registry:
            try:
                sid = self.session_registry.resolve(agent_id)
            except KeyError:
                sid = self.session_mgr.get_or_create(role=agent_id, lifecycle="persistent")
        else:
            sid = self.session_mgr.get_or_create(role=agent_id, lifecycle="persistent")

        # 2. Build prompt context
        review_ctx = {
            "project_dir": self.project_dir,
            "repair_history": self._format_loop_history(loop_history),
            "attempt_log_content": loop_state.get("script_stderr", "") or loop_state.get("script_stdout", ""),
            "execution_duration": str(loop_state.get("script_duration", "not available")),
            "review_reject_count": loop_state.get("review_reject_count", 0),
            "iteration": loop_state.get("iteration", 0),
            "last_artifact_path": self._resolve_last_artifact_path(),
        }
        entry_script = loop_vars.get("entry_script", "")
        es = str(entry_script)
        exec_cmd: str | list[str] = shlex.split(es) if isinstance(self.exec_backend, ContainerBackend) else es
        review_ctx.update(_get_exec_ctx(self.exec_backend, command=exec_cmd))
        review_ctx.update(
            self._resolve_input_mapping(phase, state, context,
                                        loop_vars=loop_vars, loop_state=loop_state,
                                        loop_history=loop_history)
        )
        self._inject_container_env_context(review_ctx)
        self._inject_execution_environment_context(review_ctx)

        prompt_text = self.prompt_loader.load_prompt(phase.prompt_template, review_ctx)
        prompt_text, _explicit_skill_bundle = self._append_explicit_runtime_skill_markdown(
            prompt_text, phase, agent_id
        )

        # 3. Send command with JSON parse retry
        parsed: dict[str, Any] = {}
        active_prompt = prompt_text
        for attempt in range(1, max_retry + 1):
            raw_response = self.session_mgr.send_command(sid, active_prompt, timeout=phase.timeout)
            parsed = extract_json_response(raw_response)
            self._raise_for_session_error_output(parsed, phase.id)
            verdict = str(parsed.get("verdict", "")).lower()
            if verdict in ("accept", "reject"):
                break
            if attempt < max_retry:
                active_prompt = (
                    "Your previous response could not be parsed as valid JSON "
                    "or was missing a valid verdict.\n"
                    "Please return valid JSON with verdict field."
                )

        # 5. Parse verdict
        if not parsed:
            parsed = {"verdict": "unknown", "reasoning": "Failed to parse response"}
        verdict = str(parsed.get("verdict", "unknown")).lower()
        reasoning = parsed.get("reasoning", "")

        # 6. Route based on verdict
        verdicts = verdicts_cfg or {
            "accept": "success",
            "reject": "reject",
            "accept_with_warning": "success",
        }

        if verdict in ("accept", "accept_with_warning"):
            status = "success"
            if "review_verdict_status" not in loop_state:
                loop_state["review_verdict_status"] = "accept"
        elif verdict == "reject":
            # Snapshot project
            try:
                self.hook_manager._dispatch_builtin(
                    "snapshot_project",
                    {"project_dir": self.project_dir},
                    {"PROJECT_DIR": self.project_dir},
                )
            except Exception as exc:
                logger.warning("Review reject snapshot failed: %s", exc)

            rc = loop_state.get("review_reject_count", 0) + 1
            loop_state["review_reject_count"] = rc
            status = "reject"
            loop_state["review_verdict_status"] = "reject"
        else:
            status = "unknown"

        # 7. Store in loop_state
        loop_state["review_verdict"] = {
            "verdict": verdict,
            "reasoning": reasoning,
            "status": status,
        }

        return {"verdict": verdict, "reasoning": reasoning, "status": status}

    def _format_loop_history(self, loop_history: list[Any]) -> str:
        """Format loop history into a markdown-style summary."""
        if not loop_history:
            return "(No repair history)"
        lines = ["| Iteration | Status | Duration |", "|---|---|---|"]
        for entry in loop_history:
            idx = entry.get("iteration", "?")
            stat = entry.get("status", "?")
            dur = entry.get("duration", "?")
            lines.append(f"| {idx} | {stat} | {dur} |")
        return "\n".join(lines)

    # ── Dispatch phase ──────────────────────────────────────────────────

    def _execute_dispatch_phase(
        self,
        phase: PhaseDefinition,
        state: dict[str, Any],
        context: dict[str, Any],
        loop_vars: dict[str, Any],
        loop_state: dict[str, Any],
        step_outputs: dict[str, Any],
    ) -> str | None:
        """Resolve dispatch routing: read a field value → look up target."""
        params = getattr(phase, "params", {}) or {}
        route_field_template = params.get("route_field", "")

        # 1. Resolve route_field template
        route_value = self.resolver.resolve(
            route_field_template,
            state=state,
            globals=self.workflow.globals,
            context=context,
            loop_vars=loop_vars,
            loop_state=loop_state,
            step_outputs=step_outputs,
        )
        if isinstance(route_value, dict):
            # If the resolution itself returned a dict, try to extract a string
            route_value = str(route_value.get("value", route_value.get("role", "")))
        route_key = str(route_value)

        # 2. Look up in phase.routes (stored in params.routes or phase.transitions)
        routes = params.get("routes", {})
        if not routes and hasattr(phase, "transitions") and phase.transitions:
            routes = phase.transitions

        # 3. Return target if found
        target = routes.get(route_key)
        if target:
            logger.info("Dispatch routing: '%s' → '%s'", route_key, target)
            return target

        # 4. Not found — warn
        logger.warning("Dispatch route '%s' not found in %s", route_key, list(routes.keys()))
        return None

    # ── Loop phase ──────────────────────────────────────────────────────

    def _absorb_passed_final_gate_success(
        self,
        loop_state: dict[str, Any],
        step_outputs: dict[str, Any],
    ) -> bool:
        for gate_key in ("serving_final_gate", "custom_op_final_gate"):
            gate_result = step_outputs.get(gate_key)
            if not isinstance(gate_result, dict):
                gate_result = loop_state.get(gate_key)
            if (
                isinstance(gate_result, dict)
                and gate_result.get("passed") is True
                and gate_result.get("skipped") is not True
            ):
                loop_state["script_exit_code"] = 0
                step_outputs["script_exit_code"] = 0
                loop_state.pop("script_stderr", None)
                step_outputs.pop("script_stderr", None)
                loop_state.pop("last_error", None)
                step_outputs.pop("last_error", None)
                return True
        return False

    def _execute_loop_phase(self, phase: PhaseDefinition, state: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        """Execute a loop-type phase with sub-workflow, stop conditions, stagnation."""
        params = getattr(phase, "params", {}) or {}
        sub_wf_name = phase.sub_workflow
        if isinstance(params, dict) and params.get("sub_workflow"):
            sub_wf_name = params["sub_workflow"]

        # 1. Load sub-workflow definition
        sub_wf_def = self.workflow.sub_workflows.get(sub_wf_name) if sub_wf_name else None
        if sub_wf_def is None:
            return {"status": "failure", "error": f"Sub-workflow '{sub_wf_name}' not found"}

        # 2. Parse input_mapping → build loop_vars
        loop_vars = self._resolve_input_mapping(phase, state, context)

        # 3. Initialize loop state
        loop_state: dict[str, Any] = {"stagnation_count": 0}
        loop_history: list[dict[str, Any]] = []
        review_reject_count = 0
        stagnation_threshold = int(
            sub_wf_def.stagnation_threshold if isinstance(sub_wf_def.stagnation_threshold, (int, float))
            else self.framework_config.get("stagnation_threshold", 3)
        )

        sub_wf_phases = sub_wf_def.phases if isinstance(sub_wf_def.phases, list) else []
        sub_wf_blocks = sub_wf_def.blocks if isinstance(sub_wf_def.blocks, dict) else {}

        # Resolve max_iterations: CLI globals override > YAML definition > framework defaults
        globals_override = (self.workflow.globals or {}).get("max_repair_iterations")
        max_iter_raw = globals_override if globals_override else sub_wf_def.max_iterations
        if isinstance(max_iter_raw, str):
            max_iterations = int(max_iter_raw)
        elif isinstance(max_iter_raw, int):
            max_iterations = max_iter_raw
        else:
            max_iterations = self.framework_config.get("max_iterations", 10)

        review_gate_enabled = bool(
            sub_wf_def.review_gate_enabled
            if isinstance(sub_wf_def.review_gate_enabled, bool)
            else self.framework_config.get("review", {}).get("enabled", False)
        )
        max_review_iterations = int(
            sub_wf_def.max_review_iterations
            if isinstance(sub_wf_def.max_review_iterations, (int, float))
            else self.framework_config.get("review", {}).get("max_review_iterations", 3)
        )

        max_entry_script_revisions = self._max_entry_script_revisions()
        loop_state["max_entry_script_revisions"] = max_entry_script_revisions
        loop_state["entry_script_revision_count"] = 0
        loop_state["entry_script_revision_requests"] = []

        # 4. Iterate
        final_status = "success"
        for iteration in range(1, max_iterations + 1):
            logger.info("Loop iteration %d/%d for phase '%s'", iteration, max_iterations, phase.id)
            iter_start = time.time()
            step_outputs: dict[str, Any] = {}
            self._carry_pending_experience_verifications(loop_state, step_outputs)

            # Execute sub-workflow
            iter_result = self._run_sub_workflow(
                sub_wf_def, loop_vars, state, context, sub_wf_phases,
                sub_wf_blocks, step_outputs, loop_history, loop_state,
            )
            iter_duration = time.time() - iter_start
            iter_status = iter_result.get("status", "success")

            # Merge step_outputs for next iterations
            loop_state.update(iter_result.get("step_outputs", {}))
            step_outputs.update(iter_result.get("step_outputs", {}))
            # Persist operator_fixer routing across iterations so the elevated
            # stagnation threshold survives condition-skips in later iterations.
            if step_outputs.get("error_analysis", {}).get("repair_role") == "operator_fixer":
                loop_state["_custom_op_operator_repair_ever_active"] = True
            self._absorb_passed_final_gate_success(loop_state, step_outputs)
            self._stamp_pending_experience_verifications(loop_state, iteration)
            verification_signal = self._record_pending_experience_verification(
                loop_state, step_outputs, iteration
            )

            # Record iteration
            history_entry = {
                "iteration": iteration,
                "status": iter_status,
                "duration": round(iter_duration, 3),
                "step_outputs_summary": {k: type(v).__name__ for k, v in step_outputs.items()},
                "experience_usage": self._summarize_iteration_experience_usage(step_outputs),
            }
            error_analysis = step_outputs.get("error_analysis")
            if isinstance(error_analysis, dict):
                error_category = error_analysis.get("category")
                repair_role = error_analysis.get("repair_role")
                if error_category:
                    history_entry["error_category"] = str(error_category)
                if repair_role:
                    history_entry["repair_role"] = str(repair_role)
            revision_result = step_outputs.get("entry_script_action_result")
            if isinstance(revision_result, dict):
                history_entry["entry_script_action"] = revision_result
            if verification_signal:
                history_entry["experience_verification"] = verification_signal
            fixer_outputs = self._collect_fixer_outputs(step_outputs)
            if fixer_outputs:
                history_entry["fixer_outputs"] = fixer_outputs
            loop_history.append(history_entry)
            loop_state["iteration"] = iteration

            # 4b. Check stop conditions
            stop_conds = sub_wf_def.stop_conditions if isinstance(sub_wf_def.stop_conditions, list) else []
            stop_status = self._check_stop_conditions(
                stop_conds, loop_state, self.workflow.globals or {}
            )
            if stop_status:
                # When the effective stagnation threshold is elevated
                # (e.g. operator_fixer routing), suppress the YAML
                # hardcoded stop condition so the elevated threshold has
                # a chance to take effect.
                if stop_status == "stagnation":
                    effective = self._effective_stagnation_threshold(
                        base_threshold=stagnation_threshold,
                        phase_id=phase.id,
                        state=state,
                        step_outputs=step_outputs,
                        loop_state=loop_state,
                    )
                    current_count = loop_state.get("stagnation_count", 0)
                    if current_count < effective:
                        logger.info(
                            "Stop condition 'stagnation' overridden: count=%d < effective_threshold=%d",
                            current_count, effective,
                        )
                        stop_status = None
                if stop_status:
                    final_status = stop_status
                    logger.info("Stop condition matched: '%s'", stop_status)
                    break

            if step_outputs.get("entry_script_revision_applied"):
                loop_state["stagnation_count"] = 0
                continue

            # 4c. Stagnation check (builtin)
            error_sig = self._normalize_error_signature(
                loop_state.get("script_stderr", "") or loop_state.get("last_error", "")
            )
            effective_stagnation_threshold = self._effective_stagnation_threshold(
                base_threshold=stagnation_threshold,
                phase_id=phase.id,
                state=state,
                step_outputs=step_outputs,
                loop_state=loop_state,
            )
            if self._check_stagnation(error_sig, loop_state, effective_stagnation_threshold):
                final_status = "stagnation"
                logger.warning("Stagnation detected at iteration %d", iteration)
                break

            # 4d. Break if sub-workflow explicitly ended
            if iter_status in ("failure", "accept", "reject_exhausted"):
                final_status = iter_status
                break

            if iter_status == "skipped":
                final_status = "skipped"
                break

        if final_status == "success" and loop_state.get("script_exit_code") != 0:
            final_status = "failure"

        # 5. Store final result
        self.state[phase.id] = {
            "iterations": len(loop_history),
            "final_status": final_status,
            "loop_history": loop_history,
            "loop_state": loop_state,
        }

        return {
            "status": final_status,
            "iterations": len(loop_history),
            "loop_history": loop_history,
            "loop_state": loop_state,
        }

    def _execute_orchestration_phase(self, phase: PhaseDefinition, state: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        handler_path = getattr(phase, 'handler', '') or getattr(phase, 'handler', None)
        if not handler_path:
            logger.error("Orchestration phase '%s' missing handler", phase.id)
            return {"status": "failure", "error": "No handler specified for orchestration phase"}

        parts = handler_path.split(".")
        if len(parts) != 3:
            logger.error("Invalid handler path '%s' for phase '%s'", handler_path, phase.id)
            return {"status": "failure", "error": f"Handler must be module.Class.method, got: {handler_path}"}
        module_name, class_name, method_name = parts

        try:
            module = importlib.import_module(f"core.{module_name}")
        except ImportError as e:
            logger.error("Failed to import module 'core.%s': %s", module_name, e)
            return {"status": "failure", "error": f"Cannot import module: {module_name}"}

        handler_cls = getattr(module, class_name, None)
        if handler_cls is None:
            logger.error("Class '%s' not found in module 'core.%s'", class_name, module_name)
            return {"status": "failure", "error": f"Class not found: {class_name}"}

        try:
            handler_instance = handler_cls(
                artifact_dir=self.artifact_store.artifact_dir,
                store=self.experience_store,
                session_mgr=self.session_mgr
            )
        except Exception as e:
            logger.error("Failed to instantiate handler '%s': %s", class_name, e)
            return {"status": "failure", "error": f"Handler instantiation failed: {e}"}

        handler_fn = getattr(handler_instance, method_name, None)
        if handler_fn is None:
            logger.error("Method '%s' not found on handler '%s'", method_name, class_name)
            return {"status": "failure", "error": f"Method not found: {method_name}"}

        run_id = self.artifact_store.run_id
        if self.experience_store and not (module_name == "experience_evaluator" and method_name == "evaluate"):
            candidates = self.experience_store.read_candidates(run_id)
            if not candidates:
                candidates = self._backfill_candidates_from_state(state, run_id)
        else:
            candidates = []

        try:
            signature = inspect.signature(handler_fn)
            if "candidates" in signature.parameters:
                result = handler_fn(run_id=run_id, candidates=candidates)
            else:
                result = handler_fn(run_id=run_id)
            if module_name == "experience_evaluator" and method_name == "evaluate":
                return {"status": "success", "candidates": result, "total_candidates": len(result)}
            return {"status": "success", "refined_experiences": result}
        except Exception as e:
            logger.error("Orchestration handler failed for phase '%s': %s", phase.id, e)
            return {"status": "failure", "error": str(e)}

    def _find_review_phase(self, phases: list[Any]) -> dict[str, Any] | None:
        """Find a review-type phase in a list of sub-workflow phase dicts."""
        for p in phases:
            if isinstance(p, dict) and (p.get("type") or "llm") == "review":
                return p
        return None

    def _execute_improvement_block(
        self,
        block_cfg: dict[str, Any],
        state: dict[str, Any],
        context: dict[str, Any],
        loop_state: dict[str, Any],
    ) -> None:
        imp_phases = block_cfg.get("phases", [])
        if not imp_phases:
            return
        step_outputs: dict[str, Any] = {}
        for imp_phase in imp_phases:
            if not isinstance(imp_phase, dict):
                continue
            pid = imp_phase.get("id", "unnamed")
            ptype = (imp_phase.get("type") or "llm").lower()

            cond = imp_phase.get("condition")
            if cond:
                cond_met = self._evaluate_condition(
                    cond, state, context,
                    loop_vars={}, loop_state=loop_state,
                    step_outputs=step_outputs,
                )
                if not cond_met:
                    continue

            try:
                if ptype == "llm":
                    mini = self._mini_phase(imp_phase)
                    input_ctx = self._resolve_input_mapping(
                        mini, state, context,
                        loop_vars={}, loop_state=loop_state,
                        step_outputs=step_outputs,
                    )
                    self._inject_llm_baseline_context(input_ctx, mini, state)
                    self._inject_sub_workflow_context(
                        input_ctx, pid, step_outputs, {}, state, [],
                    )
                    prompt_template = self._prompt_template_for_llm_phase(
                        phase_id=str(pid),
                        default_template=mini.prompt_template,
                        state=state,
                    )
                    prompt_text = self.prompt_loader.load_prompt(
                        prompt_template, input_ctx,
                    )
                    agent_id = mini.agent or "main_engineer"
                    prompt_text, _explicit_skill_bundle = self._append_explicit_runtime_skill_markdown(
                        prompt_text, mini, agent_id
                    )
                    if self.session_registry:
                        try:
                            sid = self.session_registry.resolve(agent_id)
                        except KeyError:
                            sid = self.session_mgr.get_or_create(
                                role=agent_id, lifecycle="persistent")
                    else:
                        sid = self.session_mgr.get_or_create(
                            role=agent_id, lifecycle="persistent")
                    timeout = self._resolve_sub_workflow_llm_timeout(mini)
                    raw_response = self._send_sub_workflow_llm_command(
                        phase_id=pid,
                        agent_id=agent_id,
                        session_id=sid,
                        prompt_text=prompt_text,
                        timeout=timeout,
                    )
                    output = extract_json_response(raw_response)
                    self._raise_for_session_error_output(output, pid)
                    if not output:
                        output = {"raw_response": raw_response}
                    if isinstance(output, dict):
                        self._attach_experience_usage_report(step_outputs, pid, output)
                    step_outputs[pid] = output
                    if mini.output_as:
                        state[mini.output_as] = output
                    state[pid] = output

                elif ptype == "dispatch":
                    next_id = self._execute_dispatch_phase(
                        self._mini_phase(imp_phase), state, context,
                        loop_vars={}, loop_state=step_outputs,
                        step_outputs=step_outputs,
                    )
                    if next_id:
                        for rest in imp_phases[imp_phases.index(imp_phase) + 1:]:
                            if isinstance(rest, dict) and rest.get("id") == next_id:
                                rest_mini = self._mini_phase(rest)
                                if (rest.get("type") or "llm").lower() == "llm":
                                    mini_ctx = self._resolve_input_mapping(
                                        rest_mini, state, context,
                                        loop_vars={}, loop_state=step_outputs,
                                        step_outputs=step_outputs,
                                    )
                                    self._inject_llm_baseline_context(mini_ctx, rest_mini, state)
                                    self._inject_sub_workflow_context(
                                        mini_ctx, str(rest.get("id") or ""), step_outputs, {}, state, [],
                                    )
                                    prompt = self.prompt_loader.load_prompt(
                                        rest_mini.prompt_template, mini_ctx)
                                    agent_id = rest_mini.agent or "main_engineer"
                                    prompt, _explicit_skill_bundle = self._append_explicit_runtime_skill_markdown(
                                        prompt, rest_mini, agent_id
                                    )
                                    if self.session_registry:
                                        try:
                                            sid = self.session_registry.resolve(agent_id)
                                        except KeyError:
                                            sid = self.session_mgr.get_or_create(
                                                role=agent_id, lifecycle="persistent")
                                    else:
                                        sid = self.session_mgr.get_or_create(
                                            role=agent_id, lifecycle="persistent")
                                    timeout = self._resolve_sub_workflow_llm_timeout(rest_mini)
                                    raw = self._send_sub_workflow_llm_command(
                                        phase_id=next_id,
                                        agent_id=agent_id,
                                        session_id=sid,
                                        prompt_text=prompt,
                                        timeout=timeout,
                                    )
                                    out = extract_json_response(raw)
                                    self._raise_for_session_error_output(out, next_id)
                                    if not out:
                                        out = {"raw_response": raw}
                                    if isinstance(out, dict):
                                        self._attach_experience_usage_report(step_outputs, next_id, out)
                                    step_outputs[next_id] = out
                                    if rest_mini.output_as:
                                        state[rest_mini.output_as] = out
                                    state[next_id] = out
                                    break

                elif ptype == "shell":
                    mini = self._mini_phase(imp_phase)
                    cmd = self.resolver.resolve(
                        getattr(mini, "command", "") or "",
                        state=state, globals=self.workflow.globals,
                        context=context, loop_state=loop_state,
                    )
                    subprocess.run(str(cmd), shell=True, cwd=self.project_dir, timeout=self._mini_phase(imp_phase).timeout)
            except Exception as exc:
                logger.warning("Improvement phase '%s' failed: %s", pid, exc)
        if step_outputs:
            loop_state.update(step_outputs)

    # ── Sub-workflow runner ─────────────────────────────────────────────

    def _run_sub_workflow(
        self,
        sub_wf_def: SubWorkflowDefinition,
        loop_vars: dict[str, Any],
        state: dict[str, Any],
        context: dict[str, Any],
        sub_wf_phases: list[Any],
        blocks: dict[str, Any] | None = None,
        step_outputs: dict[str, Any] | None = None,
        loop_history: list[Any] | None = None,
        loop_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute sub-workflow phases in order, collecting step_outputs."""
        if step_outputs is None:
            step_outputs = {}

        dispatch_route: str | None = None
        dispatch_targets = {"repair_dispatch": {"fix_dependency", "fix_code", "fix_operator"},
                            "improvement_dispatch": {"imp_fix_dependency", "imp_fix_code", "imp_fix_operator"}}
        dispatch_active: str | None = None
        phase_status = "success"

        for sub_phase in sub_wf_phases:
            if not isinstance(sub_phase, dict):
                continue
            phase_id = sub_phase.get("id", "unnamed")

            # Skip non-targeted phases when dispatch is active
            if dispatch_active is not None:
                if dispatch_active == "..done":
                    dispatch_active = None
                elif phase_id != dispatch_active:
                    continue
            elif dispatch_route and phase_id in dispatch_route:
                continue

            # When a phase has a dispatch route defined (repair_dispatch, improvement_dispatch, etc.),
            # and the current sub-phase is the dispatch itself, set up dispatch_route for next iterations.
            # If the dispatch hasn't been executed yet (dispatch_route is None), skip route target phases.
            if dispatch_route and phase_id not in dispatch_route:
                dispatch_active = "..done"

            if dispatch_route and phase_id in dispatch_route:
                if phase_id != dispatch_active:
                    dispatch_active = phase_id

            # Re-read phase_id (was already read above but we preserve it)
            phase_type = (sub_phase.get("type") or "llm").lower()

            # Evaluate condition
            cond = sub_phase.get("condition")
            if cond:
                cond_met = self._evaluate_condition(
                    cond, state, context,
                    loop_vars=loop_vars, loop_state=loop_state or {},
                    step_outputs=step_outputs,
                )
                if not cond_met:
                    logger.info("Sub-phase '%s' condition FALSE → skipped", phase_id)
                    continue

            # Execute based on type
            phase_status = "success"
            phase_output: Any = {}

            try:
                if phase_type == "shell":
                    # Build a minimal PhaseDefinition from dict
                    mini = self._mini_phase(sub_phase)
                    phase_status, phase_output = self._execute_shell_phase(
                        mini, state, context,
                        loop_vars=loop_vars, loop_state=step_outputs,
                    )
                elif phase_type == "llm":
                    mini = self._mini_phase(sub_phase)
                    input_ctx = self._resolve_input_mapping(
                        mini, state, context,
                        loop_vars=loop_vars, loop_state=step_outputs,
                        step_outputs=step_outputs,
                    )
                    self._inject_llm_baseline_context(input_ctx, mini, state)
                    self._inject_sub_workflow_context(input_ctx, phase_id, step_outputs, loop_vars, state, loop_history)

                    prompt_template = self._prompt_template_for_llm_phase(
                        phase_id=str(phase_id),
                        default_template=mini.prompt_template,
                        state=state,
                    )
                    prompt_text = self.prompt_loader.load_prompt(
                        prompt_template, input_ctx
                    )
                    if not self._is_slim_repair_prompt_phase(phase_id):
                        prompt_text = self._append_inherited_experience_markdown(
                            prompt_text, phase_id, step_outputs
                        )

                    timeout = self._resolve_sub_workflow_llm_timeout(mini)

                    # Resolve agent
                    agent_id = mini.agent or "main_engineer"
                    explicit_skill_bundle = None
                    prompt_text, explicit_skill_bundle = self._append_explicit_runtime_skill_markdown(
                        prompt_text, mini, agent_id
                    )
                    if not self._is_slim_repair_prompt_phase(phase_id):
                        prompt_text = self._append_dynamic_experience_markdown(
                            prompt_text, mini, state, context, explicit_skill_bundle,
                            step_outputs=step_outputs, loop_history=loop_history,
                            log_phase_id=phase_id,
                        )
                    if self.session_registry:
                        try:
                            sid = self.session_registry.resolve(agent_id)
                        except KeyError:
                            sid = self.session_mgr.get_or_create(role=agent_id, lifecycle="persistent")
                    else:
                        sid = self.session_mgr.get_or_create(role=agent_id, lifecycle="persistent")

                    raw_response = self._send_sub_workflow_llm_command(
                        phase_id=phase_id,
                        agent_id=agent_id,
                        session_id=sid,
                        prompt_text=prompt_text,
                        timeout=timeout,
                    )
                    phase_output = extract_json_response(raw_response)
                    self._raise_for_session_error_output(phase_output, phase_id)

                    sub_output_format = expected_output_format(mini.output_schema, prompt_text)

                    sub_parse_attempt = 0
                    max_sub_parse_retries = 2
                    while not phase_output and sub_parse_attempt < max_sub_parse_retries:
                        sub_parse_attempt += 1
                        parse_correction = self._build_validation_correction_prompt(
                            "Your response did not contain a valid JSON object.",
                            output_format_example=sub_output_format,
                            is_parse_failure=True,
                            phase_name=phase_id,
                        )
                        raw_response = self._send_sub_workflow_llm_command(
                            phase_id=phase_id,
                            agent_id=agent_id,
                            session_id=sid,
                            prompt_text=parse_correction,
                            timeout=timeout,
                        )
                        phase_output = extract_json_response(raw_response)
                        self._raise_for_session_error_output(phase_output, phase_id)
                    if not phase_output:
                        phase_output = {"raw_response": raw_response}
                    phase_output = self._normalize_llm_output(mini, phase_output, input_ctx, state)
                    if isinstance(phase_output, dict):
                        recovered_output = None
                        if self._operator_repair_communication_failure(phase_id, phase_output):
                            recovered_output = self._recover_operator_repair_from_current_final_gate(
                                phase_id=phase_id,
                                state=state,
                                context=context,
                                loop_vars=loop_vars,
                                command_started_at=None,
                                require_current_run=False,
                            )
                        if recovered_output is None:
                            recovered_output = self._recover_operator_repair_from_claimed_full_pass(
                                phase_id=phase_id,
                                phase_output=cast(dict[str, Any], phase_output),
                                state=state,
                                context=context,
                                loop_vars=loop_vars,
                            )
                        if recovered_output is not None:
                            phase_output = recovered_output

                    # Validate
                    validation_failed = False
                    if mini.validator or mini.validate_only:
                        validation_passed = False
                        validation_errors: list[str] = []
                        max_retries = 3
                        for attempt in range(1, max_retries + 1):
                            vr = self.validator_engine.validate(mini.validator or phase_id, phase_output)
                            if getattr(vr, "passed", True):
                                validation_passed = True
                                break
                            validation_errors = [str(error) for error in getattr(vr, "errors", ["unknown"])]
                            if attempt >= max_retries:
                                break
                            error_msg = "; ".join(validation_errors)
                            correction = self._build_validation_correction_prompt(
                                error_msg,
                                output_format_example=sub_output_format,
                                phase_name=phase_id,
                            )
                            raw_response = self._send_sub_workflow_llm_command(
                                phase_id=phase_id,
                                agent_id=agent_id,
                                session_id=sid,
                                prompt_text=correction,
                                timeout=timeout,
                            )
                            phase_output = extract_json_response(raw_response)
                            self._raise_for_session_error_output(phase_output, phase_id)
                            if not phase_output:
                                parse_correction = self._build_validation_correction_prompt(
                                    "Your response did not contain a valid JSON object.",
                                    output_format_example=sub_output_format,
                                    is_parse_failure=True,
                                    phase_name=phase_id,
                                )
                                raw_response = self._send_sub_workflow_llm_command(
                                    phase_id=phase_id,
                                    agent_id=agent_id,
                                    session_id=sid,
                                    prompt_text=parse_correction,
                                    timeout=timeout,
                                )
                                phase_output = extract_json_response(raw_response)
                                self._raise_for_session_error_output(phase_output, phase_id)
                                if not phase_output:
                                    phase_output = {"raw_response": raw_response}
                            phase_output = self._normalize_llm_output(mini, phase_output, input_ctx, state)
                        if not validation_passed:
                            validation_failed = True
                            phase_status = "failure"
                            if isinstance(phase_output, dict):
                                phase_output = {**phase_output, "validation_errors": validation_errors}
                            else:
                                phase_output = {"raw_response": phase_output, "validation_errors": validation_errors}
                            try:
                                self.artifact_store.save_phase_output(phase_id, phase_output)
                            except Exception as exc:
                                logger.warning("Artifact save failed for invalid %s: %s", phase_id, exc)

                    if not validation_failed:
                        # Save artifacts
                        try:
                            self.artifact_store.save_phase_output(phase_id, phase_output)
                            self.artifact_store.mark_validated(phase_id, phase_output)
                        except Exception as exc:
                            logger.warning("Artifact save failed for %s: %s", phase_id, exc)

                        phase_status = "success"

                elif phase_type == "dispatch":
                    next_id = self._execute_dispatch_phase(
                        self._mini_phase(sub_phase), state, context,
                        loop_vars=loop_vars, loop_state=step_outputs,
                        step_outputs=step_outputs,
                    )
                    if next_id:
                        dispatch_route = next(iter(dispatch_targets.get(phase_id) or []), None)
                        dispatch_active = next_id
                    else:
                        dispatch_route = next(iter(dispatch_targets.get(phase_id) or []), None)
                    phase_output = {"dispatched_to": next_id}

                elif phase_type == "builtin":
                    phase_status, phase_output = self._execute_builtin_phase(
                        self._mini_phase(sub_phase), state, context,
                        loop_vars=loop_vars, loop_state=step_outputs,
                    )
                elif phase_type == "review":
                    phase_output = self._execute_review_phase(
                        self._mini_phase(sub_phase), state, context,
                        loop_vars=loop_vars, loop_state=step_outputs,
                        loop_history=loop_history or [], sub_workflow_def=sub_wf_def,
                        verdicts_cfg=sub_phase.get("verdicts", {}),
                    )
                    phase_status = phase_output.get("status", "success")
                    if phase_status == "reject":
                        blocks = blocks or {}
                        imp_block = blocks.get("improvement_block")
                        if imp_block:
                            self._execute_improvement_block(
                                imp_block, state, context, step_outputs,
                            )

                else:
                    logger.warning("Unknown sub-phase type '%s'", phase_type)

            except SessionCommandError as exc:
                logger.warning("Sub-phase '%s' session command failed: %s", phase_id, exc)
                phase_status = "failure"
                phase_output = dict(exc.payload)
            except Exception as exc:
                logger.exception("Sub-phase '%s' raised: %s", phase_id, exc)
                phase_status = "failure"
                phase_output = {"error": str(exc)}

            # Store in step_outputs
            if isinstance(phase_output, dict):
                if phase_type == "llm":
                    self._attach_experience_usage_report(
                        step_outputs, phase_id, phase_output
                    )
                step_outputs[phase_id] = phase_output
                # Also update state for cross-phase references
                out_as = sub_phase.get("output_as") or phase_id
                state[out_as] = phase_output
                if out_as != phase_id:
                    step_outputs[out_as] = phase_output
                if phase_id == "analyze_error":
                    action_result = self._maybe_apply_entry_script_action(
                        phase_output, loop_vars, state, step_outputs, loop_state or {}
                    )
                    if action_result is not None:
                        step_outputs["entry_script_action_result"] = action_result
                        if action_result.get("applied"):
                            step_outputs["entry_script_revision_applied"] = True
                            phase_status = "entry_script_revised"
                            break

            # Early exit on failure with break
            if phase_status == "failure":
                sub_on_failure = sub_phase.get("on_failure", "continue")
                validation_failed = isinstance(phase_output, dict) and "validation_errors" in phase_output
                if sub_on_failure == "break" or validation_failed:
                    break

        return {
            "status": phase_status,
            "step_outputs": step_outputs,
        }

    def _max_entry_script_revisions(self) -> int:
        raw = (self.workflow.globals or {}).get("max_entry_script_revisions")
        if raw is None:
            raw = self.framework_config.get("max_entry_script_revisions")
        if raw is None:
            entry_cfg = self.framework_config.get("entry_script")
            if isinstance(entry_cfg, dict):
                raw = entry_cfg.get("max_revisions")
        if raw is None:
            return 2
        try:
            return max(0, int(str(raw)))
        except (TypeError, ValueError):
            return 2

    def _maybe_apply_entry_script_action(
        self,
        error_analysis: dict[str, Any],
        loop_vars: dict[str, Any],
        state: dict[str, Any],
        step_outputs: dict[str, Any],
        loop_state: dict[str, Any],
    ) -> dict[str, Any] | None:
        action = error_analysis.get("entry_script_action")
        if not isinstance(action, dict):
            return None

        normalized = self._normalize_entry_script_action(action)
        if not normalized["needed"]:
            return {**normalized, "applied": False, "blocked_reason": "not_needed"}

        current_iteration = loop_state.get("iteration", 0)
        if not isinstance(current_iteration, int):
            current_iteration = 0
        request = {
            "iteration": current_iteration + 1,
            "action": normalized["action"],
            "reason": normalized["reason"],
            "entry_script_path": normalized["entry_script_path"],
            "run_command": normalized["run_command"],
            "applied": False,
        }
        requests = loop_state.setdefault("entry_script_revision_requests", [])
        if isinstance(requests, list):
            requests.append(request)

        contract = state.get("phase_3_entry_script")
        if not isinstance(contract, dict):
            contract = {}
            state["phase_3_entry_script"] = contract
        if contract.get("phase5_entry_script_revision_allowed") is not True:
            request["blocked_reason"] = "revision_not_allowed"
            return {**normalized, "applied": False, "blocked_reason": "revision_not_allowed"}

        if normalized["action"] not in {"regenerate", "modify"}:
            request["blocked_reason"] = "invalid_action"
            return {**normalized, "applied": False, "blocked_reason": "invalid_action"}
        if not normalized["run_command"]:
            request["blocked_reason"] = "missing_run_command"
            return {**normalized, "applied": False, "blocked_reason": "missing_run_command"}

        revision_count_raw = loop_state.get("entry_script_revision_count", 0) or 0
        max_revisions_raw = loop_state.get("max_entry_script_revisions", self._max_entry_script_revisions()) or 0
        revision_count = int(str(revision_count_raw))
        max_revisions = int(str(max_revisions_raw))
        if revision_count >= max_revisions:
            request["blocked_reason"] = "max_revisions_exceeded"
            return {**normalized, "applied": False, "blocked_reason": "max_revisions_exceeded"}

        safety_error = self._entry_script_revision_safety_error(
            normalized["run_command"], contract, normalized["entry_script_path"]
        )
        if safety_error:
            request["blocked_reason"] = safety_error
            return {**normalized, "applied": False, "blocked_reason": safety_error}

        if normalized["entry_script_path"]:
            contract["entry_script_path"] = normalized["entry_script_path"]
        contract["run_command"] = normalized["run_command"]
        loop_vars["entry_script"] = normalized["run_command"]

        loop_state["entry_script_revision_count"] = revision_count + 1
        loop_state["entry_script"] = normalized["run_command"]
        request["applied"] = True
        request["revision_number"] = revision_count + 1
        result = {
            **normalized,
            "applied": True,
            "revision_number": revision_count + 1,
            "max_revisions": max_revisions,
        }
        step_outputs["entry_script"] = normalized["run_command"]
        return result

    @staticmethod
    def _has_shell_metacharacters(run_command: str) -> bool:
        return any(control in run_command for control in ("&&", "||", ";", "|", "`", "$(", ">", "<", "\n", "\r", "&"))

    def _entry_script_revision_safety_error(
        self,
        run_command: str,
        contract: dict[str, Any],
        entry_script_path: str,
    ) -> str | None:
        if self._has_shell_metacharacters(run_command):
            return "unsafe_run_command"
        try:
            tokens = shlex.split(run_command)
        except ValueError:
            return "unsafe_run_command"
        if not tokens:
            return "missing_run_command"
        shell_builtins = {"source", ".", "eval", "export", "alias", "unset"}
        shell_controls = {"&&", "||", ";", "|", "`", "$()", ">", "<"}
        if tokens[0] in shell_builtins or any(token in shell_controls for token in tokens):
            return "unsafe_run_command"

        real_executable = tokens[0].rsplit("/", 1)[-1]
        _, stripped_cmd = _extract_env_prefix(run_command)
        if stripped_cmd:
            try:
                stripped_tokens = shlex.split(stripped_cmd)
                if stripped_tokens:
                    real_executable = stripped_tokens[0].rsplit("/", 1)[-1]
            except ValueError:
                pass

        if real_executable in shell_builtins:
            return "unsafe_run_command"
        if real_executable in {"bash", "sh", "/bin/bash", "/bin/sh"} or real_executable.endswith(".sh"):
            return "unsafe_run_command"
        if real_executable in {"docker", "podman"}:
            return "unsafe_run_command"
        updated_contract = dict(contract)
        updated_contract["run_command"] = run_command
        if entry_script_path:
            updated_contract["entry_script_path"] = entry_script_path
        elif not updated_contract.get("entry_script_path"):
            extracted_path = self._extract_entry_script_path_from_command(run_command)
            if extracted_path:
                updated_contract["entry_script_path"] = extracted_path
        if self._has_custom_op_contract(updated_contract):
            updated_contract["reports_dir"] = str(Path(self.project_dir).resolve() / "migration_reports")
        validation = validate_entry_script(updated_contract)
        if not validation["passed"]:
            return "entry_script_contract_validation_failed"
        return None

    @staticmethod
    def _extract_entry_script_path_from_command(run_command: str) -> str:
        try:
            tokens = shlex.split(run_command)
        except ValueError:
            return ""
        _, stripped = _extract_env_prefix(run_command)
        if stripped:
            try:
                tokens = shlex.split(stripped)
            except ValueError:
                pass
        for token in tokens:
            if token.endswith(".py") or Path(token).suffix == ".py":
                return token
        return ""

    @staticmethod
    def _normalize_entry_script_action(action: dict[str, Any]) -> dict[str, Any]:
        needed = WorkflowExecutor._coerce_entry_script_action_needed(action.get("needed"))
        raw_action = str(action.get("action", "none") or "none").strip().lower()
        return {
            "needed": needed,
            "action": raw_action,
            "reason": str(action.get("reason", "") or "").strip(),
            "entry_script_path": str(action.get("entry_script_path", "") or "").strip(),
            "run_command": str(action.get("run_command", "") or "").strip(),
        }

    @staticmethod
    def _coerce_entry_script_action_needed(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"true", "1", "yes"}
        return False

    def _attach_experience_usage_report(
        self,
        step_outputs: dict[str, Any],
        phase_id: str,
        phase_output: dict[str, Any],
    ) -> None:
        usage_report = self._normalize_experience_usage_report(phase_output)
        phase_output["experience_usage"] = usage_report
        if self._is_experience_repair_phase(phase_id):
            self._record_phase_experience_usage(
                step_outputs,
                phase_id,
                usage_report,
                phase_output,
            )

    def _normalize_experience_usage_report(self, output: dict[str, Any]) -> dict[str, Any]:
        used_ids = self._normalize_string_list(output.get("used_experience_ids"))
        actions_taken = output.get("experience_actions_taken")
        if isinstance(actions_taken, dict):
            normalized_actions = {
                str(key): self._normalize_string_list(value)
                for key, value in actions_taken.items()
            }
        elif isinstance(actions_taken, list):
            normalized_actions = [str(item) for item in actions_taken if item]
        elif isinstance(actions_taken, str) and actions_taken.strip():
            normalized_actions = [actions_taken.strip()]
        else:
            normalized_actions = []

        ignored_ids = self._normalize_string_list(output.get("ignored_experience_ids"))
        ignored_reasons = output.get("ignored_reasons")
        if isinstance(ignored_reasons, dict):
            normalized_reasons = {
                str(key): str(value) for key, value in ignored_reasons.items() if value is not None
            }
        elif isinstance(ignored_reasons, list):
            normalized_reasons = [str(item) for item in ignored_reasons if item]
        elif isinstance(ignored_reasons, str) and ignored_reasons.strip():
            normalized_reasons = [ignored_reasons.strip()]
        else:
            normalized_reasons = {}

        return {
            "used_experience_ids": used_ids,
            "experience_actions_taken": normalized_actions,
            "ignored_experience_ids": ignored_ids,
            "ignored_reasons": normalized_reasons,
        }

    def _record_phase_experience_usage(
        self,
        step_outputs: dict[str, Any],
        phase_id: str,
        usage_report: dict[str, Any],
        phase_output: dict[str, Any],
    ) -> None:
        usage_by_phase = step_outputs.setdefault("experience_usage_by_phase", {})
        if isinstance(usage_by_phase, dict):
            usage_by_phase[phase_id] = usage_report
        used_ids = usage_report["used_experience_ids"]
        ignored_ids = usage_report["ignored_experience_ids"]
        self._record_experience_usage(used_ids=used_ids, ignored_ids=ignored_ids)
        self._queue_experience_verification(step_outputs, phase_id, used_ids)
        event_payload = {
            "phase_id": phase_id,
            "used_ids": used_ids,
            "ignored_ids": ignored_ids,
            "actions_taken": self._compact_usage_detail(
                usage_report["experience_actions_taken"]
            ),
            "ignored_reasons": self._compact_usage_detail(
                usage_report["ignored_reasons"]
            ),
            "output_status": phase_output.get("status", "success"),
        }
        if used_ids:
            self._emit_experience_event("experience_used", **event_payload)
        if ignored_ids:
            self._emit_experience_event("experience_ignored", **event_payload)

    def _queue_experience_verification(
        self,
        step_outputs: dict[str, Any],
        phase_id: str,
        used_ids: list[str],
    ) -> None:
        if not used_ids:
            return
        pending = step_outputs.setdefault("pending_experience_verifications", [])
        if isinstance(pending, list):
            pending.append({"phase_id": phase_id, "experience_ids": used_ids})

    def _record_pending_experience_verification(
        self,
        loop_state: dict[str, Any],
        step_outputs: dict[str, Any],
        iteration: int,
    ) -> dict[str, Any] | None:
        pending = loop_state.get("pending_experience_verifications")
        if not isinstance(pending, list) or not pending:
            return None
        exit_code = step_outputs.get("script_exit_code", loop_state.get("script_exit_code"))
        if not isinstance(exit_code, int):
            return None
        used_ids: list[str] = []
        source_phase_ids: list[str] = []
        remaining: list[dict[str, Any]] = []
        for item in pending:
            if isinstance(item, dict):
                created_iteration = item.get("created_iteration")
                if isinstance(created_iteration, int) and created_iteration >= iteration:
                    remaining.append(item)
                    continue
                used_ids.extend(self._normalize_string_list(item.get("experience_ids")))
                phase_id = item.get("phase_id")
                if phase_id:
                    source_phase_ids.append(str(phase_id))
        used_ids = self._dedupe_strings(used_ids)
        if not used_ids:
            return None
        signal = {
            "iteration": iteration,
            "experience_ids": used_ids,
            "source_phase_ids": self._dedupe_strings(source_phase_ids),
            "validation_exit_code": exit_code,
            "passed": exit_code == 0,
        }
        self._record_experience_usage(
            verification={"experience_ids": used_ids, "passed": exit_code == 0}
        )
        self._emit_experience_event("experience_verification", **signal)
        history = loop_state.setdefault("experience_verifications", [])
        if isinstance(history, list):
            history.append(signal)
        loop_state["pending_experience_verifications"] = remaining
        return signal

    def _carry_pending_experience_verifications(
        self,
        loop_state: dict[str, Any],
        step_outputs: dict[str, Any],
    ) -> None:
        pending = loop_state.get("pending_experience_verifications")
        if not isinstance(pending, list) or not pending:
            return
        carried: list[dict[str, Any]] = []
        for item in pending:
            if isinstance(item, dict):
                carried.append(dict(item))
        if carried:
            step_outputs["pending_experience_verifications"] = carried

    def _stamp_pending_experience_verifications(
        self,
        loop_state: dict[str, Any],
        iteration: int,
    ) -> None:
        pending = loop_state.get("pending_experience_verifications")
        if not isinstance(pending, list):
            return
        for item in pending:
            if isinstance(item, dict) and "created_iteration" not in item:
                item["created_iteration"] = iteration

    def _summarize_iteration_experience_usage(self, step_outputs: dict[str, Any]) -> dict[str, Any]:
        usage_by_phase = step_outputs.get("experience_usage_by_phase")
        if not isinstance(usage_by_phase, dict):
            usage_by_phase = {}
        selected_ids = self._normalize_string_list(step_outputs.get("selected_experience_ids"))
        used_ids: list[str] = []
        ignored_ids: list[str] = []
        for usage in usage_by_phase.values():
            if isinstance(usage, dict):
                used_ids.extend(self._normalize_string_list(usage.get("used_experience_ids")))
                ignored_ids.extend(self._normalize_string_list(usage.get("ignored_experience_ids")))
        return {
            "selected_experience_ids": selected_ids,
            "used_experience_ids": self._dedupe_strings(used_ids),
            "ignored_experience_ids": self._dedupe_strings(ignored_ids),
            "by_phase": usage_by_phase,
        }

    def _record_experience_usage(
        self,
        *,
        selected_ids: list[str] | None = None,
        used_ids: list[str] | None = None,
        ignored_ids: list[str] | None = None,
        verification: dict[str, Any] | None = None,
    ) -> None:
        recorder = getattr(self.experience_store, "record_experience_usage", None)
        if not callable(recorder):
            return
        try:
            recorder(
                selected_ids=selected_ids,
                used_ids=used_ids,
                ignored_ids=ignored_ids,
                verification=verification,
            )
        except Exception as exc:
            logger.warning("Experience usage counter update failed: %s", exc)

    def _emit_experience_event(self, event_type: str, **payload: Any) -> None:
        for target, method_name in (
            (self.telemetry_observer, "record_event"),
            (self.telemetry_bridge, "on_event"),
        ):
            emitter = getattr(target, method_name, None)
            if not callable(emitter):
                continue
            try:
                emitter(event_type, **payload)
            except Exception as exc:
                logger.warning("Experience telemetry event failed: %s", exc)

    def _compact_selected_experiences(self, experiences: Any) -> list[dict[str, Any]]:
        if not isinstance(experiences, list):
            return []
        compact: list[dict[str, Any]] = []
        for experience in experiences[:5]:
            if not isinstance(experience, dict):
                continue
            item: dict[str, Any] = {}
            for field_name in (
                "id",
                "type",
                "title",
                "target_roles",
                "target_phases",
                "relevance_score",
            ):
                value = experience.get(field_name)
                if value not in (None, "", []):
                    item[field_name] = value
            paths = self._compact_experience_paths(experience)
            if paths:
                item["readable_paths"] = paths
            if item:
                compact.append(item)
        return compact

    def _compact_experience_paths(self, experience: dict[str, Any]) -> list[str]:
        paths: list[str] = []
        for field_name in ("file_path", "path"):
            value = experience.get(field_name)
            if value:
                paths.append(str(value))
        asset_paths = experience.get("asset_paths", [])
        if isinstance(asset_paths, str):
            paths.append(asset_paths)
        elif isinstance(asset_paths, list):
            paths.extend(str(path) for path in asset_paths if path)
        return self._dedupe_strings(paths)[:3]

    def _compact_action_cards(self, action_cards: Any) -> list[str]:
        if not isinstance(action_cards, list):
            return []
        return [
            self._truncate_text(str(card), 600)
            for card in action_cards[:5]
            if str(card).strip()
        ]

    def _compact_usage_detail(self, value: Any) -> Any:
        if isinstance(value, dict):
            compact: dict[str, Any] = {}
            for index, (detail_key, detail_value) in enumerate(value.items()):
                if index >= 20:
                    break
                compact[str(detail_key)] = self._compact_usage_detail(detail_value)
            return compact
        if isinstance(value, list):
            return [self._truncate_text(str(item), 300) for item in value[:20]]
        if isinstance(value, str):
            return self._truncate_text(value, 300)
        return value

    @staticmethod
    def _truncate_text(text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        return text[:limit] + "..."

    def _experience_ids(self, experiences: Any) -> list[str]:
        if not isinstance(experiences, list):
            return []
        ids: list[str] = []
        for experience in experiences:
            if isinstance(experience, dict) and experience.get("id"):
                ids.append(str(experience["id"]))
        return self._dedupe_strings(ids)

    def _normalize_string_list(self, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            values = [value]
        elif isinstance(value, (list, tuple, set)):
            values = list(value)
        else:
            values = [value]
        return self._dedupe_strings(str(item).strip() for item in values if str(item).strip())

    @staticmethod
    def _dedupe_strings(values: Any) -> list[str]:
        deduped: list[str] = []
        for value in values:
            if value not in deduped:
                deduped.append(value)
        return deduped

    @staticmethod
    def _is_experience_repair_phase(phase_id: str) -> bool:
        return phase_id in {
            "fix_dependency",
            "fix_code",
            "fix_operator",
            "imp_fix_dependency",
            "imp_fix_code",
            "imp_fix_operator",
        }

    @staticmethod
    def _is_slim_repair_prompt_phase(phase_id: str) -> bool:
        return phase_id in {
            "fix_dependency",
            "fix_operator",
            "imp_fix_dependency",
            "imp_fix_operator",
        }

    def _write_repair_runtime_artifacts(
        self,
        *,
        project_dir: str,
        entry_script: str,
        error_text: str,
        category: str,
        root_cause: str,
        suggested_fix: str,
        repair_role: str,
        experience_action_cards: Any,
    ) -> tuple[str, str]:
        return write_repair_runtime_artifacts(
            artifact_dir=str(self.artifact_store.artifact_dir),
            project_dir=project_dir,
            entry_script=entry_script,
            error_text=error_text,
            category=category,
            root_cause=root_cause,
            suggested_fix=suggested_fix,
            repair_role=repair_role,
            experience_action_cards=experience_action_cards,
        )

    def _write_operator_repair_context_artifact(
        self,
        *,
        project_dir: str,
        entry_script: str,
        phase3_contract: dict[str, object] | None,
    ) -> str:
        return write_operator_repair_context_artifact(
            artifact_dir=str(self.artifact_store.artifact_dir),
            project_dir=project_dir,
            entry_script=entry_script,
            phase3_contract=phase3_contract,
        )

    def _mini_phase(self, phase_dict: dict[str, Any]) -> PhaseDefinition:
        """Create a PhaseDefinition from a plain dict (for sub-workflow phases)."""
        hooks = None
        raw_hooks = phase_dict.get("hooks")
        if raw_hooks:
            if isinstance(raw_hooks, dict):
                hooks = PhaseHooks(
                    pre_execute=raw_hooks.get("pre_execute", []),
                    post_execute=raw_hooks.get("post_execute", []),
                    on_error=raw_hooks.get("on_error", []),
                )

        transition = None
        raw_transition = phase_dict.get("transition")
        if raw_transition and isinstance(raw_transition, dict):
            transition = TransitionDefinition(
                on_success=raw_transition.get("on_success"),
                on_failure=raw_transition.get("on_failure"),
                on_skip=raw_transition.get("on_skip"),
                on_stagnation=raw_transition.get("on_stagnation"),
                on_reject_exhausted=raw_transition.get("on_reject_exhausted"),
            )

        transitions = phase_dict.get("transitions", {})
        if isinstance(transitions, dict) is False:
            transitions = {}

        runtime_skills = self._coerce_runtime_skills_config(
            phase_dict.get("runtime_skills"),
            f"sub_workflow.phases[{phase_dict.get('id', 'unnamed')}].runtime_skills",
        )

        mini = PhaseDefinition(
            id=phase_dict.get("id", "unnamed"),
            name=phase_dict.get("name", ""),
            prompt_template=phase_dict.get("prompt_template", ""),
            output_schema=phase_dict.get("output_schema", {}),
            validator=phase_dict.get("validator"),
            transitions=transitions,
            type=phase_dict.get("type", "llm"),
            agent=phase_dict.get("agent"),
            timeout=phase_dict.get("timeout"),
            condition=phase_dict.get("condition"),
            input_mapping=phase_dict.get("input_mapping", {}),
            output_as=phase_dict.get("output_as"),
            max_iterations=phase_dict.get("max_iterations"),
            sub_workflow=phase_dict.get("sub_workflow"),
            validate_only=phase_dict.get("validate_only", False),
            hooks=hooks,
            transition=transition,
            on_failure=phase_dict.get("on_failure", "continue"),
            handler=phase_dict.get("handler"),
            retrieve_experience=bool(phase_dict.get("retrieve_experience", False)),
            experience_query=phase_dict.get("experience_query"),
            runtime_skills=runtime_skills,
        )
        params = dict(phase_dict.get("params", {}) or {})
        if phase_dict.get("operation") is not None:
            params["operation"] = phase_dict["operation"]
        if phase_dict.get("route_field"):
            params["route_field"] = phase_dict["route_field"]
        if phase_dict.get("routes"):
            params["routes"] = phase_dict["routes"]
        setattr(mini, "params", params)
        setattr(mini, "command", phase_dict.get("command", ""))
        setattr(mini, "cwd", phase_dict.get("cwd"))
        return mini

    def _find_sub_phase_by_id(self, phases: list[Any], phase_id: str) -> dict[str, Any] | None:
        """Find a sub-phase dict by id."""
        for p in phases:
            if isinstance(p, dict) and p.get("id") == phase_id:
                return p
        return None

    # ── Stop conditions ─────────────────────────────────────────────────

    def _check_stop_conditions(
        self,
        stop_conditions: list[dict[str, Any]],
        loop_state: dict[str, Any],
        globals: dict[str, Any],
    ) -> str | None:
        """Evaluate stop conditions in order. Return matched status or None."""
        for cond_def in stop_conditions:
            if not isinstance(cond_def, dict):
                continue
            cond_expr = cond_def.get("condition", "")
            target_status = cond_def.get("status", "stop")

            # Resolve $.field references
            expr = cond_expr
            if "$." in expr:
                def repl(m: re.Match[str]) -> str:
                    field_name = m.group(1)
                    for src in (loop_state, globals):
                        if field_name in src:
                            val = src[field_name]
                            return json.dumps(val) if not isinstance(val, str) else val
                    return repr(field_name)
                expr = re.sub(r'\$\.(\w+)', repl, expr)

            # Evaluate
            env: dict[str, Any] = {}
            env.update(globals)
            env.update(loop_state)
            # If expression is a simple literal, just eval
            if expr.lower() == "true":
                return target_status
            if expr.lower() == "false":
                continue

            try:
                if _safe_eval_bool(expr, env):
                    return target_status
            except Exception as exc:
                logger.warning("Stop condition eval failed '%s': %s", cond_expr, exc)

        return None

    # ── Stagnation detection ────────────────────────────────────────────

    def _effective_stagnation_threshold(
        self,
        *,
        base_threshold: int,
        phase_id: str,
        state: dict[str, Any],
        step_outputs: dict[str, Any],
        loop_state: dict[str, Any] | None = None,
    ) -> int:
        if not self._active_custom_op_operator_repair(phase_id, state, step_outputs, loop_state):
            return base_threshold
        raw_value = self.framework_config.get("custom_op_operator_stagnation_threshold")
        try:
            configured = int(raw_value) if raw_value is not None else CUSTOM_OP_OPERATOR_STAGNATION_THRESHOLD_DEFAULT
        except (TypeError, ValueError):
            logger.warning(
                "Invalid custom_op_operator_stagnation_threshold=%r; using default %s",
                raw_value,
                CUSTOM_OP_OPERATOR_STAGNATION_THRESHOLD_DEFAULT,
            )
            configured = CUSTOM_OP_OPERATOR_STAGNATION_THRESHOLD_DEFAULT
        return max(base_threshold, configured)

    def _active_custom_op_operator_repair(
        self,
        phase_id: str,
        state: dict[str, Any],
        step_outputs: dict[str, Any],
        loop_state: dict[str, Any] | None = None,
    ) -> bool:
        if phase_id != "phase_5_validation":
            return False
        phase3_contract = state.get("phase_3_entry_script")
        if not isinstance(phase3_contract, dict) or not has_custom_op_contract(phase3_contract):
            return False
        error_analysis = step_outputs.get("error_analysis")
        if isinstance(error_analysis, dict) and error_analysis.get("repair_role") == "operator_fixer":
            return True
        for value in step_outputs.values():
            if isinstance(value, dict) and value.get("repair_role") == "operator_fixer":
                return True
        if loop_state and loop_state.get("_custom_op_operator_repair_ever_active"):
            return True
        return False

    def _check_stagnation(
        self,
        error_signature: str,
        loop_state: dict[str, Any],
        threshold: int = 3,
    ) -> bool:
        """Detect if the same error has occurred *threshold* times in a row."""
        normalized = self._normalize_error_signature(error_signature)
        last_sig = loop_state.get("last_error_signature", "")

        if normalized == last_sig and normalized:
            count = loop_state.get("stagnation_count", 0) + 1
            loop_state["stagnation_count"] = count
        else:
            loop_state["stagnation_count"] = 1
            loop_state["last_error_signature"] = normalized

        return loop_state.get("stagnation_count", 0) >= threshold

    @staticmethod
    def _normalize_error_signature(text: str) -> str:
        """Remove trailing whitespace from each line."""
        if not text:
            return ""
        return "\n".join(line.rstrip() for line in text.splitlines())

    # ── Next phase resolution ───────────────────────────────────────────

    def _get_next_phase_id(
        self,
        current_phase: PhaseDefinition,
        status: str,
        state: dict[str, Any],
        context: dict[str, Any],
    ) -> str | None:
        """Determine the next phase to execute.

        Priority:
          1. phase.transition (TransitionDefinition)
          2. phase.transitions dict (raw keys + on_* YAML-style aliases)
          3. Unhandled failure terminates
          4. Unhandled non-success / non-skipped status terminates (fail-closed)
          5. Default: next phase in workflow.phases list
          6. None (terminate)
        """
        # 1. Check TransitionDefinition
        transition = current_phase.transition
        if transition is not None:
            if status == "success" and transition.on_success:
                target = transition.on_success
                if target in ("phase_7a_evaluate", "phase_7b_refine"):
                    p7_cfg = getattr(getattr(self.workflow, 'experience', None), 'phase7_enabled', True)
                    if not p7_cfg:
                        return "complete"
                return target
            if status == "failure" and transition.on_failure:
                return transition.on_failure
            if status == "skipped" and transition.on_skip:
                return transition.on_skip
            if status == "stagnation" and transition.on_stagnation:
                return transition.on_stagnation
            if status == "reject_exhausted" and transition.on_reject_exhausted:
                return transition.on_reject_exhausted

        # 2. Check transitions dict
        if current_phase.transitions:
            status_keys = {
                "success": ("success", "on_success"),
                "failure": ("failure", "on_failure"),
                "skipped": ("skipped", "on_skip"),
                "stagnation": ("stagnation", "on_stagnation"),
                "reject_exhausted": ("reject_exhausted", "on_reject_exhausted"),
            }
            for key in status_keys.get(status, (status,)):
                target = current_phase.transitions.get(key)
                if target:
                    if target in ("phase_7a_evaluate", "phase_7b_refine"):
                        p7_cfg = getattr(getattr(self.workflow, 'experience', None), 'phase7_enabled', True)
                        if not p7_cfg:
                            return "complete"
                    return target

        # 3. Fail closed when a phase fails without an explicit recovery route.
        if status == "failure":
            return None

        # 4. Fail closed for all non-standard terminal statuses (stagnation,
        #    reject_exhausted, accept, …) that lack an explicit transition.
        #    Only `success` and `skipped` are allowed to fall through to the
        #    default next-phase lookup.
        if status not in ("success", "skipped"):
            return None

        # 5. Default: next phase in list
        idx = self.phase_index.get(current_phase.id, -1)
        phases = self.workflow.phases or []
        if idx >= 0 and idx + 1 < len(phases):
            next_id = phases[idx + 1].id
            if next_id in ("phase_7a_evaluate", "phase_7b_refine"):
                p7_cfg = getattr(getattr(self.workflow, 'experience', None), 'phase7_enabled', True)
                if not p7_cfg:
                    return "complete"
            return next_id

        # 6. Last phase → terminate
        return None

    def _build_experience_query_context(
        self,
        phase: PhaseDefinition,
        state: dict[str, Any],
        context: dict[str, Any],
        step_outputs: dict[str, Any] | None = None,
        loop_history: list[Any] | None = None,
    ) -> dict[str, Any]:
        query_config = getattr(phase, 'experience_query', None) or {}
        result = {
            "phase": phase.id,
            "phases": [phase.id],
            "parent_phase": self._experience_parent_phase(phase.id),
            "role": phase.agent or "main_engineer",
            "roles": self._experience_query_roles(phase),
            "error_category": "unknown",
            "error_stderr": "",
            "project_type": "unknown",
            "dependencies": "",
            "previous_repair_attempts": "None recorded",
            "root_cause": "",
            "suggested_fix": "",
        }

        phase_id = phase.id

        phase3_contract = state.get("phase_3_entry_script")
        phase35_static = state.get("phase_35_static_validate")
        native_custom_op_gate_required = (
            isinstance(phase3_contract, dict)
            and self._has_custom_op_contract(phase3_contract)
        ) or (
            isinstance(phase35_static, dict)
            and phase35_static.get("custom_op_static_required") is True
        )
        if native_custom_op_gate_required:
            result["custom_op_native_gate_required"] = "true"
            result["custom_op_evidence_policy"] = (
                self.platform_policy.custom_op_evidence.custom_op_evidence_policy
                or "require_real_custom_op_artifacts"
            )

        # Resolve from known state sources
        ph1 = state.get("phase_1_project_analysis", {})
        if isinstance(ph1, dict):
            if ph1.get("project_type"):
                result["project_type"] = str(ph1["project_type"])
            if ph1.get("dependencies"):
                deps = ph1["dependencies"]
                result["dependencies"] = ", ".join(deps) if isinstance(deps, list) else str(deps)

        if step_outputs and isinstance(step_outputs, dict):
            stderr = step_outputs.get("script_stderr", "")
            shell_out = step_outputs.get("run_entry_script", {})
            if not stderr and isinstance(shell_out, dict):
                stderr = shell_out.get("script_stderr", "") or shell_out.get("stderr", "")
            if stderr:
                result["error_stderr"] = str(stderr)[:5000]

        # Include previous iteration's error_analysis for Phase 5 context
        prev_analysis = state.get("error_analysis", {})
        if isinstance(prev_analysis, dict):
            result["error_category"] = str(prev_analysis.get("category", "unknown"))
            if prev_analysis.get("repair_role"):
                result["repair_role"] = str(prev_analysis["repair_role"])
            if prev_analysis.get("root_cause"):
                result["root_cause"] = str(prev_analysis["root_cause"])
            if prev_analysis.get("suggested_fix"):
                result["suggested_fix"] = str(prev_analysis["suggested_fix"])

        if loop_history and isinstance(loop_history, list) and loop_history:
            attempt_labels = []
            for entry in loop_history:
                if isinstance(entry, dict):
                    status = entry.get("status", "")
                    dur = entry.get("duration", "")
                    attempt_labels.append(f"Iteration {entry.get('iteration', '?')}: status={status}, duration={dur}")
            if attempt_labels:
                result["previous_repair_attempts"] = "; ".join(attempt_labels)

        # Resolve signals from config if provided
        source = query_config.get("source")
        signals = query_config.get("signals", [])
        if source and isinstance(state.get(source), dict):
            source_data = state[source]
            for sig in signals:
                if sig in source_data and sig not in ("error_category",):
                    val = source_data[sig]
                    result[sig] = val if isinstance(val, str) else str(val)

        return result


    def _experience_parent_phase(self, phase_id: str) -> str:
        if phase_id in {
            "analyze_error",
            "repair_dispatch",
            "fix_dependency",
            "fix_code",
            "fix_operator",
            "improvement_plan",
            "imp_fix_dependency",
            "imp_fix_code",
            "imp_fix_operator",
        }:
            return "phase_5_validation"
        return phase_id

    def _experience_query_roles(self, phase: PhaseDefinition) -> list[str]:
        phase_id = phase.id
        if phase_id == "analyze_error":
            return ["error_analyzer", "dependency_fixer", "code_adapter", "operator_fixer"]
        if phase_id in {"fix_dependency", "imp_fix_dependency"}:
            return ["dependency_fixer"]
        if phase_id in {"fix_code", "imp_fix_code"}:
            return ["code_adapter"]
        if phase_id in {"fix_operator", "imp_fix_operator"}:
            return ["operator_fixer"]
        return [phase.agent or "main_engineer"]

    def _backfill_candidates_from_state(self, state: dict[str, Any], run_id: str) -> list[dict[str, Any]]:
        """Bridge Phase 7a → 7b: copy LLM-produced candidates from state to ExperienceStore.

        Phase 7a outputs candidates to state['phase_7a_evaluate']['candidates'],
        but Phase 7b reads from ExperienceStore disk. This backfill writes them
        to staging if they haven't been persisted yet.
        """
        phase_7a_output = state.get("phase_7a_evaluate", {})
        if not isinstance(phase_7a_output, dict):
            return []

        candidates = phase_7a_output.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            logger.info("No candidates to backfill from state phase_7a_evaluate")
            return []

        store = self.experience_store
        project_source_root = str(phase_7a_output.get("project_source_root") or "")
        normalized_candidates: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for index, raw_candidate in enumerate(candidates, start=1):
            if not isinstance(raw_candidate, dict):
                continue
            c = dict(raw_candidate)
            cid = self._stable_candidate_id(c, index, seen_ids)
            seen_ids.add(cid)
            c["candidate_id"] = cid
            c.setdefault("source_run_id", run_id)
            if project_source_root:
                c.setdefault("project_source_root", project_source_root)
            if store is None:
                break
            try:
                store.write_candidate(run_id, cid, c)
                logger.info("Backfilled candidate %s to ExperienceStore (run_id=%s)", cid, run_id)
                normalized_candidates.append(c)
            except Exception as exc:
                logger.warning("Failed to backfill candidate %s: %s", cid, exc)

        return normalized_candidates

    @staticmethod
    def _stable_candidate_id(candidate: dict[str, Any], index: int, seen_ids: set[str]) -> str:
        raw_id = str(candidate.get("candidate_id") or "").strip()
        if raw_id:
            candidate_id = re.sub(r"[^A-Za-z0-9_.-]+", "-", raw_id).strip("-") or f"candidate-{index:03d}"
        else:
            candidate_id = f"candidate-{index:03d}"
        if candidate_id not in seen_ids:
            return candidate_id
        suffix = 2
        while f"{candidate_id}-{suffix}" in seen_ids:
            suffix += 1
        return f"{candidate_id}-{suffix}"
