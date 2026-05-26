from __future__ import annotations

import json
import logging
import os
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast, runtime_checkable

from harness.session.manager import extract_json_response

from core.artifact_store import ArtifactStore
from core.assisted_verification import (
    AssistedVerificationResult,
    AssistedVerificationRunner,
    attach_assisted_summary,
)
from core.custom_op_opp_preflight import has_custom_op_contract, has_explicit_no_custom_op_contract
from core.custom_op_variants import (
    apply_expanded_variant_contract,
    ensure_strict_expanded_variant_validation_script,
    expanded_variant_contract_from_outputs,
    normalize_project_analysis_expanded_variants,
)
from core.execution_backend import get_execution_context as _get_exec_ctx, get_execution_environment_context as _get_env_ctx
from core.paths import resolve_relative_path, workspace_root
from core.phase_boundary import inject_phase_boundary
from core.phase6_fallback import build_phase6_fallback_report, collect_phase6_prior_artifacts, resolve_phase6_timeout
from core.prompt_loader import PromptLoader
from core.routes import (
    SERVING_ROUTES,
    normalize_serving_phase1_surface,
    normalize_serving_phase3_contract,
)
from core.runtime_skill_resolver import RuntimeSkillBundle, RuntimeSkillResolver
from core.types import PhaseDefinition, RuntimeSkillsConfig, WorkflowDefinition
from core.validation_correction import (
    build_phase_correction_prompt,
    build_validation_correction_prompt,
    extract_output_format_from_prompt,
)
from core.validator_engine import ValidationResult, ValidatorEngine
from migrator.rule_based import RuleBasedMigrator
from validators.validate_entry_script import validate as validate_entry_script
from validators.validate_entry_static import validate as validate_entry_static
from validators.validate_env_detect import validate as validate_env_detect
from validators.validate_project_analysis import validate as validate_project_analysis
from validators.validate_phase_1_custom_op_completeness import validate as validate_phase_1_custom_op_completeness
from validators.validate_phase_3_custom_op_contract_coverage import validate as validate_phase_3_custom_op_contract_coverage
from validators.validate_reports import validate as validate_reports
from validators.validate_rule_migration import validate as validate_rule_migration
from validators.validate_venv import validate as validate_venv


JsonObject = dict[str, object]

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

def _rewrite_container_to_host_path(
    path_str: str,
    project_dir: str,
    container_workdir: str,
) -> str:
    """Convert a container-visible path to its host-visible equivalent.

    When a model returns a path under ``container_workdir`` (e.g.
    ``/workspace/validate_fwi.py``), return the corresponding path under
    ``project_dir`` (e.g. ``{project_dir}/validate_fwi.py``).

    Boundary-safe: ``/workspace2/x`` does NOT match container workdir ``/workspace``.
    """
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


logger = logging.getLogger(__name__)


class SessionManagerLike(Protocol):
    def get_or_create(self, role: str, lifecycle: str, agent: str = "") -> str:
        ...

    def send_command(
        self,
        session_id: str,
        command: str,
        timeout: int | None = None,
        retries: int | None = None,
    ) -> str:
        ...


@runtime_checkable
class InlineSessionLike(Protocol):
    def send_command(self, prompt: str, timeout: int | None = None) -> str:
        ...


@runtime_checkable
class SessionWithIdLike(InlineSessionLike, Protocol):
    session_id: str


@dataclass(frozen=True)
class PhaseSpec:
    alias: str
    prompt_id: str
    validator_name: str
    timeout: int | None = None

    @property
    def artifact_id(self) -> str:
        return self.prompt_id.removeprefix("phase_")


