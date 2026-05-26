"""Phase 5 error analyzer and repair loop engine."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol, TypedDict, cast

from harness.session.manager import extract_json_response

from core.artifact_store import ArtifactStore
from core.custom_op_variants import apply_expanded_variant_contract, expanded_variant_contract_from_contract
from core.execution_backend import ContainerBackend, get_execution_context as _get_exec_ctx
from core.paths import workspace_root
from core.prompt_loader import PromptLoader
from core.runtime_artifacts import write_operator_repair_context_artifact, write_repair_runtime_artifacts
from core.types import RepairContext
from core.validator_engine import ValidatorEngine
from core.platform_policy import PlatformPolicy
from validators.validate_entry_script import validate as validate_entry_script
from validators.validate_validation_final import (
    custom_op_final_gate_unit_ledger,
    validate as validate_validation_final,
    validate_custom_op_final_gate,
)

JsonDict = dict[str, object]
ConfigDict = dict[str, object]
_CUSTOM_OP_GATE_REPORT_MAX_BYTES = 5 * 1024 * 1024


class _ClassificationRequiredDict(TypedDict):
    category: str
    root_cause: str
    suggested_fix: str
    repair_role: str
    raw_response: str


class ClassificationDict(_ClassificationRequiredDict, total=False):
    entry_script_action: dict[str, object]


class FixAttemptDict(TypedDict, total=False):
    status: str
    message: str
    repair_role: str
    repair_session_id: str
    instruction: str
    response: str
    modified_files: list[str]
    fix_summary: str


class FixMetadataDict(TypedDict, total=False):
    modified_files: list[str]
    summary: str


class IterationRecord(TypedDict):
    iteration: int
    exit_code: int
    stdout: str
    stderr: str
    error: str
    classification: ClassificationDict
    fix_attempt: FixAttemptDict
    error_analyzer_session_id: str

_ANALYZER_ROLE = "error_analyzer"
_PHASE_ID = "phase_5_validation"
_REPAIR_ROLES = {"dependency_fixer", "code_adapter", "operator_fixer"}
_REPAIR_PROMPT_IDS = {
    "dependency_fixer": "repair_dependency_fixer",
    "code_adapter": "repair_code_adapter",
    "operator_fixer": "repair_operator_fixer",
}
_REPAIR_PROMPT_IDS_CONTAINER = {
    "dependency_fixer": "repair_dependency_fixer_container",
    "code_adapter": "repair_code_adapter_container",
    "operator_fixer": "repair_operator_fixer_container",
}


def _workspace_root() -> str:
    return str(workspace_root())


def _operator_generic_guidance(
    *,
    project_dir: str,
    entry_script: str,
    platform_policy: PlatformPolicy | None = None,
) -> str:
    native_label = platform_policy.guidance_native_label if platform_policy else "Ascend NPU"
    native_framework = platform_policy.guidance_native_framework if platform_policy else "torch_npu/PyTorch primitives"
    return (
        f"4. This is a generic operator-incompatibility repair. Focus on the unsupported or missing "
        f"{native_label} operator named by the runtime error, using {native_label}-native replacements, supported "
        f"{native_framework}, or local code changes. Do not add CPU fallback and do not "
        "turn this into a broader workplan.\n"
        f"5. 修改后用 {project_dir}/.venv/bin/python 和 {entry_script} 进行验证, 只在最终回答里输出一个 JSON 代码块, "
        "至少包含 modified_files, summary, agent_diagnostics。"
    )


def _operator_custom_op_target_units(phase3_contract: dict[str, object] | None) -> list[str]:
    if not isinstance(phase3_contract, dict):
        return []
    variant_overlay = expanded_variant_contract_from_contract(phase3_contract)
    units = variant_overlay.get("unit_identities")
    if isinstance(units, list):
        unit_items = cast(list[object], units)
        return [str(unit).strip() for unit in unit_items if isinstance(unit, str) and unit.strip()]
    schema_obj = phase3_contract.get("operator_inventory_schema")
    if isinstance(schema_obj, dict):
        schema = cast(dict[str, object], schema_obj)
        raw_units = schema.get("fine_grained_operator_units")
        if isinstance(raw_units, list):
            raw_unit_items = cast(list[object], raw_units)
            return [str(unit).strip() for unit in raw_unit_items if isinstance(unit, str) and unit.strip()]
    return []


def _operator_custom_op_progress_block(phase3_contract: dict[str, object] | None, project_dir: str) -> str:
    target_units = _operator_custom_op_target_units(phase3_contract)
    reports_dir = Path(project_dir).resolve() / "migration_reports"
    gate_path = reports_dir / "custom_op_final_gate.json"
    gate_data: object = {}
    if gate_path.exists() and gate_path.stat().st_size <= _CUSTOM_OP_GATE_REPORT_MAX_BYTES:
        try:
            loaded_gate = cast(object, json.loads(gate_path.read_text(encoding="utf-8")))
            gate_data = loaded_gate
        except (OSError, json.JSONDecodeError):
            gate_data = {}
    gate_map = cast(dict[str, object], gate_data) if isinstance(gate_data, dict) else {}
    ledger = custom_op_final_gate_unit_ledger(
        gate_map,
        target_units=target_units or None,
        project_root=project_dir,
    )
    lines = [
        "Current strict custom-op final-gate progress",
        f"target_units={ledger.get('total_count', 0)}",
        f"strict_pass_units={ledger.get('strict_pass_count', 0)}",
        f"remaining_units={ledger.get('remaining_count', 0)}",
    ]
    remaining = ledger.get("remaining_units")
    if isinstance(remaining, list) and remaining:
        remaining_items = cast(list[object], remaining)
        lines.append("remaining_unit_identities=" + ", ".join(str(unit) for unit in remaining_items[:50]))
    return "\n".join(lines)


def _operator_custom_op_guidance(
    operator_repair_context_artifact_path: str,
    *,
    project_dir: str,
    entry_script: str,
    platform_policy: PlatformPolicy | None = None,
) -> str:
    perf_mode = "full"
    perf_mode_note = ""
    if platform_policy is not None:
        perf_mode = platform_policy.custom_op_evidence.performance_validation
        if perf_mode == "presence_only":
            perf_mode_note = (
                f"\nPerformance validation mode: {perf_mode}. "
                "You must still provide real baseline/custom timing presence, route proof, and device proof, "
                "but speedup_vs_baseline fields are not required to be present or positive."
            )
        elif perf_mode == "disabled":
            perf_mode_note = (
                f"\nPerformance validation mode: {perf_mode}. "
                "Performance validation is skipped. All other gates still apply."
            )

    schema_checklist = "\n".join([
        "",
        "Final-gate evidence object schema (every in-scope row MUST satisfy):",
        "- opp_custom_op_artifact_evidence: object/dict with project_local=true, built/loaded booleans, project_relative_path, runtime_loaded_module_file, build_provenance={command, log_path}",
        "- adapter_evidence: object/dict with imported=true, passed=true",
        "- parity_evidence: object/dict with verified=true, passed=true",
        "- integration_e2e_evidence: object/dict with project_api_invoked=true, custom_op_route_executed=true, native_custom_op_route_executed=true",
        "- same_run_runtime_coverage: object/dict with same_run=true, custom_call_count > 0, project_api_route=true, native_custom_op_route_executed=true",
        "- performance_evidence: object/dict with baseline_seconds > 0, custom_seconds > 0, baseline_device (string), custom_device (string), project_api_invoked=true",
    ])
    if perf_mode == "full":
        schema_checklist += ", speedup_vs_baseline > 0"
    schema_checklist += (
        "\n- no_fallback_no_zero_call_no_builtin_contamination: object/dict with "
        "fallback_detected=false, zero_call_detected=false, builtin_contamination_detected=false, "
        "baseline_only_detected=false, stub_detected=false (ALL must be explicit `false`, not absent)\n"
        "Top-level: inventory_count == manifest_entries == closed_pass_entries, remaining_entries == 0, "
        "full_migration_status == FULL_PASS\n"
        "Script exit code 0 alone is NOT sufficient; the final-gate schema MUST validate."
    )

    if platform_policy is not None and platform_policy.id != "npu_ascend":
        native_label = platform_policy.guidance_native_label
        native_artifact_desc = f"real on-disk {native_label} compiled artifacts"
        native_build_desc = f"project-local build provenance/logs with {native_label} build or link evidence"
        native_path_desc = "runtime-loaded compiled artifact paths (not .py)"
        return "".join([
            f"4. Read bounded operator context: {operator_repair_context_artifact_path}; this context is the only inventory / manifest / final-gate closure source.\n",
            "5. Treat the custom-op contract as hard scope: freeze manifest rows, keep every in-scope operator, public entry, framework alias, and forward/backward/grad/training-only path in scope, and never downgrade rows or accept report-only, MVP-only, fallback, builtin, or zero-call success. If a row is unresolved, split it into smaller slices and continue the remaining rows instead of stopping.\n",
            f"6. Every in-scope row must have {native_artifact_desc}, {native_build_desc}, {native_path_desc}, adapter/import/link success, direct/reference parity, same-run runtime coverage > 0, and baseline/custom performance evidence. Evidence-only marker shims, files or libraries named *_evidence*, stub/dummy/fake placeholder native libraries, and artifacts that only export marker functions or return synthetic success codes must be reported as FAILED/INCOMPLETE rather than final success. Final success requires inventory_count == manifest_entries == closed_pass_entries, remaining_entries == 0, full_migration_status == FULL_PASS, and passing final evidence validation.",
            schema_checklist,
            perf_mode_note,
            "\n",
            f"7. 修改后用 {project_dir}/.venv/bin/python 和 {entry_script} 进行验证。只在最终回答里输出一个 JSON 代码块, ",
            "至少包含 modified_files, summary, agent_diagnostics；modified_files 必须列出实际修改文件，除非 summary 明确写 FAILED/INCOMPLETE 和外部阻塞原因。",
        ])
    return "".join([
        f"4. Read bounded operator context: {operator_repair_context_artifact_path}; this context is the only inventory / manifest / final-gate closure source.\n",
        "5. Treat the custom-op contract as hard scope: freeze manifest rows, keep every in-scope operator, public entry, framework alias, and forward/backward/grad/training-only path in scope, and never downgrade rows or accept report-only, MVP-only, fallback, builtin, or zero-call success. If a row is unresolved, split it into smaller slices and continue the remaining rows instead of stopping.\n",
        "6. Every in-scope row must have real on-disk Ascend OPP/CANN compiled artifacts, project-local build provenance/logs with ACL/CANN/AscendC/OPP build or link evidence, runtime-loaded compiled artifact paths (not .py), adapter/import/link success, direct/reference parity, same-run runtime coverage > 0, and baseline/custom performance evidence. A normal PyTorch C++ extension that only links torch_cpu/ATen operators is not an Ascend custom op even if it is copied under an ascend_custom_op path. Evidence-only marker shims, files or libraries named *_evidence*, stub/dummy/fake placeholder native libraries, and artifacts that only export marker functions or return synthetic success codes must be reported as FAILED/INCOMPLETE rather than final success. Final success requires inventory_count == manifest_entries == closed_pass_entries, remaining_entries == 0, full_migration_status == FULL_PASS, and passing final evidence validation.",
        schema_checklist,
        perf_mode_note,
        "\n",
        f"7. 修改后用 {project_dir}/.venv/bin/python 和 {entry_script} 进行验证。只在最终回答里输出一个 JSON 代码块, ",
        "至少包含 modified_files, summary, agent_diagnostics；modified_files 必须列出实际修改文件，除非 summary 明确写 FAILED/INCOMPLETE 和外部阻塞原因。",
    ])


def _operator_repair_has_custom_op_contract(phase3_contract: dict[str, object] | None) -> bool:
    return isinstance(phase3_contract, dict) and _has_custom_op_contract_fields(phase3_contract)
_STAGNATION_THRESHOLD = 3
__all__ = ["RepairLoopEngine", "SessionManagerLike", "ReviewGateState", "_get_timeout", "force_custom_op_operator_routing_if_needed"]


_CUSTOM_OP_OPERATOR_EVIDENCE_PATTERNS = (
    r"custom[-_ ]op final evidence gate failed",
    r"custom_op_final_gate",
    r"full_migration_status",
    r"closed_pass_entries",
    r"remaining_entries",
    r"opp_custom_op_artifact_evidence",
    r"same_run_runtime_coverage",
    r"custom_call_count",
    r"custom_call_count_total",
    r"zero_call_detected",
    r"builtin_contamination_detected",
    r"no_fallback_no_zero_call_no_builtin_contamination",
    r"FULL_MIGRATION_INCOMPLETE",
    r"operator evidence",
    r"custom[-_ ]op evidence",
)
_CUSTOM_OP_STRONG_OPERATOR_EVIDENCE_PATTERNS = (
    r"custom[-_ ]op final evidence gate failed",
    r"custom_op_final_gate",
    r"full_migration_status",
    r"closed_pass_entries",
    r"remaining_entries",
    r"opp_custom_op_artifact_evidence",
    r"same_run_runtime_coverage",
    r"custom_call_count",
    r"custom_call_count_total",
    r"zero_call_detected",
    r"builtin_contamination_detected",
    r"no_fallback_no_zero_call_no_builtin_contamination",
    r"FULL_MIGRATION_INCOMPLETE",
)
_CUSTOM_OP_NEGATIVE_EVIDENCE_PATTERNS = (
    r"no\s+custom[-_ ]?op(?:erator)?s?\b",
    r"custom[-_ ]?op(?:erator)?s?\s*[:=]\s*(?:none|false|no)\b",
    r"custom_op_detected\s*[:=]\s*false\b",
    r"custom[-_ ]op evidence gate (?:is )?not activated",
    r"without\s+custom[-_ ]?op(?:erator)?s?\b",
)
_CUSTOM_OP_SHARED_OBJECT_PATTERNS = (
    r"\.so\b",
    r"shared object file",
    r"ctypes\.CDLL",
)
_CUSTOM_OP_CONTEXT_PATTERNS = (
    r"custom[-_ ]op",
    r"custom_op",
    r"operator",
    r"opp",
    r"adapter",
    r"parity",
    r"runtime coverage",
    r"final gate",
    r"final[-_ ]gate",
)


def _flatten_for_routing(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except TypeError:
        return str(value)


def _has_custom_op_contract_fields(contract: dict[str, object]) -> bool:
    if contract.get("entry_script_kind") == "custom_op_full_validation":
        return True
    return any(
        field in contract
        for field in (
            "reports_dir",
            "required_report_paths",
            "required_checks",
            "operator_discovery_sources",
            "operator_inventory_schema",
            "validation_obligations",
        )
    )


def _has_custom_op_operator_evidence_signal(*, error_text: str = "", history: list[object] | None = None, classification: dict[str, object] | None = None, phase3_contract: dict[str, object] | None = None, prompt_context: dict[str, object] | None = None) -> bool:
    text_parts = [error_text, _flatten_for_routing(history or []), _flatten_for_routing(classification or {}), _flatten_for_routing(prompt_context or {})]
    combined = "\n".join(part for part in text_parts if part).lower()
    if not combined:
        return False

    has_custom_contract = False
    if isinstance(phase3_contract, dict):
        has_custom_contract = _has_custom_op_contract_fields(phase3_contract)
    elif isinstance(prompt_context, dict):
        contract_text = _flatten_for_routing(prompt_context.get("entry_script_contract", "")).lower()
        has_custom_contract = any(key in contract_text for key in ("custom_op_full_validation", "required_report_paths", "required_checks", "migration_reports"))

    if not has_custom_contract:
        return False

    has_strong_evidence = any(re.search(pattern, combined, re.IGNORECASE) for pattern in _CUSTOM_OP_STRONG_OPERATOR_EVIDENCE_PATTERNS)
    has_negative_evidence = any(re.search(pattern, combined, re.IGNORECASE) for pattern in _CUSTOM_OP_NEGATIVE_EVIDENCE_PATTERNS)
    if has_negative_evidence and not has_strong_evidence:
        return False

    if any(re.search(pattern, combined, re.IGNORECASE) for pattern in _CUSTOM_OP_OPERATOR_EVIDENCE_PATTERNS):
        return True

    has_shared_object_failure = any(re.search(pattern, combined, re.IGNORECASE) for pattern in _CUSTOM_OP_SHARED_OBJECT_PATTERNS)
    has_custom_context = any(re.search(pattern, combined, re.IGNORECASE) for pattern in _CUSTOM_OP_CONTEXT_PATTERNS)
    return has_shared_object_failure and has_custom_context


def force_custom_op_operator_routing_if_needed(classification: dict[str, object], *, error_text: str = "", history: list[object] | None = None, phase3_contract: dict[str, object] | None = None, prompt_context: dict[str, object] | None = None) -> dict[str, object]:
    if not _has_custom_op_operator_evidence_signal(
        error_text=error_text,
        history=history,
        classification=classification,
        phase3_contract=phase3_contract,
        prompt_context=prompt_context,
    ):
        return classification

    routed = dict(classification)
    routed["category"] = "operator"
    routed["repair_role"] = "operator_fixer"
    if not str(routed.get("root_cause", "")).strip():
        routed["root_cause"] = "Custom-op/operator evidence remains incomplete after Phase 5 validation"
    if not str(routed.get("suggested_fix", "")).strip():
        routed["suggested_fix"] = "Complete custom-op operator artifacts, runtime coverage, final-gate row closure, and no-fallback evidence"
    return routed



@dataclass
class ReviewGateState:
    best_passing_version: JsonDict | None = None
    review_reject_reasons: list[str] = field(default_factory=list)
    improvement_iterations: int = 0


def _get_timeout(config: ConfigDict | None, key: str, default: int | None = None) -> int | None:
    framework_config = config.get("framework") if config else None
    if isinstance(framework_config, dict):
        framework_settings = cast(ConfigDict, framework_config)
        value = framework_settings.get(key)
        if value is None:
            return None
        if isinstance(value, (int, float, str)):
            return int(value)
    return default


def get_timeout(config: ConfigDict | None, key: str, default: int | None = None) -> int | None:
    return _get_timeout(config, key, default)


class SessionManagerLike(Protocol):
    def get_or_create(self, role: str, lifecycle: str) -> str:
        ...

    def send_command(self, session_id: str, command: str, timeout: object = None) -> str:
        ...


class RepairLoopEngine:
    """Run Phase 5 execution, analysis, and targeted repair retries."""

    session_mgr: SessionManagerLike
    artifact_store: ArtifactStore
    prompt_loader: PromptLoader
    validator: ValidatorEngine
    config: ConfigDict | None
    exec_backend: object | None
    platform_policy: PlatformPolicy | None

    def __init__(
        self,
        session_mgr: SessionManagerLike,
        artifact_store: ArtifactStore,
        prompt_loader: PromptLoader,
        validator: ValidatorEngine,
        config: ConfigDict | None = None,
        exec_backend: object | None = None,
        platform_policy: PlatformPolicy | None = None,
    ) -> None:
        self.session_mgr = session_mgr
        self.artifact_store = artifact_store
        self.prompt_loader = prompt_loader
        self.validator = validator
        self.config = config
        self.exec_backend = exec_backend
        self.platform_policy = platform_policy
        self.validator.register_validator("validation_final", validate_validation_final)
        self.validator.register_validator("repair_classification", self._validate_classification)

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
            parsed_map = cast(dict[str, object], parsed)
            parsed_error = parsed_map.get("error")
            if parsed_map.get("ok") is False and parsed_error:
                return str(parsed_error)
        return None

    @staticmethod
    def _communication_error_classification(error_text: str, raw_response: str = "") -> ClassificationDict:
        return {
            "category": "communication_error",
            "root_cause": f"OpenCode session command failed: {error_text}",
            "suggested_fix": "Check OpenCode server/session state and retry after the role session is fully stopped with completed todos",
            "repair_role": "dependency_fixer",
            "raw_response": raw_response,
        }

    @staticmethod
    def _log(msg: str, logger: Callable[[str], None] | None) -> None:
        """Forward message to logger callback if provided."""
        if logger is not None:
            logger(msg)

    @staticmethod
    def _parse_env_variables(tokens: list[str]) -> tuple[dict[str, str], list[str]]:
        """Extract leading KEY=VALUE environment variable tokens from token list.

        E.g. ['WORLD_SIZE=1', 'RANK=0', 'python', 'script.py']
        → ({'WORLD_SIZE': '1', 'RANK': '0'}, ['python', 'script.py'])
        """
        env_vars: dict[str, str] = {}
        cmd_start = 0
        for i, tok in enumerate(tokens):
            m = re.match(r'^([A-Za-z_][A-Za-z0-9_]*)=(.*)$', tok)
            if m and not tok.startswith('-'):
                env_vars[m.group(1)] = m.group(2)
                cmd_start = i + 1
            else:
                break
        return env_vars, tokens[cmd_start:]

    @staticmethod
    def _safe_split_command(entry_script: str) -> list[str]:
        """Split entry command into argv for subprocess (shell=False).

        Normalises common LLM-generated patterns:
        - ``cd <dir> && <cmd>`` → strips the ``cd`` CWD prefix.
        - Properly quoted paths handled by ``shlex.split``.
        - Unquoted space-broken absolute paths re-joined heuristically.
        - Leading ``KEY=VALUE`` env-var assignments are **not** stripped;
          use ``_parse_env_variables`` for that instead.
        """
        raw = entry_script.strip()
        if not raw:
            return [raw]

        # Strip leading 'cd <path> &&' or 'cd <path> ;' shell constructs.
        cd_pattern = re.compile(r"^cd\s+(?:'[^']*'|\"[^\"]*\"|\S+)\s+(?:&&|;)\s*")
        while True:
            new_raw = cd_pattern.sub("", raw)
            if new_raw == raw:
                break
            raw = new_raw

        if not raw:
            return [entry_script.strip()]

        try:
            tokens = shlex.split(raw)
        except ValueError:
            return [entry_script.strip()]

        if len(tokens) <= 2:
            return tokens

        tokens = list(tokens)
        i = 0
        while i < len(tokens) - 1:
            token = tokens[i]
            next_tok = tokens[i + 1]
            if token.startswith("/") and not re.search(r"\.[a-zA-Z0-9]+$", token):
                base = Path(token).name
                if base not in ("python", "python3") and "/" in next_tok:
                    combined = f"{token} {next_tok}"
                    tokens = tokens[:i] + [combined] + tokens[i + 2 :]
                    continue
            i += 1

        return tokens

    @staticmethod
    def _resolve_script_cwd(entry_script: str, project_dir: str) -> str:
        """Return the CWD for executing the parsed command.

        The command argv returned by ``_safe_split_command`` is executed with
        ``shell=False``, so the CWD must be the directory where the *relative
        script path* is reachable from.

        Bug fix: when the entry script is
          ``cd /path && python test_data_and_scripts/run_inference.py``
        the argv after cd-stripping is ``[python, test_data_and_scripts/run_inference.py]``.
        Previously we returned the script's parent (``.../test_data_and_scripts``) as cwd,
        causing the subprocess to look for
        ``test_data_and_scripts/test_data_and_scripts/run_inference.py``.

        The fix: always return ``project_dir`` as cwd so that the relative path
        ``test_data_and_scripts/run_inference.py`` resolves correctly.
        """
        tokens = RepairLoopEngine._safe_split_command(entry_script)
        _, tokens = RepairLoopEngine._parse_env_variables(tokens)

        script_token = None
        for i, token in enumerate(tokens):
            if token.startswith("-"):
                flag_arg = token in ("-c", "-m")
                if not flag_arg or i + 1 >= len(tokens):
                    continue

            if token.endswith(".py") or Path(token).suffix == ".py":
                script_token = token
                break
            if "python" in Path(token).name:
                next_token = next((t for t in tokens[i + 1 :] if not t.startswith("-")), None)
                if next_token:
                    script_token = next_token
                    break

        if script_token:
            script_path = Path(script_token)
            if script_path.is_absolute():
                full_path = script_path
                if full_path.is_file():
                    return project_dir
            else:
                full_path = Path(project_dir) / script_path
                if full_path.is_file():
                    return project_dir
                # Script not at project_dir/<script_path>; search immediate subdirs.
                # E.g. script='hallo3/sample_video.py', project_dir/output has original_src/
                for subdir in sorted(Path(project_dir).iterdir()):
                    if subdir.is_dir() and not subdir.name.startswith("."):
                        candidate = subdir / script_path
                        if candidate.is_file():
                            return str(subdir)

        return project_dir

    def _read_tail(self, filepath: str, max_bytes: int = 500000) -> str:
        if max_bytes <= 0:
            return ""

        path = Path(filepath)
        if not path.is_file():
            return ""

        file_size = path.stat().st_size
        read_size = min(file_size, max_bytes)

        with path.open("rb") as handle:
            if file_size > read_size:
                _ = handle.seek(-read_size, os.SEEK_END)
            tail_bytes = handle.read(read_size)

        return tail_bytes.decode("utf-8", errors="replace")

    def run(
        self,
        entry_script: str,
        project_dir: str,
        max_iterations: int = 5,
        logger: Callable[[str], None] | None = None,
        review_callable: Callable[[dict[str, object]], dict[str, object]] | None = None,
        constraint_summary: str = "",
        env_context: dict[str, object] | None = None,
        enable_review_gate: bool = False,
        max_review_iterations: int = 3,
        phase3_contract: dict[str, object] | None = None,
    ) -> dict[str, object]:
        if max_iterations < 1:
            raise ValueError("max_iterations must be >= 1")
        if not entry_script.strip():
            raise ValueError("entry_script must be a non-empty string")

        analyzer_session_id = self.session_mgr.get_or_create(
            role=_ANALYZER_ROLE,
            lifecycle="persistent",
        )
        repair_session_ids: dict[str, str] = {}
        context = RepairContext(repair_role="", max_iterations=max_iterations)
        repeated_error_count = 0
        last_error_signature: str | None = None
        final_stdout = ""
        final_stderr = ""
        final_exit_code = 1
        status = "max_iterations"
        last_review: dict[str, object] | None = None
        gate_state = ReviewGateState()
        last_classification: ClassificationDict | None = None
        last_fix_instruction = ""
        last_fix_response = ""
        last_fix_metadata: FixMetadataDict = {}
        entry_script_revision_count = 0
        max_entry_script_revisions = self._max_entry_script_revisions()
        entry_script_revision_requests: list[dict[str, object]] = []
        active_phase3_contract = dict(phase3_contract or {})

        entry_script_timeout = _get_timeout(self.config, "entry_script_timeout")
        prepared_command = self._prepare_entry_command(entry_script, project_dir)
        script_cwd: str = prepared_command[0]
        env_vars: dict[str, str] = dict(prepared_command[1])
        cmd_argv: list[str] = list(prepared_command[2])
        use_shell: bool = bool(prepared_command[3])

        with tempfile.TemporaryDirectory(prefix="repair-loop-") as tmp_dir:
            stdout_log_path = os.path.join(tmp_dir, "out.log")
            stderr_log_path = os.path.join(tmp_dir, "err.log")

            for iteration in range(1, max_iterations + 1):
                review_result: dict[str, object] | None = None
                error_text = ""
                self._log(f"[Iter {iteration}/{max_iterations}] Running entry script...", logger)
                try:
                    run_start = time.monotonic()

                    current_script_cwd: str = str(script_cwd)
                    current_env_vars: dict[str, str] = dict(env_vars)
                    current_cmd_argv: list[str] = list(cmd_argv)
                    current_use_shell: bool = bool(use_shell)

                    if isinstance(self.exec_backend, ContainerBackend):
                        try:
                            container_command: str | list[str] = " ".join(current_cmd_argv) if current_use_shell else current_cmd_argv
                            container_env: dict[str, str] | None = current_env_vars if current_env_vars else None
                            exec_result = self.exec_backend.run(
                                command=container_command,
                                cwd=current_script_cwd,
                                env=container_env,
                                timeout=entry_script_timeout,
                            )
                            final_exit_code = exec_result.exit_code
                            final_stdout = exec_result.stdout
                            final_stderr = exec_result.stderr
                            execution_duration = exec_result.duration
                        except subprocess.TimeoutExpired:
                            final_exit_code = 124
                            final_stdout = ""
                            final_stderr = f"Execution timed out after {entry_script_timeout}s"
                            execution_duration = entry_script_timeout if entry_script_timeout else 0
                        except Exception as exc:
                            final_exit_code = 1
                            final_stdout = ""
                            final_stderr = str(exc)
                            execution_duration = round(time.monotonic() - run_start, 1)
                    else:
                        run_env = os.environ.copy()
                        run_env.update(current_env_vars)

                        with open(stdout_log_path, "w", encoding="utf-8") as stdout_handle, open(
                            stderr_log_path, "w", encoding="utf-8"
                        ) as stderr_handle:
                            if use_shell:
                                completed = subprocess.run(
                                    current_cmd_argv,
                                    stdout=stdout_handle,
                                    stderr=stderr_handle,
                                    cwd=current_script_cwd,
                                    shell=True,
                                    executable="/bin/bash",
                                    timeout=entry_script_timeout,
                                    env=run_env,
                                )
                            else:
                                completed = subprocess.run(
                                    current_cmd_argv,
                                    stdout=stdout_handle,
                                    stderr=stderr_handle,
                                    cwd=current_script_cwd,
                                    shell=False,
                                    timeout=entry_script_timeout,
                                    env=run_env,
                                )

                        execution_duration = round(time.monotonic() - run_start, 1)
                        final_stdout = self._read_tail(stdout_log_path)
                        final_stderr = self._read_tail(stderr_log_path)
                        final_exit_code = completed.returncode

                    if final_exit_code == 0:
                        gate_result = self._validate_custom_op_final_gate_for_contract(
                            active_phase3_contract, project_dir
                        )
                        if gate_result is not None and gate_result.get("passed") is not True:
                            final_exit_code = 1
                            gate_errors = gate_result.get("errors")
                            if isinstance(gate_errors, list) and gate_errors:
                                gate_error_items = cast(list[object], gate_errors)
                                concise_gate_errors = "; ".join(str(error) for error in gate_error_items[:5])
                            else:
                                concise_gate_errors = "custom-op final gate failed"
                            final_stderr = f"{final_stderr}\nCustom-op final evidence gate failed: {concise_gate_errors}".strip()
                            error_text = self._combine_error(final_stdout, final_stderr)
                            self._log(
                                f"[Iter {iteration}] Validation FAILED (custom-op final gate) - {concise_gate_errors}",
                                logger,
                            )
                        else:
                            self._log(f"[Iter {iteration}] Validation SUCCESS (exit 0)", logger)

                            review_result = None
                        if final_exit_code == 0 and enable_review_gate and review_callable is not None:
                            try:
                                classification_for_review = (
                                    cast(dict[str, object], cast(object, last_classification))
                                    if last_classification is not None
                                    else {}
                                )
                                raw_dir = str(getattr(self.artifact_store, "raw_dir", ""))
                                existing_attempts = sorted(
                                    p for p in os.listdir(raw_dir) if p.startswith("phase_5_validation_attempt") and p.endswith(".json")
                                ) if os.path.isdir(raw_dir) else []
                                last_artifact_path = (
                                    os.path.join(raw_dir, existing_attempts[-1])
                                    if existing_attempts else "(no previous attempt available)"
                                )
                                review_payload: dict[str, object] = {
                                    "iteration": iteration,
                                    "error_text": "",
                                    "classification": classification_for_review,
                                    "repair_role": context.repair_role,
                                    "fix_instruction": last_fix_instruction,
                                    "fix_response": last_fix_response,
                                    "fix_metadata": last_fix_metadata,
                                    "history": list(context.history),
                                    "last_artifact_path": last_artifact_path,
                                    "attempt_log_content": self._load_attempt_log_content(last_artifact_path),
                                    "execution_duration": str(execution_duration),
                                    "gate_state_summary": {
                                        "best_passing_version": gate_state.best_passing_version,
                                        "review_reject_reasons": list(gate_state.review_reject_reasons),
                                        "improvement_iterations": gate_state.improvement_iterations,
                                        "max_review_iterations": max_review_iterations,
                                        "history": list(context.history),
                                    },
                                }
                                raw_review_result = cast(object, review_callable(review_payload))
                                review_result = cast(dict[str, object], raw_review_result) if isinstance(raw_review_result, dict) else {"verdict": "session_error", "reasoning": "review callable returned a non-object result"}
                                self._log(
                                    f"[Iter {iteration}] Review verdict: {review_result.get('verdict', 'unknown')}",
                                    logger,
                                )
                            except Exception as e:
                                self._log(f"[Iter {iteration}] Review step failed: {e}", logger)

                        if review_result is not None:
                            typed_review_result: dict[str, object] = dict(review_result)
                            last_review = typed_review_result
                            verdict = str(typed_review_result.get("verdict", "")).lower()
                            session_error = typed_review_result.get("session_error")

                            if verdict == "session_error" or session_error:
                                reason = str(session_error or typed_review_result.get("reasoning", "Review session failed"))
                                self._log(
                                    f"[Iter {iteration}] Review gate: SESSION_ERROR - {reason}",
                                    logger,
                                )
                                context.iteration_count = iteration
                                context.last_error = f"Review gate session error: {reason}"
                                final_exit_code = 1
                                final_stderr = f"{final_stderr}\n{context.last_error}".strip()
                                status = "review_failed"
                                break

                            if verdict == "reject":
                                self._log(
                                    f"[Iter {iteration}] Review gate: REJECT (verdict '{verdict}')",
                                    logger,
                                )
                                snapshot = self._snapshot_project_files(project_dir, f"iter{iteration}")
                                gate_state.best_passing_version = {
                                    "iteration": iteration,
                                    "exit_code": final_exit_code,
                                    "modified_files": last_fix_metadata.get("modified_files", []),
                                    "fix_summary": str(last_fix_metadata.get("summary", "")),
                                    "timestamp": datetime.now(timezone.utc).isoformat(),
                                    "snapshot_path": snapshot["snapshot_path"],
                                }
                                gate_state.review_reject_reasons.append(
                                    str(review_result.get("reasoning", ""))
                                )
                                gate_state.improvement_iterations += 1

                                improvement_result = self._run_improvement_iteration(
                                    gate_state=gate_state,
                                    project_dir=project_dir,
                                    entry_script=entry_script,
                                    constraint_summary=constraint_summary,
                                    logger=logger,
                                )

                                if improvement_result.get("status") == "success":
                                    improvement_message = (
                                        f"[Iter {iteration}] Improvement applied: "
                                        f"role={improvement_result.get('repair_role', 'N/A')}, "
                                        f"area={improvement_result.get('improvement_area', 'N/A')}"
                                    )
                                    self._log(
                                        improvement_message,
                                        logger,
                                    )

                                if gate_state.improvement_iterations >= max_review_iterations:
                                    self._log(
                                        f"[Iter {iteration}] Review gate: Max iterations ({max_review_iterations}) reached, marking passed_with_reviews",
                                        logger,
                                    )
                                    context.iteration_count = iteration
                                    status = "passed_with_reviews"
                                    break
                                else:
                                    self._log(
                                        f"[Iter {iteration}] Review gate: Improvement mode activated (iteration {gate_state.improvement_iterations}/{max_review_iterations})",
                                        logger,
                                    )
                                    context.iteration_count = iteration
                                    iteration_record_exit0: IterationRecord = {
                                        "iteration": iteration,
                                        "exit_code": final_exit_code,
                                        "stdout": final_stdout,
                                        "stderr": final_stderr,
                                        "error": "",
                                        "classification": last_classification or {
                                            "category": "",
                                            "root_cause": "",
                                            "suggested_fix": "",
                                            "repair_role": context.repair_role,
                                            "raw_response": "",
                                        },
                                        "fix_attempt": {
                                            "status": "review_rejected",
                                            "repair_role": context.repair_role,
                                            "instruction": last_fix_instruction,
                                            "response": last_fix_response,
                                            "modified_files": last_fix_metadata.get("modified_files", []),
                                            "fix_summary": str(last_fix_metadata.get("summary", "")),
                                        },
                                        "error_analyzer_session_id": analyzer_session_id,
                                    }
                                    self._record_iteration(iteration, context, iteration_record_exit0)
                                    continue

                        if final_exit_code == 0:
                            context.iteration_count = iteration
                            status = "success"
                            break

                    if final_exit_code == 0:
                        pass
                    elif final_exit_code < 0:
                        error_text = (
                            f"Entry script terminated by Signal {abs(final_exit_code)}. "
                            "Likely caused by OOM or system limits."
                        )
                    else:
                        error_text = self._combine_error(final_stdout, final_stderr)
                except subprocess.TimeoutExpired:
                    final_stdout = self._read_tail(stdout_log_path)
                    final_stderr = self._read_tail(stderr_log_path)
                    final_exit_code = 1
                    error_text = (f"Execution timed out after {entry_script_timeout}s. "
                                  f"STDOUT: {final_stdout}\nSTDERR: {final_stderr}")
                except OSError as exc:
                    final_stdout = ""
                    final_stderr = str(exc)
                    final_exit_code = 1
                    error_text = f"Entry script execution failed: {exc}"
                error_signature = self._normalize_error_signature(error_text)
                self._log(
                    f"[Iter {iteration}] Validation FAILED (exit {final_exit_code}) - {error_text[:200]}",
                    logger,
                )
                repeated_error_count = repeated_error_count + 1 if error_signature == last_error_signature else 1
                last_error_signature = error_signature

                classification = self._analyze_error(
                    analyzer_session_id=analyzer_session_id,
                    entry_script=entry_script,
                    project_dir=project_dir,
                    iteration=iteration,
                    error_text=error_text,
                    history=context.history,
                    constraint_summary=constraint_summary,
                    last_review=last_review,
                    env_context=env_context or {},
                    phase3_contract=active_phase3_contract,
                    cmd_argv=list(cmd_argv),
                    use_shell=bool(use_shell),
                    script_cwd=str(script_cwd),
                    env_vars=dict(env_vars),
                )
                last_classification = classification
                self._log(
                    f"[Iter {iteration}] Analyzer classified -> category={classification.get('category')}, role={classification.get('repair_role')}",
                    logger,
                )

                fix_attempt: FixAttemptDict = {"status": "skipped"}
                action_result = self._maybe_apply_entry_script_action(
                    classification=classification,
                    active_contract=active_phase3_contract,
                    project_dir=project_dir,
                    revision_count=entry_script_revision_count,
                    max_revisions=max_entry_script_revisions,
                )
                if action_result is not None:
                    entry_script_revision_requests.append(dict(action_result))
                if action_result is not None and action_result.get("applied") is True:
                    entry_script_revision_count += 1
                    entry_script = str(action_result["run_command"])
                    active_phase3_contract["run_command"] = entry_script
                    if action_result.get("entry_script_path"):
                        active_phase3_contract["entry_script_path"] = str(action_result["entry_script_path"])
                    prepared_command = self._prepare_entry_command(entry_script, project_dir)
                    script_cwd = cast(str, prepared_command[0])
                    env_vars = dict(prepared_command[1])
                    cmd_argv = list(cast(list[str], prepared_command[2]))
                    use_shell = bool(prepared_command[3])
                    repeated_error_count = 0
                    last_error_signature = None
                    fix_attempt = {
                        "status": "entry_script_revised",
                        "message": str(action_result.get("reason", "")),
                    }
                    revision_iteration_record: IterationRecord = {
                        "iteration": iteration,
                        "exit_code": final_exit_code,
                        "stdout": final_stdout,
                        "stderr": final_stderr,
                        "error": error_text,
                        "classification": classification,
                        "fix_attempt": fix_attempt,
                        "error_analyzer_session_id": analyzer_session_id,
                    }
                    self._record_iteration(iteration, context, revision_iteration_record)
                    status = "max_iterations"
                    continue

                if repeated_error_count >= _STAGNATION_THRESHOLD:
                    status = "stagnation"
                    self._log(
                        f"[Iter {iteration}] STOP: Same error repeated {_STAGNATION_THRESHOLD}x, stagnating",
                        logger,
                    )
                    fix_attempt = {
                        "status": "stagnation",
                        "message": "Repeated identical execution error three times; escalating.",
                    }
                else:
                    repair_role = str(classification["repair_role"])
                    repair_session_id = repair_session_ids.get(repair_role)
                    if repair_session_id is None:
                        repair_session_id = self.session_mgr.get_or_create(
                            role=repair_role,
                            lifecycle="persistent",
                        )
                        repair_session_ids[repair_role] = repair_session_id
                        self._log(
                            f"[Iter {iteration}] Created new repair session {repair_session_id} (role: {repair_role})",
                            logger,
                        )
                    else:
                        self._log(
                            f"[Iter {iteration}] Reusing repair session {repair_session_id} (role: {repair_role})",
                            logger,
                        )

                    repair_prompt = self._build_repair_prompt(
                        entry_script=entry_script,
                        project_dir=project_dir,
                        iteration=iteration,
                        error_text=error_text,
                        classification=classification,
                        history=context.history,
                        constraint_summary=constraint_summary,
                        last_review=last_review,
                        env_context=env_context or {},
                        phase3_contract=active_phase3_contract,
                        cmd_argv=cmd_argv,
                        use_shell=use_shell,
                        script_cwd=script_cwd,
                        env_vars=env_vars,
                    )
                    last_fix_instruction = repair_prompt
                    repair_response: str | None = None
                    repair_failed = False
                    repair_error = ""
                    fix_metadata: FixMetadataDict = {}
                    max_retries = 2
                    retry_delays = [5, 15]

                    for attempt in range(max_retries + 1):
                        try:
                            repair_response = self.session_mgr.send_command(
                                repair_session_id,
                                repair_prompt,
                                timeout=_get_timeout(self.config, "session_timeout_repair"),
                            )
                            session_error = self._session_error_from_response(repair_response)
                            if session_error:
                                self._log(
                                    f"[Iter {iteration}] Repair LLM session error: {session_error}",
                                    logger,
                                )
                                repair_failed = True
                                repair_error = session_error
                                break
                            break
                        except TimeoutError:
                            self._log(
                                f"[Iter {iteration}] Repair LLM timed out on attempt {attempt + 1}",
                                logger,
                            )
                            if attempt < max_retries:
                                time.sleep(retry_delays[attempt])
                                continue
                            repair_failed = True
                        except (RuntimeError, ConnectionRefusedError) as e:
                            self._log(
                                f"[Iter {iteration}] Repair LLM error on attempt {attempt + 1}: {e}",
                                logger,
                            )
                            if attempt < max_retries:
                                time.sleep(retry_delays[attempt])
                                continue
                            repair_failed = True

                    if repair_failed:
                        fix_metadata = {
                            "modified_files": [],
                            "summary": repair_error or "Repair LLM call failed after retries",
                        }
                        fix_attempt = {
                            "status": "communication_error",
                            "repair_role": repair_role,
                            "repair_session_id": repair_session_id,
                            "instruction": repair_prompt,
                            "response": repair_response or "",
                            "modified_files": [],
                            "fix_summary": repair_error or "Repair LLM call failed after retries",
                        }
                    else:
                        repair_response_text = repair_response or ""
                        self._log(
                            f"[Iter {iteration}] Repair agent responded ({len(repair_response_text)} chars)",
                            logger,
                        )
                        fix_metadata = self._extract_fix_summary(
                            repair_session_id,
                            repair_response_text,
                            max_retries=2,
                        )
                        fix_attempt = {
                            "status": "sent",
                            "repair_role": repair_role,
                            "repair_session_id": repair_session_id,
                            "instruction": repair_prompt,
                            "response": repair_response_text,
                            "modified_files": fix_metadata.get("modified_files", []),
                            "fix_summary": str(fix_metadata.get("summary", "")),
                        }
                    context.repair_role = repair_role
                    last_fix_response = repair_response or ""
                    last_fix_metadata = fix_metadata

                iteration_record: IterationRecord = {
                    "iteration": iteration,
                    "exit_code": final_exit_code,
                    "stdout": final_stdout,
                    "stderr": final_stderr,
                    "error": error_text,
                    "classification": classification,
                    "fix_attempt": fix_attempt,
                    "error_analyzer_session_id": analyzer_session_id,
                }
                self._record_iteration(iteration, context, iteration_record)

                if status == "stagnation":
                    break

                status = "max_iterations"

        if status == "success":
            self._log(f"Phase 5 completed: SUCCESS (iteration {context.iteration_count})", logger)
        elif status == "stagnation":
            self._log(
                f"Phase 5 completed: STAGNATION (identical error repeated {_STAGNATION_THRESHOLD}x)",
                logger,
            )
        elif status == "passed_with_reviews":
            self._log(
                f"Phase 5 completed: PASSED_WITH_REVIEWS ({gate_state.improvement_iterations} review rejections)",
                logger,
            )
        else:
            self._log(
                f"Phase 5 completed: MAX_ITERATIONS (reached limit of {max_iterations})",
                logger,
            )

        result = self._build_result(
            status=status,
            analyzer_session_id=analyzer_session_id,
            repair_session_ids=repair_session_ids,
            context=context,
            final_stdout=final_stdout,
            final_stderr=final_stderr,
            final_exit_code=final_exit_code,
            gate_state=gate_state,
        )
        self._save_final_result(result)
        return result

    def _analyze_error(
        self,
        *,
        analyzer_session_id: str,
        entry_script: str,
        project_dir: str,
        iteration: int,
        error_text: str,
        history: list[object],
        constraint_summary: str = "",
        last_review: dict[str, object] | None = None,
        env_context: dict[str, object] | None = None,
        phase3_contract: dict[str, object] | None = None,
        cmd_argv: list[str] | None = None,
        use_shell: bool = False,
        script_cwd: str | None = None,
        env_vars: dict[str, str] | None = None,
    ) -> ClassificationDict:
        prompt_context: dict[str, str] = {
            "phase_name": _PHASE_ID,
            "project_dir": project_dir,
            "failed_phase": _PHASE_ID,
            "entry_script": entry_script,
            "iteration": str(iteration),
            "previous_outputs": self._format_error_analyzer_context(
                cast(list[dict[str, object]], history),
                error_text,
            ),
            "failure_log": error_text,
            "entry_script_contract": self._serialize(phase3_contract) if phase3_contract else "(No Phase 3 entry-script contract available)",
            "constraint_summary": constraint_summary,
            "last_review": self._serialize(last_review) if last_review else "(No review available)",
            "env_context": self._serialize(env_context) if env_context else "(No environment context available)",
            "artifact_base_path": str(getattr(self.artifact_store, "artifact_dir", "")),
            "raw_attempt_files": self._serialize(self._list_previous_attempt_paths()),
            "workspace_root": _workspace_root(),
            "phase1_phase3_repair_scope": "(Phase 1 / Phase 3 repair scope is available in the operatorRepairContext artifact when active custom-op contract is present.)",
            "strict_custom_op_acceptance_contract": "For active custom-op contracts, success requires current project-local migration reports and strict custom_op_final_gate FULL_PASS; agent text alone is not accepted.",
            "operator_repair_progress_block": "(No current custom-op repair progress block is available in this repair-loop context.)",
            "active_custom_op_full_repair_requirements": "",
        }
        exec_cmd: str | list[str] = shlex.join(cmd_argv) if (use_shell and cmd_argv) else (cmd_argv if cmd_argv else entry_script)
        prompt_context.update(_get_exec_ctx(
            getattr(self, "exec_backend", None), command=exec_cmd, cwd=script_cwd,
            env=env_vars,
        ))
        analyzer_prompt_id = "phase_error_recovery_container" if isinstance(getattr(self, "exec_backend", None), ContainerBackend) else "phase_error_recovery"
        analyzer_prompt = self.prompt_loader.load_prompt(analyzer_prompt_id, prompt_context)
        max_send_retries = 2
        retry_delays = [5, 15]
        raw_response: str | None = None

        for attempt in range(max_send_retries + 1):
            try:
                raw_response = self.session_mgr.send_command(
                    analyzer_session_id,
                    analyzer_prompt,
                    timeout=_get_timeout(self.config, "session_timeout_repair"),
                )
                session_error = self._session_error_from_response(raw_response)
                if session_error:
                    return self._communication_error_classification(session_error, raw_response)
                break
            except TimeoutError:
                if attempt < max_send_retries:
                    time.sleep(retry_delays[attempt])
                    continue
                return {
                    "category": "communication_error",
                    "root_cause": "Error analyzer LLM call timed out after retries",
                    "suggested_fix": "Check OpenCode server health",
                    "repair_role": "dependency_fixer",
                    "raw_response": "",
                }
            except (RuntimeError, ConnectionRefusedError) as e:
                if attempt < max_send_retries:
                    time.sleep(retry_delays[attempt])
                    continue
                return {
                    "category": "communication_error",
                    "root_cause": f"Error analyzer LLM connection refused: {e}",
                    "suggested_fix": "Check OpenCode server connectivity",
                    "repair_role": "dependency_fixer",
                    "raw_response": "",
                }

        max_retries = 2
        repair_role_raw = ""
        category_raw = "unknown"
        root_cause_raw = ""
        suggested_fix_raw = ""
        entry_script_action_raw: dict[str, object] | None = None

        for attempt in range(max_retries + 1):
            parsed = cast(dict[str, object], dict(extract_json_response(cast(str, raw_response))))
            repair_role_raw = str(parsed.get("repair_role", ""))
            category_raw = str(parsed.get("category", "unknown"))
            root_cause_raw = str(parsed.get("root_cause", ""))
            suggested_fix_raw = str(parsed.get("suggested_fix", ""))
            action_candidate = parsed.get("entry_script_action")
            entry_script_action_raw = cast(dict[str, object], action_candidate) if isinstance(action_candidate, dict) else None

            if repair_role_raw in _REPAIR_ROLES:
                break

            if attempt < max_retries:
                follow_up = (
                    "Your previous reply is missing a valid classification JSON at the end. "
                    "Please reply again with a JSON code block containing:\n"
                    '- `"category"`: one of [environment, dependency, pathing, migration logic, operator, unknown]\n'
                    '- `"root_cause"`: specific explanation\n'
                    '- `"suggested_fix"`: concrete corrective action\n'
                    '- `"repair_role"`: dependency_fixer, code_adapter, or operator_fixer\n'
                    "Keep your existing reasoning unchanged — just append the JSON at the end."
                )
                raw_response = self.session_mgr.send_command(
                    analyzer_session_id, follow_up,
                    timeout=_get_timeout(self.config, "session_timeout_followup"),
                )
                session_error = self._session_error_from_response(raw_response)
                if session_error:
                    return self._communication_error_classification(session_error, raw_response)

        classification_raw: dict[str, object] = {
            "category": category_raw,
            "root_cause": root_cause_raw,
            "suggested_fix": suggested_fix_raw,
            "repair_role": repair_role_raw,
            "raw_response": raw_response or "",
        }
        if entry_script_action_raw is not None:
            classification_raw["entry_script_action"] = entry_script_action_raw
        classification = cast(ClassificationDict, cast(object, force_custom_op_operator_routing_if_needed(
            classification_raw,
            error_text=error_text,
            history=history,
            phase3_contract=phase3_contract,
        )))
        validation = self.validator.validate(
            "repair_classification",
            cast(dict[str, object], cast(object, classification)),
        )
        if not validation.passed:
            raise ValueError(
                "Repair classification failed validation: "
                + "; ".join(validation.errors)
            )
        return classification

    def _max_entry_script_revisions(self) -> int:
        framework_config = self.config.get("framework") if self.config else None
        raw: object = None
        if isinstance(framework_config, dict):
            framework_map = cast(dict[str, object], framework_config)
            raw = framework_map.get("max_entry_script_revisions")
            entry_cfg = framework_map.get("entry_script")
            if raw is None and isinstance(entry_cfg, dict):
                entry_map = cast(dict[str, object], entry_cfg)
                raw = entry_map.get("max_revisions")
        if raw is None:
            return 2
        try:
            return max(0, int(str(raw)))
        except (TypeError, ValueError):
            return 2

    def _prepare_entry_command(
        self,
        entry_script: str,
        project_dir: str,
    ) -> tuple[str, dict[str, str], list[str], bool]:
        script_cwd = self._resolve_script_cwd(entry_script, project_dir)
        env_vars, cmd_argv = RepairLoopEngine._parse_env_variables(
            RepairLoopEngine._safe_split_command(entry_script)
        )
        shell_builtins = {"source", ".", "eval", "export"}
        shell_controls = {"&&", "||", ";", "|"}
        use_shell = len(cmd_argv) > 0 and (
            ".sh" in cmd_argv[0]
            or cmd_argv[0] in ("bash", "sh", "/bin/bash", "/bin/sh")
            or cmd_argv[0] in shell_builtins
            or any(tok in shell_controls for tok in cmd_argv)
        )
        if use_shell and cmd_argv and cmd_argv[0] == "bash":
            cmd_argv = ["/bin/bash"] + cmd_argv[1:]
        return script_cwd, env_vars, cmd_argv, use_shell

    def _maybe_apply_entry_script_action(
        self,
        *,
        classification: ClassificationDict,
        active_contract: dict[str, object],
        project_dir: str,
        revision_count: int,
        max_revisions: int,
    ) -> dict[str, object] | None:
        action = classification.get("entry_script_action")
        if not isinstance(action, dict):
            return None
        normalized = self._normalize_entry_script_action(action)
        if normalized["needed"] is not True:
            return {**normalized, "applied": False, "blocked_reason": "not_needed"}
        if active_contract.get("phase5_entry_script_revision_allowed") is not True:
            return {**normalized, "applied": False, "blocked_reason": "revision_not_allowed"}
        if normalized["action"] not in {"regenerate", "modify"}:
            return {**normalized, "applied": False, "blocked_reason": "invalid_action"}
        if not normalized["run_command"]:
            return {**normalized, "applied": False, "blocked_reason": "missing_run_command"}
        if revision_count >= max_revisions:
            return {**normalized, "applied": False, "blocked_reason": "max_revisions_exceeded"}
        safety_error = self._entry_script_revision_safety_error(
            str(normalized["run_command"]),
            active_contract,
            project_dir,
            str(normalized["entry_script_path"]),
        )
        if safety_error:
            return {**normalized, "applied": False, "blocked_reason": safety_error}
        return {
            **normalized,
            "applied": True,
            "revision_number": revision_count + 1,
            "max_revisions": max_revisions,
        }

    @staticmethod
    def _normalize_entry_script_action(action: dict[str, object]) -> dict[str, object]:
        needed_value = action.get("needed")
        if isinstance(needed_value, bool):
            needed = needed_value
        elif isinstance(needed_value, str):
            needed = needed_value.strip().lower() in {"true", "1", "yes"}
        else:
            needed = False
        return {
            "needed": needed,
            "action": str(action.get("action", "none") or "none").strip().lower(),
            "reason": str(action.get("reason", "") or "").strip(),
            "entry_script_path": str(action.get("entry_script_path", "") or "").strip(),
            "run_command": str(action.get("run_command", "") or "").strip(),
        }

    @staticmethod
    def _has_shell_metacharacters(run_command: str) -> bool:
        return any(control in run_command for control in ("&&", "||", ";", "|", "`", "$(", ">", "<", "\n", "\r", "&"))

    def _entry_script_revision_safety_error(
        self,
        run_command: str,
        active_contract: dict[str, object],
        project_dir: str,
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
        if tokens[0] in {"bash", "sh", "/bin/bash", "/bin/sh"} or tokens[0].endswith(".sh"):
            return "unsafe_run_command"
        updated_contract = dict(active_contract)
        updated_contract["run_command"] = run_command
        if entry_script_path:
            updated_contract["entry_script_path"] = entry_script_path
        elif not updated_contract.get("entry_script_path"):
            script_path = self._extract_entry_script_path_from_command(run_command)
            if script_path:
                updated_contract["entry_script_path"] = script_path
        if self._has_custom_op_contract(updated_contract):
            updated_contract["reports_dir"] = str(self._canonical_custom_op_reports_dir(project_dir))
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
        for token in tokens:
            if token.endswith(".py") or Path(token).suffix == ".py":
                return token
        return ""

    @staticmethod
    def _has_custom_op_contract(contract: dict[str, object]) -> bool:
        return _has_custom_op_contract_fields(contract)

    @staticmethod
    def _canonical_custom_op_reports_dir(project_dir: str) -> Path:
        return Path(project_dir).resolve() / "migration_reports"

    def _validate_custom_op_final_gate_for_contract(
        self,
        contract: dict[str, object],
        project_dir: str,
    ) -> dict[str, object] | None:
        if not self._has_custom_op_contract(contract):
            return None
        reports_dir = self._canonical_custom_op_reports_dir(project_dir)
        gate_path = reports_dir / "custom_op_final_gate.json"
        result: dict[str, object] = {
            "operation": "custom_op_final_gate",
            "skipped": False,
            "path": str(gate_path),
            "passed": False,
            "errors": [],
        }
        if not gate_path.exists():
            result["errors"] = [f"custom-op final gate report missing: {gate_path}"]
            return result
        try:
            gate_size = gate_path.stat().st_size
        except OSError as exc:
            result["errors"] = [f"custom-op final gate report could not be stat'ed: {exc}"]
            return result
        if gate_size > _CUSTOM_OP_GATE_REPORT_MAX_BYTES:
            result["errors"] = [f"custom-op final gate report too large: {gate_path}"]
            return result
        try:
            with gate_path.open("r", encoding="utf-8") as handle:
                gate_data = cast(object, json.load(handle))
        except (OSError, json.JSONDecodeError) as exc:
            result["errors"] = [f"custom-op final gate report could not be read: {exc}"]
            return result
        if not isinstance(gate_data, dict):
            result["errors"] = ["custom-op final gate report must be a JSON object"]
            return result
        gate_map = cast(dict[str, object], gate_data)
        variant_overlay = expanded_variant_contract_from_contract(contract)
        apply_expanded_variant_contract(gate_map, variant_overlay, include_required_checks=False)
        validation = validate_custom_op_final_gate(
            gate_map,
            project_root=reports_dir.parent,
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
        return result

    def _record_iteration(
        self,
        iteration: int,
        context: RepairContext,
        record: IterationRecord,
    ) -> None:
        context.iteration_count = iteration
        context.last_error = str(record["error"])

        agent_diagnostics = ""
        try:
            response_text = str(record["fix_attempt"].get("response", ""))
            if response_text:
                parsed = cast(dict[str, object], dict(extract_json_response(response_text)))
                agent_diagnostics = str(parsed.get("agent_diagnostics", ""))
        except Exception:
            agent_diagnostics = "(failed to extract diagnostics)"

        summary_entry = {
            "iteration": iteration,
            "exit_code": record["exit_code"],
            "error_category": str(record["classification"].get("category", "unknown")),
            "repair_role": str(record["fix_attempt"].get("repair_role", "")),
            "modified_files": record["fix_attempt"].get("modified_files", []),
            "fix_summary": str(record["fix_attempt"].get("fix_summary", "")),
            "agent_diagnostics": agent_diagnostics,
        }
        context.history.append(summary_entry)

        raw_path = self.artifact_store.save_phase_output(
            _PHASE_ID,
            cast(dict[str, object], cast(object, record)),
            attempt=iteration,
        )
        journal_status = "stagnation" if record["fix_attempt"].get("status") == "stagnation" else "repair_dispatched"
        _ = self.artifact_store.write_journal(
            {
                "phase_id": _PHASE_ID,
                "attempt": iteration,
                "status": journal_status,
                "session_ref": str(record["error_analyzer_session_id"]),
                "raw_path": raw_path,
                "canonical_path": "",
                "errors": [str(record["error"])],
                "warnings": [],
            }
        )
        _ = self.artifact_store.save_checkpoint(asdict(context))

    def _build_result(
        self,
        *,
        status: str,
        analyzer_session_id: str,
        repair_session_ids: dict[str, str],
        context: RepairContext,
        final_stdout: str,
        final_stderr: str,
        final_exit_code: int,
        gate_state: ReviewGateState,
    ) -> dict[str, object]:
        success = status in {"success", "passed_with_reviews"}
        errors = [] if success else ([context.last_error] if context.last_error else [])
        result: dict[str, object] = {
            "success": success,
            "status": status,
            "iteration_count": context.iteration_count,
            "errors": errors,
            "error_history": list(context.history),
            "error_analyzer_session_id": analyzer_session_id,
            "repair_session_ids": dict(repair_session_ids),
            "final_stdout": final_stdout,
            "final_stderr": final_stderr,
            "final_exit_code": final_exit_code,
        }
        if status == "passed_with_reviews" and gate_state.best_passing_version is not None:
            result["review_gate_summary"] = {
                "passing_iteration": gate_state.best_passing_version["iteration"],
                "review_rejections": len(gate_state.review_reject_reasons),
                "improvement_iterations": gate_state.improvement_iterations,
                "last_passing_version_path": gate_state.best_passing_version["snapshot_path"],
            }
        return result

    def _save_final_result(self, result: dict[str, object]) -> None:
        validation = self.validator.validate("validation_final", result)
        if not validation.passed:
            raise ValueError(
                "Repair loop result failed validation: "
                + "; ".join(validation.errors)
            )

        canonical_path = self.artifact_store.mark_validated(_PHASE_ID, result)
        iteration_count = cast(int, result["iteration_count"])
        errors = cast(list[str], result["errors"])
        _ = self.artifact_store.write_journal(
            {
                "phase_id": _PHASE_ID,
                "attempt": iteration_count,
                "status": result["status"],
                "session_ref": str(result["error_analyzer_session_id"]),
                "raw_path": "",
                "canonical_path": canonical_path,
                "errors": errors,
                "warnings": [],
            }
        )

    @staticmethod
    def _combine_error(stdout: str, stderr: str) -> str:
        chunks = [segment.strip() for segment in (stderr, stdout) if segment and segment.strip()]
        return "\n\n".join(chunks)

    @staticmethod
    def _load_attempt_log_content(attempt_path: str) -> str:
        if not attempt_path.endswith(".json"):
            return "(attempt log unavailable)"

        try:
            payload_raw = cast(object, json.loads(Path(attempt_path).read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            return "(attempt log unavailable)"

        if not isinstance(payload_raw, dict):
            return "(attempt log unavailable)"

        payload = cast(dict[str, object], payload_raw)

        sections: list[str] = []
        for key in ("stdout", "stderr", "error"):
            value = str(payload.get(key, "") or "")
            if value.strip():
                sections.append(f"{key}:\n{value}")

        return "\n\n".join(sections) if sections else "(stdout/stderr/error not available)"

    @staticmethod
    def _normalize_error_signature(error_text: str) -> str:
        return "\n".join(line.rstrip() for line in error_text.strip().splitlines())

    @staticmethod
    def _parse_last_json_block(text: str) -> dict[str, object] | None:
        """Find and parse the last JSON code block or standalone JSON object in text.

        Tries in order:
        1. Last ```json ... ``` fenced code block.
        2. Last standalone {...} object (brace-matching scan from end).

        Returns parsed dict on success, None on failure.
        """
        pattern = r"```(?:json)?\s*([\s\S]*?)\s*```"
        matches = cast(list[str], re.findall(pattern, text))
        if matches:
            for candidate in reversed(matches):
                try:
                    result = cast(object, json.loads(candidate))
                    if isinstance(result, dict):
                        return cast(dict[str, object], result)
                except json.JSONDecodeError:
                    continue

        # Fallback: brace-matching scan from end
        depth = 0
        end = -1
        start = -1
        for i in range(len(text) - 1, -1, -1):
            if text[i] == "}":
                if depth == 0:
                    end = i
                depth += 1
            elif text[i] == "{":
                depth -= 1
                if depth == 0 and end != -1:
                    start = i
                    break
        if start >= 0 and end != -1:
            try:
                result = cast(object, json.loads(text[start : end + 1]))
                if isinstance(result, dict):
                    return cast(dict[str, object], result)
            except json.JSONDecodeError:
                pass

        return None

    def _extract_fix_summary(
        self,
        repair_session_id: str,
        response: str,
        max_retries: int = 2,
    ) -> FixMetadataDict:
        """Extract a structured summary from a repair agent's response.

        Tries to parse the last JSON block in `response`.
        On failure, sends a follow-up command to the same persistent session
        requesting a properly formatted JSON summary. Retries up to
        max_retries times before falling back to defaults.
        """
        session_error = self._session_error_from_response(response)
        if session_error:
            return {"modified_files": [], "summary": f"Repair session error: {session_error}"}

        for attempt in range(max_retries + 1):
            parsed = self._parse_last_json_block(response)
            if parsed is not None:
                modified_files = parsed.get("modified_files", [])
                summary = parsed.get("summary", "")
                if isinstance(modified_files, list) and isinstance(summary, str):
                    modified_file_list = cast(list[object], modified_files)
                    if all(isinstance(path, str) for path in modified_file_list):
                        return {"modified_files": cast(list[str], modified_file_list), "summary": summary}

            if attempt < max_retries:
                follow_up = (
                    "Your previous reply is missing the required JSON summary at the end. "
                    "Please reply again with a JSON code block containing:\n"
                    '- `"modified_files"`: list of file paths you changed (relative to project dir)\n'
                    '- `"summary"`: a 1-2 sentence description of what you fixed\n'
                    "You can keep your existing text — just append the JSON at the end."
                )
                response = self.session_mgr.send_command(
                    repair_session_id, follow_up,
                    timeout=_get_timeout(self.config, "session_timeout_followup"),
                )
                session_error = self._session_error_from_response(response)
                if session_error:
                    return {"modified_files": [], "summary": f"Repair session error: {session_error}"}
            else:
                return {"modified_files": [], "summary": "Summary could not be parsed from agent response"}

        return {"modified_files": [], "summary": "Summary could not be parsed from agent response"}

    @staticmethod
    def _serialize(value: object) -> str:
        return json.dumps(value, indent=2, ensure_ascii=False, default=str)

    def _list_previous_attempt_paths(self) -> list[str]:
        import glob
        raw_dir = str(getattr(self.artifact_store, "raw_dir", ""))
        patterns = [
            glob.glob(os.path.join(raw_dir, "phase_run_entry_script_attempt*.json")),
            glob.glob(os.path.join(raw_dir, "phase_5_validation_attempt*.json")),
        ]
        return sorted(fp for pattern in patterns for fp in pattern)

    @staticmethod
    def _format_history_summary(history: list[dict[str, object]]) -> str:
        """Format repair history as a compact markdown table.

        Shows iteration number, exit code, error category, repair role,
        fix summary (truncated to 150 chars), and list of modified files.
        Returns a single-line placeholder when history is empty.
        """
        if not history:
            return "(No previous repair attempts)"

        lines = [
            "| Iter | Exit | Category | Role | Agent Diagnostics | Fix Summary | Modified Files |",
            "|------|------|----------|------|-------------------|-------------|----------------|",
        ]
        for h in history:
            files = cast(list[object], h.get("modified_files", []))
            files_str = ", ".join(str(f) for f in files) if files else "(none)"
            summary = str(h.get("fix_summary", "(no summary)"))[:500]
            diagnostics = str(h.get("agent_diagnostics", "") or "(none)")[:500]
            lines.append(
                f"| Iter {h['iteration']} | exit={h['exit_code']} | "
                + f"{str(h.get('error_category', '?'))} | {str(h.get('repair_role', '?'))} | "
                + f"{diagnostics} | {summary} | {files_str} |"
            )
        return "\n".join(lines)

    @staticmethod
    def format_history_summary(history: list[dict[str, object]]) -> str:
        return RepairLoopEngine._format_history_summary(history)

    @staticmethod
    def _format_error_analyzer_context(
        history: list[dict[str, object]], _error_text: str,
    ) -> str:
        """Compact history context for error analyzer.

        Handles BOTH schemas in context.history:
        - Full IterationRecord schema: has keys 'error', 'classification' (dict), 'fix_attempt' (dict)
        - Summary schema: has keys 'error_category', 'repair_role', 'fix_summary', 'modified_files'

        Returns a markdown table showing iteration progression plus
        an error category frequency count for trend spotting.
        """
        if not history:
            return "(No previous repair attempts — this is the first failure)"

        lines = [
            "| Iter | Exit | Category | Repair Role | Agent Diagnostics | Error Signature | Suggested Fix |",
            "|------|------|----------|-------------|-------------------|-----------------|---------------|",
        ]
        for h in history:
            iter_num = h.get("iteration", "?")
            exit_code = h.get("exit_code", "?")

            if "error_category" in h:
                # Summary schema (from Task 7)
                category = str(h.get("error_category", "unknown"))
                role = str(h.get("repair_role", "(none)"))
                summary = str(h.get("fix_summary", "(none)"))[:500]
                error_sig = category if category != "unknown" else "(recorded after improvement)"
                diagnostics = str(h.get("agent_diagnostics", "") or "(none)")[:300]
            else:
                # Full IterationRecord schema (pre-Task 7)
                classification = cast(dict[str, object], h.get("classification", {}))
                fix_attempt = cast(dict[str, object], h.get("fix_attempt", {}))
                category = str(classification.get("category", "unknown"))
                role = str(fix_attempt.get("repair_role", "(none)"))
                error_raw = str(h.get("error", ""))
                error_lines = error_raw.strip().splitlines()
                sig = error_lines[-1][:80] if error_lines else "(empty)"
                summary = str(classification.get("suggested_fix", "(none)"))[:500]
                error_sig = sig
                diagnostics = str(fix_attempt.get("agent_diagnostics", "") or "(none)")[:300]

            lines.append(
                f"| Iter {iter_num} | exit={exit_code} | {category} | {role} | "
                + f"{diagnostics} | {error_sig} | {summary} |"
            )

        categories: dict[str, int] = {}
        for h in history:
            if "error_category" in h:
                cat = str(h.get("error_category", "unknown"))
            else:
                classification = cast(dict[str, object], h.get("classification", {}))
                cat = str(classification.get("category", "unknown"))
            categories[cat] = categories.get(cat, 0) + 1
        freq = ", ".join(f"{k}: {v}" for k, v in sorted(categories.items(), key=lambda x: -x[1]))

        lines.append("")
        lines.append(f"Error category frequency: {freq}")

        return "\n".join(lines)

    def _build_repair_prompt(
        self,
        *,
        entry_script: str,
        project_dir: str,
        iteration: int,
        error_text: str,
        classification: ClassificationDict,
        history: list[object],
        constraint_summary: str = "",
        last_review: dict[str, object] | None = None,
        env_context: dict[str, object] | None = None,
        phase3_contract: dict[str, object] | None = None,
        cmd_argv: list[str] | None = None,
        use_shell: bool = False,
        script_cwd: str | None = None,
        env_vars: dict[str, str] | None = None,
    ) -> str:
        repair_role = classification["repair_role"]
        if isinstance(getattr(self, "exec_backend", None), ContainerBackend):
            prompt_id = _REPAIR_PROMPT_IDS_CONTAINER.get(repair_role, "repair_code_adapter_container")
        else:
            prompt_id = _REPAIR_PROMPT_IDS.get(repair_role, "repair_code_adapter")
        if repair_role == "operator_fixer" and _operator_repair_has_custom_op_contract(phase3_contract):
            prompt_id = "repair_custom_op_variant_service"
        context: dict[str, str] = {
            "repair_role": repair_role,
            "entry_script": entry_script,
            "project_dir": project_dir,
            "iteration": str(iteration),
            "category": classification["category"],
            "root_cause": classification["root_cause"],
            "suggested_fix": classification["suggested_fix"],
            "error_text": error_text,
            "history_summary": self._format_history_summary(cast(list[dict[str, object]], history))
            if history else "(No previous repair attempts)",
            "constraint_summary": constraint_summary,
            "last_review": self._serialize(last_review) if last_review else "(No review available)",
            "env_context": self._serialize(env_context) if env_context else "(No environment context available)",
            "artifact_base_path": str(getattr(self.artifact_store, "artifact_dir", "")),
            "raw_attempt_files": self._serialize(self._list_previous_attempt_paths()),
            "workspace_root": _workspace_root(),
            "phase1_phase3_repair_scope": "(Phase 1 / Phase 3 repair scope is available in the operatorRepairContext artifact when active custom-op contract is present.)",
            "strict_custom_op_acceptance_contract": "For active custom-op contracts, success requires current project-local migration reports and strict custom_op_final_gate FULL_PASS; agent text alone is not accepted.",
            "operator_repair_progress_block": "(No current custom-op repair progress block is available in this repair-loop context.)",
            "active_custom_op_full_repair_requirements": "",
        }
        exec_cmd: str | list[str] = shlex.join(cmd_argv) if (use_shell and cmd_argv) else (cmd_argv if cmd_argv else entry_script)
        context.update(_get_exec_ctx(
            getattr(self, "exec_backend", None), command=exec_cmd, cwd=script_cwd, env=env_vars,
        ))
        if repair_role in {"dependency_fixer", "operator_fixer"}:
            runtime_error_path, runtime_card_path = write_repair_runtime_artifacts(
                artifact_dir=str(getattr(self.artifact_store, "artifact_dir", project_dir)),
                project_dir=project_dir,
                entry_script=entry_script,
                error_text=error_text,
                category=classification["category"],
                root_cause=classification["root_cause"],
                suggested_fix=classification["suggested_fix"],
                repair_role=repair_role,
                experience_action_cards=[],
            )
            context["runtime_error_artifact_path"] = runtime_error_path
            context["runtime_card_artifact_path"] = runtime_card_path
        if repair_role == "operator_fixer":
            if _operator_repair_has_custom_op_contract(phase3_contract):
                operator_context_path = write_operator_repair_context_artifact(
                    artifact_dir=str(getattr(self.artifact_store, "artifact_dir", project_dir)),
                    project_dir=project_dir,
                    entry_script=entry_script,
                    phase3_contract=phase3_contract,
                )
                context["operator_custom_op_guidance"] = _operator_custom_op_guidance(
                    operator_context_path,
                    project_dir=project_dir,
                    entry_script=entry_script,
                    platform_policy=self.platform_policy,
                )
                context["operator_repair_progress_block"] = _operator_custom_op_progress_block(phase3_contract, project_dir)
                context["active_custom_op_full_repair_requirements"] = context["operator_custom_op_guidance"]
            else:
                context["operator_custom_op_guidance"] = _operator_generic_guidance(
                    project_dir=project_dir,
                    entry_script=entry_script,
                    platform_policy=self.platform_policy,
                )
        return self.prompt_loader.load_prompt(prompt_id, context)

    @staticmethod
    def _validate_classification(data: dict[str, object]) -> dict[str, object]:
        errors: list[str] = []
        for field_name in ("category", "root_cause", "suggested_fix", "repair_role"):
            value = data.get(field_name)
            if not isinstance(value, str) or not value.strip():
                errors.append(f"{field_name} must be a non-empty string")

        repair_role = data.get("repair_role")
        if isinstance(repair_role, str) and repair_role not in _REPAIR_ROLES:
            errors.append(
                f"repair_role must be one of {sorted(_REPAIR_ROLES)}"
            )

        return {"passed": not errors, "errors": errors, "warnings": []}

    def _snapshot_project_files(self, project_dir: str, label: str) -> dict[str, object]:
        excluded = {".sm-artifacts", ".git", "__pycache__", ".venv"}
        files: list[dict[str, str]] = []
        for root, _dirs, filenames in os.walk(project_dir):
            if any(part in excluded for part in Path(root).relative_to(project_dir).parts):
                continue
            for fn in filenames:
                if not fn.endswith(".py"):
                    continue
                fp = os.path.join(root, fn)
                try:
                    content = Path(fp).read_bytes()
                    files.append({
                        "path": str(Path(fp).relative_to(project_dir)),
                        "sha256": hashlib.sha256(content).hexdigest(),
                        "size": str(len(content)),
                    })
                except OSError:
                    pass
        snapshot: dict[str, object] = {
            "file_count": len(files),
            "files": files,
            "label": label,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        snapshot_path = os.path.join(
            os.path.join(project_dir, ".sm-artifacts"),
            f"passing_version_{label}.json",
        )
        os.makedirs(os.path.dirname(snapshot_path), exist_ok=True)
        _ = Path(snapshot_path).write_text(
            json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8",
        )
        snapshot["snapshot_path"] = snapshot_path
        return snapshot

    def _run_improvement_iteration(
        self,
        *,
        gate_state: ReviewGateState,
        project_dir: str,
        entry_script: str,
        constraint_summary: str,
        logger: Callable[[str], None] | None = None,
    ) -> dict[str, object]:
        history_lines = gate_state.review_reject_reasons
        review_json = {
            "verdict": "reject",
            "reasoning": history_lines[-1] if history_lines else "",
            "cpu_fallback_detected": True,
        }
        prompt_context = {
            "phase_name": _PHASE_ID,
            "project_dir": project_dir,
            "entry_script": entry_script,
            "last_review_json": self._serialize(review_json),
            "constraint_summary": constraint_summary,
            "improvement_history": "\n".join(f"- {r}" for r in history_lines) if history_lines else "(none)",
        }
        prompt_context.update(_get_exec_ctx(
            getattr(self, "exec_backend", None), command=shlex.split(entry_script),
        ))
        imp_prompt_id = "phase_review_improvement_container" if isinstance(getattr(self, "exec_backend", None), ContainerBackend) else "phase_review_improvement"
        imp_prompt = self.prompt_loader.load_prompt(imp_prompt_id, prompt_context)
        analyzer_session_id = self.session_mgr.get_or_create("error_analyzer", "persistent")
        try:
            raw = self.session_mgr.send_command(
                analyzer_session_id,
                imp_prompt,
                timeout=_get_timeout(self.config, "session_timeout_analyzer"),
            )
        except (TimeoutError, RuntimeError, ConnectionRefusedError):
            return {"status": "improvement_failed"}
        session_error = self._session_error_from_response(raw)
        if session_error:
            return {"status": "improvement_failed", "error": session_error}
        parsed = self._parse_last_json_block(raw) or {}
        repair_role = str(parsed.get("repair_role", "code_adapter"))
        if repair_role not in _REPAIR_ROLES:
            repair_role = "code_adapter"
        improvement_area = str(parsed.get("improvement_area", ""))
        suggested_direction = str(parsed.get("suggested_direction", ""))

        improvement_instruction = (
            f"Previous repair attempts were reviewed and rejected.\n"
            f"**Improvement Area**: {improvement_area}\n"
            f"**Suggested Direction**: {suggested_direction}\n"
            f"\n"
            f"Please execute the necessary modifications to address this improvement.\n"
            f"Project directory: {project_dir}\n"
            f"\n"
        )
        imp_exec_ctx = _get_exec_ctx(
            getattr(self, "exec_backend", None), command=shlex.split(entry_script),
        )
        if imp_exec_ctx.get("execution_backend_mode") == "container":
            improvement_instruction += (
                f"**Container Execution Context**:\n"
                f"- Execution mode: {imp_exec_ctx['execution_backend_mode']}\n"
                f"- Actual execution command: {imp_exec_ctx['actual_execution_command']}\n"
                f"- Container: {imp_exec_ctx['container_name_or_id']}\n"
                f"\n"
                f"When validating manually, use `actual_execution_command` — do NOT run "
                f"`{entry_script}` directly on the host.\n"
                f"\n"
            )
        improvement_instruction += (
            f"End your response with a JSON code block:\n"
            f'```{ "json" }\n'
            f'{{"modified_files": [...], "summary": "..."}}\n'
            f"```"
        )
        repair_session_id = self.session_mgr.get_or_create(
            role=repair_role,
            lifecycle="persistent",
        )
        try:
            repair_response = self.session_mgr.send_command(
                repair_session_id,
                improvement_instruction,
                timeout=_get_timeout(self.config, "session_timeout_repair"),
            )
        except (TimeoutError, RuntimeError, ConnectionRefusedError):
            self._log(
                f"[Improvement] Repair LLM call failed for role={repair_role}",
                logger,
            )
            return {
                "status": "improvement_repair_failed",
                "repair_role": repair_role,
                "improvement_area": improvement_area,
                "suggested_direction": suggested_direction,
            }
        session_error = self._session_error_from_response(repair_response)
        if session_error:
            self._log(
                f"[Improvement] Repair LLM session error for role={repair_role}: {session_error}",
                logger,
            )
            return {
                "status": "improvement_repair_failed",
                "repair_role": repair_role,
                "repair_session_id": repair_session_id,
                "improvement_area": improvement_area,
                "suggested_direction": suggested_direction,
                "error": session_error,
            }
        fix_metadata = self._extract_fix_summary(repair_session_id, repair_response, max_retries=2)
        improvement_log = (
            f"[Improvement] Repair agent ({repair_role}) responded "
            f"({len(repair_response)} chars), modified: {fix_metadata.get('modified_files', [])}"
        )
        self._log(
            improvement_log,
            logger,
        )
        return {
            "status": "success",
            "repair_role": repair_role,
            "repair_session_id": repair_session_id,
            "improvement_area": improvement_area,
            "suggested_direction": suggested_direction,
            "modified_files": fix_metadata.get("modified_files", []),
            "fix_summary": str(fix_metadata.get("summary", "")),
        }