class PhaseRunner:
    PHASE_ORDER: tuple[str, ...] = ("phase_0", "phase_1", "phase_2", "phase_3", "phase_35")
    _SHARED_SESSION_PHASES: frozenset[str] = frozenset(
        {
            "phase_0_env_detect",
            "phase_1_project_analysis",
            "phase_2_venv_create",
            "phase_3_entry_script",
            "phase_35_static_validate",
        }
    )
    session_mgr: SessionManagerLike
    artifact_store: ArtifactStore
    prompt_loader: PromptLoader
    validator: ValidatorEngine
    max_retry: int
    phase_specs: dict[str, PhaseSpec]
    workflow: WorkflowDefinition | None
    framework_config: dict[str, object]
    _runtime_skill_resolver: RuntimeSkillResolver | None
    _runtime_phase_index: dict[str, PhaseDefinition]

    def __init__(
        self,
        session_mgr: SessionManagerLike,
        artifact_store: ArtifactStore,
        prompt_loader: PromptLoader,
        validator: ValidatorEngine,
        workflow: WorkflowDefinition | None = None,
        framework_config: dict[str, object] | None = None,
    ) -> None:
        self.session_mgr = session_mgr
        self.artifact_store = artifact_store
        self.prompt_loader = prompt_loader
        self.validator = validator
        self.workflow = workflow
        self.framework_config = framework_config or {}
        self._runtime_skill_resolver = None
        self.max_retry = 3
        self.phase_specs = {
            "phase_0": PhaseSpec("phase_0", "phase_0_env_detect", "env_detect"),
            "phase_0_env_detect": PhaseSpec("phase_0", "phase_0_env_detect", "env_detect"),
            "phase_1": PhaseSpec("phase_1", "phase_1_project_analysis", "project_analysis"),
            "phase_1_project_analysis": PhaseSpec("phase_1", "phase_1_project_analysis", "project_analysis"),
            "phase_2": PhaseSpec("phase_2", "phase_2_venv_create", "venv"),
            "phase_2_venv_create": PhaseSpec("phase_2", "phase_2_venv_create", "venv"),
            "phase_3": PhaseSpec("phase_3", "phase_3_entry_script", "entry_script"),
            "phase_3_entry_script": PhaseSpec("phase_3", "phase_3_entry_script", "entry_script"),
            "phase_35": PhaseSpec("phase_35", "phase_35_static_validate", "entry_static"),
            "phase_35_static_validate": PhaseSpec("phase_35", "phase_35_static_validate", "entry_static"),
        }
        self._runtime_phase_index = self._build_runtime_phase_index(workflow)
        self._register_default_validators()
        self._container_context: dict[str, str] = {}
        self._exec_env_context: str = ""

    def set_container_context(self, ctx: dict[str, str]) -> None:
        self._container_context = dict(ctx)

    def set_execution_environment_context(self, ctx: str) -> None:
        self._exec_env_context = ctx

    @staticmethod
    def _session_error_from_response(response: str | None) -> str | None:
        if not response:
            return None
        text = response.strip()
        if not (text.startswith("{") and text.endswith("}")):
            return None
        try:
            parsed = cast(object, json.loads(text))
        except json.JSONDecodeError:
            return None
        if isinstance(parsed, dict):
            parsed_dict = cast(dict[str, object], parsed)
        else:
            return None
        if parsed_dict.get("ok") is False and parsed_dict.get("error"):
            return str(parsed_dict["error"])
        return None

    def run_single_phase(
        self,
        session: str | InlineSessionLike,
        phase_id: str,
        context: JsonObject,
    ) -> JsonObject:
        return self._run_single_phase(
            session=session,
            phase_id=phase_id,
            context=context,
            session_mgr=self.session_mgr,
            artifact_store=self.artifact_store,
        )

    def run_phase_0_to_3(
        self,
        project_dir: str,
        session_mgr: SessionManagerLike,
        artifact_store: ArtifactStore,
    ) -> dict[str, JsonObject]:
        active_session_mgr = session_mgr or self.session_mgr
        active_artifact_store = artifact_store or self.artifact_store
        session_id = active_session_mgr.get_or_create(role="main_engineer", lifecycle="persistent")

        outputs: dict[str, JsonObject] = {}
        for phase_id in self.PHASE_ORDER:
            phase_context: JsonObject = {
                "project_dir": project_dir,
                "previous_outputs": outputs,
            }
            result = self._run_single_phase(
                session=session_id,
                phase_id=phase_id,
                context=phase_context,
                session_mgr=active_session_mgr,
                artifact_store=active_artifact_store,
            )
            outputs[self._resolve_phase_spec(phase_id).prompt_id] = result

        return outputs

    def run_phase_0_to_1(
        self,
        project_dir: str,
        session_mgr: SessionManagerLike,
        artifact_store: ArtifactStore,
        user_constraints: str = "",
    ) -> dict[str, JsonObject]:
        """Run Phase 0 and Phase 1 only.

        Args:
            project_dir: Root directory of the project.
            session_mgr: Session manager for sending commands.
            artifact_store: ArtifactStore for saving outputs.
            user_constraints: User-defined constraints (empty string = none).

        Returns:
            Dict with phase_0_env_detect and phase_1_project_analysis outputs.
        """
        active_session_mgr = session_mgr or self.session_mgr
        active_artifact_store = artifact_store or self.artifact_store
        session_id = active_session_mgr.get_or_create(role="main_engineer", lifecycle="persistent")

        outputs: dict[str, JsonObject] = {}
        for phase_id in ("phase_0", "phase_1"):
            context: JsonObject = {
                "project_dir": project_dir,
                "previous_outputs": outputs,
                "user_constraints": user_constraints,
            }
            result = self._run_single_phase(
                session=session_id,
                phase_id=phase_id,
                context=context,
                session_mgr=active_session_mgr,
                artifact_store=active_artifact_store,
            )
            outputs[self._resolve_phase_spec(phase_id).prompt_id] = result

        return outputs

    def run_phase_2_to_3(
        self,
        project_dir: str,
        session_mgr: SessionManagerLike,
        artifact_store: ArtifactStore,
        prior_outputs: dict[str, JsonObject],
        constraint_summary: str = "",
    ) -> dict[str, JsonObject]:
        """Run Phase 2 and Phase 3 only.

        Args:
            project_dir: Root directory of the project.
            session_mgr: Session manager for sending commands.
            artifact_store: ArtifactStore for saving outputs.
            prior_outputs: Outputs from Phase 0-1.
            constraint_summary: Constraint summary from Phase 1.5 (empty string = none).

        Returns:
            Dict with phase_2_venv_create and phase_3_entry_script outputs.
        """
        active_session_mgr = session_mgr or self.session_mgr
        active_artifact_store = artifact_store or self.artifact_store
        session_id = active_session_mgr.get_or_create(role="main_engineer", lifecycle="persistent")

        outputs: dict[str, JsonObject] = dict(prior_outputs)
        phase_2_context: JsonObject = {
            "project_dir": project_dir,
            "previous_outputs": outputs,
            "constraint_summary": constraint_summary,
        }
        phase_2_result = self._run_single_phase(
            session=session_id,
            phase_id="phase_2",
            context=phase_2_context,
            session_mgr=active_session_mgr,
            artifact_store=active_artifact_store,
        )
        outputs[self._resolve_phase_spec("phase_2").prompt_id] = phase_2_result

        last_phase_35_error: ValueError | None = None
        for outer_attempt in range(1, self.max_retry + 1):
            phase_3_context: JsonObject = {
                "project_dir": project_dir,
                "previous_outputs": outputs,
                "constraint_summary": constraint_summary,
            }
            phase_3_result = self._run_single_phase(
                session=session_id,
                phase_id="phase_3",
                context=phase_3_context,
                session_mgr=active_session_mgr,
                artifact_store=active_artifact_store,
            )
            outputs[self._resolve_phase_spec("phase_3").prompt_id] = phase_3_result

            try:
                phase_35_context: JsonObject = {
                    "project_dir": project_dir,
                    "previous_outputs": outputs,
                    "constraint_summary": constraint_summary,
                }
                phase_35_result = self._run_single_phase(
                    session=session_id,
                    phase_id="phase_35",
                    context=phase_35_context,
                    session_mgr=active_session_mgr,
                    artifact_store=active_artifact_store,
                )
            except ValueError as exc:
                last_phase_35_error = exc
                outputs["phase_35_static_validate_failure"] = {
                    "attempt": outer_attempt,
                    "error": str(exc),
                }
                if outer_attempt >= self.max_retry:
                    raise
                continue

            outputs[self._resolve_phase_spec("phase_35").prompt_id] = phase_35_result
            _ = outputs.pop("phase_35_static_validate_failure", None)
            return outputs

        if last_phase_35_error is not None:
            raise last_phase_35_error
        return outputs

    def run_phase_1_5(
        self,
        main_session_id: str,
        session_mgr: SessionManagerLike,
        artifact_store: ArtifactStore,
        *,
        project_dir: str,
        user_constraints: str,
        phase_1_output: JsonObject | None = None,
    ) -> str:
        """Generate a constraint summary from user constraints + Phase 1 analysis.

        Args:
            main_session_id: The persistent main_engineer session ID.
            session_mgr: Session manager for sending commands.
            artifact_store: ArtifactStore for saving the output.
            project_dir: Root directory of the project.
            user_constraints: Raw user constraint text.
            phase_1_output: Phase 1 analysis output (optional).

        Returns:
            The constraint summary string.

        Raises:
            ValueError: If the response cannot be parsed.
        """
        phase_1_context = (
            self._serialize_context(phase_1_output)
            if phase_1_output
            else "(No phase 1 context available)"
        )

        prompt = self.prompt_loader.load_prompt(
            "phase_1_5_constraint_summary",
            {
                "project_dir": project_dir,
                "phase_1_context": phase_1_context,
                "user_constraints": user_constraints,
                **self._container_context,
            },
        )
        prompt = self._append_explicit_runtime_skill_markdown(
            prompt,
            "phase_1_5_constraint_summary",
        )
        prompt = inject_phase_boundary(prompt, framework_config=self.framework_config)

        raw_response = session_mgr.send_command(main_session_id, prompt, timeout=None)
        parsed: JsonObject = dict(extract_json_response(raw_response))
        constraint_summary = str(parsed.get("constraint_summary", ""))

        artifact_data: JsonObject = {
            "constraint_summary": constraint_summary,
            "constraint_count": parsed.get("constraint_count", 0),
            "challenges_flagged": parsed.get("challenges_flagged", []),
        }
        _ = artifact_store.save_phase_output("phase_1_5_constraint_summary", artifact_data, attempt=1)
        _ = artifact_store.mark_validated("phase_1_5_constraint_summary", artifact_data)
        _ = artifact_store.write_journal(
            {
                "phase_id": "phase_1_5_constraint_summary",
                "attempt": 1,
                "status": "succeeded",
                "session_ref": main_session_id,
                "raw_path": "",
                "canonical_path": "",
                "errors": [],
                "warnings": [],
            }
        )

        return constraint_summary

    def run_review_check(
        self,
        review_session_id: str,
        session_mgr: SessionManagerLike,
        project_dir: str,
        repair_history: str,
        *,
        last_artifact_path: str = "(no artifact available)",
        attempt_log_content: str = "(attempt log unavailable)",
        execution_duration: str = "(not available)",
        max_retry: int = 2,
    ) -> JsonObject:
        """Run a review of a repair iteration and assess NPU compliance.

        Args:
            review_session_id: The session ID for sending the review prompt.
            session_mgr: Session manager for sending commands.
            project_dir: Root directory of the project.
            repair_history: Markdown table of all repair iterations (from _format_history_summary).
            last_artifact_path: Path to the most recent validation attempt JSON.
            attempt_log_content: Extracted stdout/stderr/error from the most recent validation attempt.
            execution_duration: Duration of the last validation run in seconds.
            max_retry: Maximum number of review response validation attempts.

        Returns:
            Dict with keys: verdict, cpu_fallback_detected, cpu_fallback_necessary,
                alternative_suggestions, reasoning.
        """
        prompt = self.prompt_loader.load_prompt(
            "phase_5_review",
            {
                "repair_history": repair_history,
                "project_dir": project_dir,
                "last_artifact_path": last_artifact_path,
                "attempt_log_content": attempt_log_content,
                "execution_duration": execution_duration,
            },
        )
        prompt = self._append_explicit_runtime_skill_markdown(prompt, "phase_5_review")
        prompt = inject_phase_boundary(prompt, framework_config=self.framework_config)

        active_prompt = prompt
        parsed: JsonObject = {}

        for attempt in range(1, max_retry + 1):
            raw_response = session_mgr.send_command(review_session_id, active_prompt, timeout=None)
            session_error = self._session_error_from_response(raw_response)
            if session_error:
                return {
                    "verdict": "session_error",
                    "cpu_fallback_detected": False,
                    "cpu_fallback_necessary": False,
                    "alternative_suggestions": "",
                    "reasoning": f"Review session command failed: {session_error}",
                    "session_error": session_error,
                    "raw_response": raw_response,
                }
            parsed = dict(extract_json_response(raw_response))

            verdict = str(parsed.get("verdict", "")).lower()
            if verdict in ("accept", "reject"):
                break

            if attempt < max_retry:
                error_details = "Verdict was missing or invalid"
                if not parsed:
                    error_details = "Response contained no parseable JSON"
                elif verdict not in ("accept", "reject"):
                    error_details = (
                        f"Verdict was '{verdict}' - must be either 'accept' or 'reject'"
                    )

                active_prompt = (
                    "Your previous review response failed validation:\n"
                    f"{error_details}\n\n"
                    "Please provide ONLY a valid review verdict. "
                    "The verdict must be exactly 'accept' or 'reject' (no other values).\n"
                    "Respond with a JSON code block containing: verdict, "
                    "cpu_fallback_detected, cpu_fallback_necessary, "
                    "alternative_suggestions, reasoning."
                )

        return {
            "verdict": str(parsed.get("verdict", "unknown")),
            "cpu_fallback_detected": bool(parsed.get("cpu_fallback_detected", False)),
            "cpu_fallback_necessary": bool(parsed.get("cpu_fallback_necessary", False)),
            "alternative_suggestions": str(parsed.get("alternative_suggestions", "")),
            "reasoning": str(parsed.get("reasoning", "")),
        }

    def run_phase_4(
        self,
        artifact_store: ArtifactStore,
        migrator: RuleBasedMigrator,
    ) -> dict[str, object]:
        """Run Phase 4: Rule-based CUDA-to-NPU migration.

        Reads project_dir from Phase 3 output in ArtifactStore,
        runs RuleBasedMigrator.migrate_directory(), validates,
        and saves migration report.

        Args:
            artifact_store: ArtifactStore with Phase 3 output.
            migrator: RuleBasedMigrator instance.

        Returns:
            Migration report dict.

        Raises:
            ValueError: If Phase 3 output missing or validation fails.
        """
        from typing import cast

        phase_3_output = artifact_store.load_phase_output("phase_3_entry_script")
        if phase_3_output is None:
            raise ValueError("Phase 3 output (phase_3_entry_script) not found in ArtifactStore")

        project_dir = cast(dict[str, object], phase_3_output).get("project_dir", "")
        if not project_dir:
            raise ValueError("project_dir not found in Phase 3 output")

        raw_report = migrator.migrate_directory(str(project_dir), pattern="*.py")

        summary = cast(dict[str, object], raw_report.get("summary", {}))
        files_skipped = 0
        files_data = cast(dict[str, object], raw_report.get("files", {}))
        for _, file_report in files_data.items():
            if isinstance(file_report, dict) and "error" in file_report:
                files_skipped += 1

        report: dict[str, object] = {
            "files_migrated": cast(int, summary.get("total_files", 0)),
            "files_skipped": files_skipped,
            "replacement_counts": cast(dict[str, object], summary.get("rules", {})),
            "total_replacements": cast(int, summary.get("total_replacements", 0)),
            "project_dir": str(project_dir),
        }

        # Validate
        validation = validate_rule_migration(report)
        if not validation["passed"]:
            raise ValueError(
                f"Phase 4 migration report failed validation: {'; '.join(validation['errors'])}"
            )

        # Save to ArtifactStore
        _ = artifact_store.save_phase_output("phase_4_rule_migration", report, attempt=1)
        _ = artifact_store.mark_validated("phase_4_rule_migration", report)
        _ = artifact_store.write_journal({
            "phase_id": "phase_4_rule_migration",
            "attempt": 1,
            "status": "succeeded",
            "session_ref": "local_script",
            "raw_path": "",
            "canonical_path": "",
            "errors": validation["errors"],
            "warnings": validation["warnings"],
        })

        return report

    @staticmethod
    def _artifact_report_dir(artifact_store: ArtifactStore, project_dir: str) -> str:
        artifact_dir = getattr(artifact_store, "artifact_dir", None)
        if isinstance(artifact_dir, str):
            return os.path.join(artifact_dir, "reports")
        if isinstance(artifact_dir, Path):
            return str(artifact_dir / "reports")
        return str(Path(project_dir) / ".sm-artifacts" / "reports")

    def run_phase_6(
        self,
        project_dir: str,
        artifact_store: ArtifactStore,
        session_mgr: SessionManagerLike | None = None,
    ) -> dict[str, object]:
        """Run Phase 6: Final report generation.

        Reads all prior phase outputs from ArtifactStore, reuses the persistent
        main_engineer session, sends the phase_6_report prompt, and saves the
        resulting report manifest.

        Args:
            project_dir: Root directory of the migrated project.
            artifact_store: ArtifactStore with Phase 0-5 outputs.
            session_mgr: SessionManager; falls back to self.session_mgr.

        Returns:
            Dict with report paths, migration_summary, and phase_6 metadata.
        """
        active_session_mgr = session_mgr or self.session_mgr

        session_id = active_session_mgr.get_or_create(
            role="main_engineer", lifecycle="persistent"
        )

        prior_artifacts = collect_phase6_prior_artifacts(artifact_store)

        report_dir = self._artifact_report_dir(artifact_store, str(project_dir))
        os.makedirs(report_dir, exist_ok=True)

        prompt_context = {
            "phase_name": "phase_6_report",
            "project_dir": str(project_dir),
            "previous_outputs": self._serialize_context(prior_artifacts),
            "report_dir": report_dir,
        }
        for k, v in self._container_context.items():
            _ = prompt_context.setdefault(k, v)
        for k, v in _get_exec_ctx(None).items():
            _ = prompt_context.setdefault(k, v)
        _ = prompt_context.setdefault("execution_environment_context", self._exec_env_context or _get_env_ctx(None))

        prompt = self.prompt_loader.load_prompt("phase_6_report", prompt_context)
        prompt = self._append_explicit_runtime_skill_markdown(prompt, "phase_6_report")
        prompt = inject_phase_boundary(prompt, framework_config=self.framework_config)

        fallback_reason = ""
        try:
            raw_response = active_session_mgr.send_command(
                session_id, prompt, timeout=self._phase_6_timeout(), retries=0
            )
            fallback_reason = self._session_error_from_response(raw_response) or ""
        except (TimeoutError, RuntimeError, ConnectionRefusedError) as exc:
            raw_response = ""
            fallback_reason = str(exc)

        if fallback_reason:
            report = build_phase6_fallback_report(
                project_dir=str(project_dir),
                report_dir=report_dir,
                prior_outputs=prior_artifacts,
                reason=fallback_reason,
            )
        else:
            parsed: dict[str, object] = dict(extract_json_response(raw_response))
            if not self._phase_6_output_complete(parsed):
                report = build_phase6_fallback_report(
                    project_dir=str(project_dir),
                    report_dir=report_dir,
                    prior_outputs=prior_artifacts,
                    reason="Phase 6 LLM response omitted required report fields",
                )
            else:
                report = {
                    "phase_id": "phase_6_report",
                    "report_paths": parsed.get("report_paths", []),
                    "migration_summary": parsed.get("migration_summary", {}),
                    "project_dir": str(project_dir),
                }

        raw_path = artifact_store.save_phase_output("phase_6_report", report, attempt=1)
        canonical_path = artifact_store.mark_validated("phase_6_report", report)
        _ = artifact_store.write_journal({
            "phase_id": "phase_6_report",
            "attempt": 1,
            "status": "fallback" if report.get("fallback") else "succeeded",
            "session_ref": session_id,
            "raw_path": raw_path,
            "canonical_path": canonical_path,
            "errors": [],
            "warnings": [],
        })

        return report

    def _phase_6_timeout(self) -> int:
        runtime_phase = self._runtime_phase_index.get("phase_6_report")
        phase_timeout = runtime_phase.timeout if runtime_phase else None
        return resolve_phase6_timeout(self.framework_config, phase_timeout, logger)

    @staticmethod
    def _phase_6_output_complete(output: dict[str, object]) -> bool:
        report_paths = output.get("report_paths")
        migration_summary = output.get("migration_summary")
        return isinstance(report_paths, list) and isinstance(migration_summary, dict)

    def _run_single_phase(
        self,
        session: str | InlineSessionLike,
        phase_id: str,
        context: JsonObject | None,
        session_mgr: SessionManagerLike,
        artifact_store: ArtifactStore,
    ) -> JsonObject:
        phase = self._resolve_phase_spec(phase_id)
        normalized_context: JsonObject = dict(context or {})
        prompt_context = self._build_prompt_context(phase, normalized_context)
        prompt = self.prompt_loader.load_prompt(phase.prompt_id, prompt_context)
        prompt = self._append_explicit_runtime_skill_markdown(prompt, phase.prompt_id)
        prompt = inject_phase_boundary(prompt, framework_config=self.framework_config)
        max_retry = self._resolve_max_retry(normalized_context)
        timeout = self._resolve_timeout(phase, normalized_context)
        session_ref = self._session_reference(session)

        last_validation = ValidationResult(passed=False, errors=["phase did not execute"], warnings=[])
        active_prompt = prompt
        for attempt in range(1, max_retry + 1):
            raw_response = self._send_prompt(session, active_prompt, timeout, session_mgr)
            parsed_output: JsonObject = dict(extract_json_response(raw_response))
            output_format = self._extract_output_format_from_prompt(active_prompt)
            parse_attempt = 0
            while not parsed_output and parse_attempt < 2:
                parse_attempt += 1
                parse_prompt = build_validation_correction_prompt(
                    "Your response did not contain a valid JSON object.",
                    output_format_example=output_format,
                    is_parse_failure=True,
                    phase_name=phase.prompt_id,
                )
                raw_response = self._send_prompt(session, parse_prompt, timeout, session_mgr)
                parsed_output = dict(extract_json_response(raw_response))
            normalized_output = self._normalize_output(phase, parsed_output, prompt_context, normalized_context)

            # Attach raw prompt and response for end-to-end verification.
            # Keys are prefixed with `_` to avoid conflicts with phase output schemas.
            normalized_output["_meta"] = {
                "prompt": active_prompt,
                "response": raw_response,
            }

            raw_path = artifact_store.save_phase_output(phase.artifact_id, normalized_output, attempt=attempt)

            validation = self.validator.validate(phase.validator_name, normalized_output)
            last_validation = validation
            if validation.passed:
                assisted_result = self._run_assisted_verification(
                    phase=phase,
                    output=normalized_output,
                    context=normalized_context,
                    session_mgr=session_mgr,
                    artifact_store=artifact_store,
                    attempt=attempt,
                )
                if assisted_result is not None:
                    normalized_output = attach_assisted_summary(normalized_output, assisted_result)
                    if not assisted_result.passed:
                        last_validation = ValidationResult(
                            passed=False,
                            errors=assisted_result.errors or ["assisted verification failed"],
                            warnings=assisted_result.warnings,
                        )
                        _ = artifact_store.write_journal(
                            self._build_journal_entry(
                                phase=phase,
                                attempt=attempt,
                                status="assisted_verification_failed",
                                session_ref=session_ref,
                                raw_path=raw_path,
                                canonical_path="",
                                validation=last_validation,
                            )
                        )
                        if attempt < max_retry:
                            active_prompt = assisted_result.correction_prompt or self._build_correction_prompt(
                                phase=phase,
                                validation=last_validation,
                                previous_prompt=prompt,
                            )
                            continue
                        break
                validated_output = dict(normalized_output)
                _ = validated_output.pop("_meta", None)
                canonical_path = artifact_store.mark_validated(phase.artifact_id, validated_output)
                _ = artifact_store.write_journal(
                    self._build_journal_entry(
                        phase=phase,
                        attempt=attempt,
                        status="succeeded",
                        session_ref=session_ref,
                        raw_path=raw_path,
                        canonical_path=canonical_path,
                        validation=validation,
                    )
                )
                return normalized_output

            _ = artifact_store.write_journal(
                self._build_journal_entry(
                    phase=phase,
                    attempt=attempt,
                    status="validation_failed",
                    session_ref=session_ref,
                    raw_path=raw_path,
                    canonical_path="",
                    validation=validation,
                )
            )

            if attempt < max_retry:
                active_prompt = self._build_correction_prompt(
                    phase=phase,
                    validation=validation,
                    previous_prompt=prompt,
                )

        error_text = "; ".join(last_validation.errors) or "unknown validation failure"
        raise ValueError(f"{phase.prompt_id} failed validation after {max_retry} attempts: {error_text}")

    @staticmethod
    def _build_correction_prompt(
        *,
        phase: PhaseSpec,
        validation: ValidationResult,
        previous_prompt: str,
    ) -> str:
        return build_phase_correction_prompt(
            phase_name=phase.prompt_id,
            validation_errors=[str(error) for error in validation.errors],
            output_format_example=PhaseRunner._extract_output_format_from_prompt(previous_prompt),
        )

    @staticmethod
    def _extract_output_format_from_prompt(prompt_text: str) -> str | None:
        return extract_output_format_from_prompt(prompt_text)

    def _run_assisted_verification(
        self,
        *,
        phase: PhaseSpec,
        output: JsonObject,
        context: JsonObject,
        session_mgr: SessionManagerLike,
        artifact_store: ArtifactStore,
        attempt: int,
    ) -> AssistedVerificationResult | None:
        if phase.prompt_id not in {"phase_1_project_analysis", "phase_3_entry_script"}:
            return None
        runner = AssistedVerificationRunner(
            session_mgr=session_mgr,
            artifact_store=artifact_store,
            framework_config=self.framework_config,
        )
        if not runner.config.enabled:
            return None
        project_dir = self._assisted_project_dir(context)
        phase_output = self._without_meta(output)
        if phase.prompt_id == "phase_1_project_analysis":
            return runner.verify_phase1(
                phase_output=phase_output,
                project_dir=project_dir,
                attempt=attempt,
            )

        return runner.verify_phase3(
            phase_output=phase_output,
            phase1_output=self._phase1_output_from_context(context),
            project_dir=project_dir,
            attempt=attempt,
        )

    def _assisted_project_dir(self, context: JsonObject) -> str:
        for key in ("project_dir", "PROJECT_DIR"):
            value = context.get(key)
            if isinstance(value, str) and value.strip():
                return value
        return "."

    @classmethod
    def _phase1_output_from_context(cls, context: JsonObject) -> dict[str, object] | None:
        previous_outputs = context.get("previous_outputs")
        if not isinstance(previous_outputs, dict):
            return None
        outputs = cast(dict[str, object], previous_outputs)
        for key in ("phase_1_project_analysis", "phase_1"):
            phase_output = outputs.get(key)
            if isinstance(phase_output, dict):
                return cls._without_meta(cast(dict[str, object], phase_output))
        return None

    @staticmethod
    def _without_meta(output: dict[str, object]) -> dict[str, object]:
        clean = dict(output)
        _ = clean.pop("_meta", None)
        return clean

    def _register_default_validators(self) -> None:
        registrations = {
            "env_detect": validate_env_detect,
            "project_analysis": validate_project_analysis,
            "venv": validate_venv,
            "entry_script": validate_entry_script,
            "entry_static": validate_entry_static,
            "phase_1_custom_op_completeness_check": validate_phase_1_custom_op_completeness,
            "phase_3_custom_op_contract_coverage_check": validate_phase_3_custom_op_contract_coverage,
            "reports": validate_reports,
        }
        for name, validator_fn in registrations.items():
            self.validator.register_validator(name, validator_fn)

    def _resolve_phase_spec(self, phase_id: str) -> PhaseSpec:
        phase = self.phase_specs.get(phase_id)
        if phase is None:
            raise ValueError(f"Unsupported phase_id: {phase_id}")
        return phase

    def _build_runtime_phase_index(
        self,
        workflow: WorkflowDefinition | None,
    ) -> dict[str, PhaseDefinition]:
        if workflow is None:
            return {}

        index: dict[str, PhaseDefinition] = {}
        for phase in workflow.phases or []:
            self._index_runtime_phase(index, phase)

        for sub_workflow in (workflow.sub_workflows or {}).values():
            sub_workflow_phases = cast(list[object], getattr(sub_workflow, "phases", []) or [])
            for phase_item in sub_workflow_phases:
                phase = self._runtime_phase_from_item(
                    phase_item,
                    "sub_workflows.phases",
                )
                if phase is not None:
                    self._index_runtime_phase(index, phase)

            blocks = getattr(sub_workflow, "blocks", {}) or {}
            if not isinstance(blocks, dict):
                continue
            block_items = cast(dict[object, object], blocks)
            for block_id, block in block_items.items():
                if not isinstance(block, dict):
                    continue
                block_mapping = cast(dict[str, object], block)
                block_phases = block_mapping.get("phases", [])
                if not isinstance(block_phases, list):
                    continue
                for phase_item in cast(list[object], block_phases):
                    phase = self._runtime_phase_from_item(
                        phase_item,
                        f"sub_workflows.blocks[{block_id}].phases",
                    )
                    if phase is not None:
                        self._index_runtime_phase(index, phase)

        return index

    @staticmethod
    def _index_runtime_phase(index: dict[str, PhaseDefinition], phase: PhaseDefinition) -> None:
        for key in (phase.id, phase.prompt_template):
            if key:
                _ = index.setdefault(str(key), phase)

    def _runtime_phase_from_item(
        self,
        phase_item: object,
        location: str,
    ) -> PhaseDefinition | None:
        if isinstance(phase_item, PhaseDefinition):
            return phase_item
        if not isinstance(phase_item, dict):
            return None

        phase_dict = cast(dict[str, object], phase_item)
        phase_id = str(phase_dict.get("id", "unnamed"))
        output_schema_value = phase_dict.get("output_schema", {})
        output_schema = (
            cast(dict[str, object], output_schema_value)
            if isinstance(output_schema_value, dict)
            else {}
        )
        agent = phase_dict.get("agent")
        timeout_value = phase_dict.get("timeout")
        return PhaseDefinition(
            id=phase_id,
            name=str(phase_dict.get("name", phase_id)),
            prompt_template=str(phase_dict.get("prompt_template", "")),
            output_schema=output_schema,
            validator=phase_dict.get("validator"),
            transitions={},
            type=str(phase_dict.get("type", "llm")),
            agent=str(agent) if agent else None,
            timeout=timeout_value if isinstance(timeout_value, int) else None,
            runtime_skills=self._coerce_runtime_skills_config(
                phase_dict.get("runtime_skills"),
                f"{location}[{phase_id}].runtime_skills",
            ),
        )

    def _runtime_skill_repo_root(self) -> Path:
        configured_root = self.framework_config.get("runtime_skill_repo_root")
        if not configured_root:
            runtime_skills_cfg = self.framework_config.get("runtime_skills")
            if isinstance(runtime_skills_cfg, dict):
                runtime_skills_dict = cast(dict[str, object], runtime_skills_cfg)
                configured_root = runtime_skills_dict.get("repo_root")
        if configured_root:
            return resolve_relative_path(Path(str(configured_root)))
        return workspace_root()

    def _get_runtime_skill_resolver(self) -> RuntimeSkillResolver:
        if self._runtime_skill_resolver is None:
            self._runtime_skill_resolver = RuntimeSkillResolver(
                self._runtime_skill_repo_root()
            )
        return self._runtime_skill_resolver

    def _runtime_skill_names(self, value: object, location: str) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError(
                f"{location} must be a list of skill names, got {type(value).__name__}"
            )
        names: list[str] = []
        for index, item in enumerate(cast(list[object], value)):
            if not isinstance(item, str) or not item.strip():
                raise ValueError(f"{location}[{index}] must be a non-empty string")
            names.append(item.strip())
        return names

    def _coerce_runtime_skills_config(
        self,
        raw: object,
        location: str,
    ) -> RuntimeSkillsConfig | None:
        if raw is None or isinstance(raw, RuntimeSkillsConfig):
            return raw
        if isinstance(raw, list):
            return RuntimeSkillsConfig(include=self._runtime_skill_names(cast(list[object], raw), location))
        if not isinstance(raw, dict):
            raise ValueError(
                f"{location} must be a list or mapping, got {type(raw).__name__}"
            )

        raw_dict = cast(dict[str, object], raw)
        merge = str(raw_dict.get("merge", "append"))
        if merge not in {"append", "replace", "none"}:
            raise ValueError(
                f"{location}.merge must be one of ['append', 'none', 'replace'], got '{merge}'"
            )
        missing = str(raw_dict.get("missing", "warn"))
        if missing not in {"warn", "error", "ignore"}:
            raise ValueError(
                f"{location}.missing must be one of ['error', 'ignore', 'warn'], got '{missing}'"
            )

        return RuntimeSkillsConfig(
            include=self._runtime_skill_names(raw_dict.get("include", []), f"{location}.include"),
            exclude=self._runtime_skill_names(raw_dict.get("exclude", []), f"{location}.exclude"),
            merge=merge,
            missing=missing,
            inject_full=bool(raw_dict.get("inject_full", False)),
            exclude_dynamic_duplicates=bool(raw_dict.get("exclude_dynamic_duplicates", True)),
        )

    def _agent_runtime_skill_config(self, agent_id: str) -> RuntimeSkillsConfig | None:
        if self.workflow is None:
            return None
        agent_cfg = (self.workflow.agents or {}).get(agent_id)
        if not isinstance(agent_cfg, dict):
            return None
        return self._coerce_runtime_skills_config(
            agent_cfg.get("runtime_skills"),
            f"agents.{agent_id}.runtime_skills",
        )

    def _resolve_runtime_skill_bundle(
        self,
        phase: PhaseDefinition,
        agent_id: str,
    ) -> RuntimeSkillBundle | None:
        agent_config = self._agent_runtime_skill_config(agent_id)
        phase_config = self._coerce_runtime_skills_config(
            phase.runtime_skills,
            f"phases[{phase.id}].runtime_skills",
        )
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
        prompt_id: str,
    ) -> str:
        phase = self._runtime_phase_index.get(prompt_id)
        if phase is None:
            return prompt_text
        agent_id = phase.agent or "main_engineer"
        bundle = self._resolve_runtime_skill_bundle(phase, agent_id)
        if not bundle or not bundle.markdown:
            return prompt_text

        if prompt_text.endswith("\n\n"):
            separator = ""
        elif prompt_text.endswith("\n"):
            separator = "\n"
        else:
            separator = "\n\n"
        logger.info(
            "[INJECT RUNTIME SKILLS %s] Skills=%s",
            phase.id,
            ", ".join(bundle.names),
        )
        return f"{prompt_text}{separator}{bundle.markdown}"

    # Phase-aware previous_outputs whitelist (shared with WorkflowExecutor strategy).
    _PREVIOUS_OUTPUTS_WHITELIST: dict[str, list[str]] = {
        "phase_0_env_detect": [],
        "phase_1_project_analysis": [],
        "phase_2_venv_create": [],
        "phase_1_5_constraint_summary": [],
        "phase_3_entry_script": [],
        "phase_35_static_validate": ["phase_3_entry_script"],
    }

    def _filter_previous_outputs(self, prompt_id: str, previous_outputs: JsonObject) -> JsonObject:
        allowed = self._PREVIOUS_OUTPUTS_WHITELIST.get(prompt_id)
        if allowed is None:
            return previous_outputs  # no whitelist → legacy "all" behaviour
        if not allowed:
            return {}
        return {k: v for k, v in previous_outputs.items() if k in allowed}

    def _build_prompt_context(self, phase: PhaseSpec, context: JsonObject) -> dict[str, str]:
        previous_outputs = context.get("previous_outputs", {})
        previous_output_map = cast(JsonObject, previous_outputs) if isinstance(previous_outputs, dict) else {}
        prompt_ctx: dict[str, str] = {
            "phase_name": str(context.get("phase_name", phase.prompt_id)),
            "project_dir": str(context.get("project_dir", ".")),
            "constraint_summary": str(context.get("constraint_summary", "")),
            "user_constraints": str(context.get("user_constraints", "")),
        }
        if phase.prompt_id not in self._SHARED_SESSION_PHASES:
            filtered = self._filter_previous_outputs(phase.prompt_id, previous_output_map)
            prompt_ctx["previous_outputs"] = self._serialize_context(filtered)
        if phase.prompt_id == "phase_35_static_validate":
            filtered = self._filter_previous_outputs(phase.prompt_id, previous_output_map)
            prompt_ctx["previous_outputs"] = self._serialize_context(filtered)
            entry_script = self._lookup_previous_output(filtered, "phase_3_entry_script", "entry_script_path")
            prompt_ctx["entry_script_path"] = str(entry_script) if entry_script else "(not available)"
        for k, v in self._container_context.items():
            _ = prompt_ctx.setdefault(k, v)
        for k, v in _get_exec_ctx(None).items():
            _ = prompt_ctx.setdefault(k, v)
        _ = prompt_ctx.setdefault("execution_environment_context", self._exec_env_context or _get_env_ctx(None))
        return prompt_ctx

    @staticmethod
    def _serialize_context(value: object) -> str:
        if isinstance(value, str):
            return value
        return json.dumps(value, indent=2, ensure_ascii=False, default=str)

    def _resolve_max_retry(self, context: JsonObject) -> int:
        raw_value = context.get("max_retry", self.max_retry)
        if isinstance(raw_value, bool):
            max_retry = int(raw_value)
        elif isinstance(raw_value, int):
            max_retry = raw_value
        elif isinstance(raw_value, str):
            max_retry = int(raw_value)
        else:
            raise ValueError("max_retry must be an integer")
        if max_retry < 1:
            raise ValueError("max_retry must be >= 1")
        return max_retry

    @staticmethod
    def _resolve_timeout(phase: PhaseSpec, context: JsonObject) -> int | None:
        raw_timeout = context.get("timeout", phase.timeout)
        if raw_timeout is None:
            return None
        if isinstance(raw_timeout, bool):
            return int(raw_timeout)
        if isinstance(raw_timeout, int):
            return raw_timeout
        if isinstance(raw_timeout, str):
            return int(raw_timeout)
        raise ValueError("timeout must be an integer or null")

    def _send_prompt(
        self,
        session: str | InlineSessionLike,
        prompt: str,
        timeout: int | None,
        session_mgr: SessionManagerLike,
    ) -> str:
        if isinstance(session, str):
            return session_mgr.send_command(session, prompt, timeout=timeout)
        return session.send_command(prompt, timeout=timeout)

    def _normalize_output(
        self,
        phase: PhaseSpec,
        output: JsonObject,
        prompt_context: dict[str, str],
        context: JsonObject,
    ) -> JsonObject:
        normalized = dict(output)
        if phase.prompt_id == "phase_0_env_detect" and "python_version" not in normalized:
            normalized["python_version"] = self._current_python_version()
        if phase.prompt_id == "phase_1_project_analysis":
            normalized["project_dir"] = str(prompt_context["project_dir"])
            normalize_project_analysis_expanded_variants(normalized)
            normalize_serving_phase1_surface(normalized)
        if phase.prompt_id == "phase_3_entry_script":
            previous_outputs = context.get("previous_outputs", {})
            previous_output_map = cast(dict[str, object], previous_outputs) if isinstance(previous_outputs, dict) else {}
            phase1_route = self._lookup_previous_output(previous_output_map, "phase_1_project_analysis", "migration_route")
            if "entry_script_path" not in normalized:
                entry_script = self._lookup_previous_output(previous_output_map, "phase_1_project_analysis", "entry_script")
                if isinstance(entry_script, str) and entry_script:
                    normalized["entry_script_path"] = entry_script
            raw_workflow_globals_phase35: object = getattr(self.workflow, "globals", None) or {} if self.workflow else {}
            workflow_globals = cast(dict[str, object], raw_workflow_globals_phase35) if isinstance(raw_workflow_globals_phase35, dict) else {}
            if self._custom_op_route_disabled(workflow_globals):
                normalized = self._strip_custom_op_contract_fields(normalized)
            else:
                if isinstance(phase1_route, str) and phase1_route in SERVING_ROUTES:
                    raw_phase1_output = previous_output_map.get("phase_1_project_analysis")
                    phase1_output = cast(JsonObject, raw_phase1_output) if isinstance(raw_phase1_output, dict) else None
                    normalize_serving_phase3_contract(
                        normalized,
                        route=phase1_route,
                        project_dir=str(prompt_context["project_dir"]),
                        phase1_output=phase1_output,
                    )
                elif self._custom_op_required_signal(previous_output_map, context):
                    _ = normalized.setdefault("entry_script_kind", "custom_op_full_validation")
                    normalized["project_dir"] = str(prompt_context["project_dir"])
                variant_overlay = expanded_variant_contract_from_outputs(previous_output_map)
                if variant_overlay:
                    apply_expanded_variant_contract(normalized, variant_overlay, include_required_checks=True)
                    ensure_strict_expanded_variant_validation_script(
                        normalized,
                        variant_overlay,
                        project_dir=str(prompt_context["project_dir"]),
                    )
            normalized = self._normalize_phase3_container_paths(
                normalized, prompt_context,
            )
        if phase.prompt_id == "phase_35_static_validate":
            previous_outputs = context.get("previous_outputs", {})
            previous_output_map = cast(dict[str, object], previous_outputs) if isinstance(previous_outputs, dict) else {}
            entry_script_kind = self._lookup_previous_output(
                previous_output_map,
                "phase_3_entry_script",
                "entry_script_kind",
            )
            raw_workflow_globals: object = getattr(self.workflow, "globals", None) or {} if self.workflow else {}
            workflow_globals = cast(dict[str, object], raw_workflow_globals) if isinstance(raw_workflow_globals, dict) else {}
            if not self._custom_op_route_disabled(workflow_globals) and entry_script_kind == "custom_op_full_validation":
                normalized["custom_op_static_required"] = True
                normalized["entry_script_kind"] = "custom_op_full_validation"
                entry_script_path = self._lookup_previous_output(
                    previous_output_map,
                    "phase_3_entry_script",
                    "entry_script_path",
                )
                if isinstance(entry_script_path, str) and entry_script_path.strip():
                    _ = normalized.setdefault("entry_script_path", entry_script_path)
            if expanded_variant_contract_from_outputs(previous_output_map):
                normalized["expanded_variant_static_required"] = True
        return normalized

    @staticmethod
    def _custom_op_route_disabled(workflow_globals: dict[str, object]) -> bool:
        if workflow_globals.get("custom_op_route_enabled") is False:
            return True
        return workflow_globals.get("disable_custom_op_contract_injection") is True

    @staticmethod
    def _strip_custom_op_contract_fields(output: JsonObject) -> JsonObject:
        stripped = dict(output)
        for field in CUSTOM_OP_CONTRACT_KEYS:
            _ = stripped.pop(field, None)
        return stripped

    @staticmethod
    def _normalize_phase3_container_paths(
        output: JsonObject,
        prompt_context: dict[str, str],
    ) -> JsonObject:
        """Rewrite host-visible path fields when the model returns container paths.

        Only targets ``entry_script_path`` and ``reports_dir``.  ``run_command``
        is NOT rewritten here — the Phase 5 execution backend already handles
        host-to-container path mapping for command execution.

        When the model returns a path that starts with the container workdir (e.g.
        ``/workspace/...`` or the value of ``{container_project_dir}``), convert it
        to the corresponding host-visible path under ``{project_dir}``.
        """
        project_dir = prompt_context.get("project_dir")
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
                return False
            lowered = value.lower()
            return any(term.lower() in lowered for term in CUSTOM_OP_REQUIRED_TERMS) or None
        if isinstance(value, dict):
            value_dict = cast(dict[object, object], value)
            contract_dict = cast(dict[str, object], value)
            if has_custom_op_contract(contract_dict):
                return True
            if has_explicit_no_custom_op_contract(contract_dict):
                return False
            custom_op_surface = value_dict.get("custom_op_surface")
            if isinstance(custom_op_surface, dict):
                return cls._custom_op_signal_from_iterable(
                    item for key, item in value_dict.items() if key not in {"_meta", "custom_op_surface"}
                )
            return cls._custom_op_signal_from_iterable(
                item for key, item in value_dict.items() if key != "_meta"
            )
        if isinstance(value, list):
            return cls._custom_op_signal_from_iterable(cast(list[object], value))
        if isinstance(value, tuple):
            return cls._custom_op_signal_from_iterable(cast(tuple[object, ...], value))
        if isinstance(value, set):
            return cls._custom_op_signal_from_iterable(cast(set[object], value))
        return None

    @classmethod
    def _custom_op_signal_from_iterable(cls, values: Iterable[object]) -> bool | None:
        for item in values:
            signal = cls._custom_op_signal(item)
            if signal is not None:
                return signal
        return None

    @staticmethod
    def _lookup_previous_output(previous_outputs: object, phase_id: str, key: str) -> object | None:
        if not isinstance(previous_outputs, dict):
            return None
        outputs_dict = cast(dict[str, object], previous_outputs)
        phase_output = outputs_dict.get(phase_id)
        if isinstance(phase_output, dict):
            return cast(dict[str, object], phase_output).get(key)
        return None

    @staticmethod
    def _current_python_version() -> str:
        return f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"

    @staticmethod
    def _session_reference(session: str | InlineSessionLike) -> str:
        if isinstance(session, str):
            return session
        if isinstance(session, SessionWithIdLike) and session.session_id:
            return session.session_id
        return session.__class__.__name__

    @staticmethod
    def _build_journal_entry(
        phase: PhaseSpec,
        attempt: int,
        status: str,
        session_ref: str,
        raw_path: str,
        canonical_path: str,
        validation: ValidationResult,
    ) -> JsonObject:
        return {
            "phase_id": phase.prompt_id,
            "attempt": attempt,
            "status": status,
            "session_id": session_ref,
            "raw_path": raw_path,
            "canonical_path": canonical_path,
            "errors": validation.errors,
            "warnings": validation.warnings,
        }


__all__ = ["PhaseRunner"]
