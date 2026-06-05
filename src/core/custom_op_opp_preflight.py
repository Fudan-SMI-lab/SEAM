"""Pre-run checks for custom-op migrations that must produce native artifacts."""

from __future__ import annotations

import os
import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from core.platform_policy import (
    PlatformPolicy,
    get_artifact_path_tokens,
    get_native_build_log_tokens,
    get_native_source_tokens,
)

_CUSTOM_OP_STRUCTURAL_CONTRACT_FIELDS = frozenset({
    "operator_discovery_sources",
    "operator_inventory_schema",
    "validation_obligations",
    "expanded_variant_inventory",
    "variant_axis_coverage",
    "per_variant_performance_report",
})
_CUSTOM_OP_REPORT_TOKENS = frozenset({
    "custom_op_final_gate",
    "operator_inventory",
    "opp_custom_op",
})
_CUSTOM_OP_CHECK_TOKENS = frozenset({
    "custom_op",
    "custom-op",
    "opp",
    "op_host",
    "op_kernel",
    "native_operator_symbol",
})
_CUSTOM_OP_NEGATIVE_BOOL_FIELDS = frozenset({
    "custom_op_detected",
    "custom_op_required",
    "custom_op_static_required",
    "native_custom_op_required",
})
_CUSTOM_OP_ZERO_COUNT_FIELDS = frozenset({
    "operator_unit_count",
    "inventory_count",
    "manifest_entries",
})
_CUSTOM_OP_POSITIVE_COUNT_FIELDS = frozenset({
    "operator_unit_count",
    "inventory_count",
    "manifest_entries",
    "closed_pass_entries",
    "remaining_entries",
})
_CUSTOM_OP_POSITIVE_LIST_FIELDS = frozenset({
    "operators",
    "rows",
})
_FALSE_STRINGS = frozenset({"false", "0", "no", "none", "null", "not_applicable", "not-applicable"})
_TRUE_STRINGS = frozenset({"true", "1", "yes"})
_SKIP_DIR_NAMES = frozenset({
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "node_modules",
})
_TEXT_SUFFIXES = frozenset({
    "",
    ".c",
    ".cc",
    ".cpp",
    ".cu",
    ".cuh",
    ".h",
    ".hpp",
    ".hh",
    ".py",
    ".sh",
    ".cmake",
    ".txt",
    ".json",
    ".yaml",
    ".yml",
    ".md",
    ".log",
})
_SOURCE_SUFFIXES = frozenset({".c", ".cc", ".cpp", ".cu", ".cuh", ".h", ".hpp", ".hh"})
_MAX_SCANNED_FILES = 20_000
_MAX_TEXT_SCAN_BYTES = 256 * 1024
_MAX_EVIDENCE_PER_KIND = 20


def has_custom_op_contract(contract: Mapping[str, object]) -> bool:
    if _has_positive_custom_op_inventory(contract):
        return True
    if has_explicit_no_custom_op_contract(contract):
        return False
    if contract.get("entry_script_kind") == "custom_op_full_validation":
        return True
    if any(field in contract for field in _CUSTOM_OP_STRUCTURAL_CONTRACT_FIELDS):
        return True
    required_report_paths = contract.get("required_report_paths")
    if _value_contains_token(required_report_paths, _CUSTOM_OP_REPORT_TOKENS):
        return True
    required_checks = contract.get("required_checks")
    return _value_contains_token(required_checks, _CUSTOM_OP_CHECK_TOKENS)


def has_explicit_no_custom_op_contract(contract: Mapping[str, object]) -> bool:
    if _has_positive_custom_op_inventory(contract):
        return False

    for field in _CUSTOM_OP_NEGATIVE_BOOL_FIELDS:
        if field in contract and _is_false_value(contract.get(field)):
            return True
    for field in _CUSTOM_OP_ZERO_COUNT_FIELDS:
        if field in contract and _is_zero_value(contract.get(field)):
            return True

    custom_op_surface = contract.get("custom_op_surface")
    if isinstance(custom_op_surface, Mapping):
        surface = cast(Mapping[str, object], custom_op_surface)
        if _has_positive_custom_op_inventory(surface):
            return False
        if has_explicit_no_custom_op_contract(surface):
            return True

    source_inventory = contract.get("source_inventory")
    if isinstance(source_inventory, Mapping):
        inventory = cast(Mapping[str, object], source_inventory)
        entries = inventory.get("entries")
        if isinstance(entries, list) and entries:
            return False
        if isinstance(entries, list) and not entries:
            return True

    return False


def _has_positive_custom_op_inventory(contract: Mapping[str, object]) -> bool:
    for field in _CUSTOM_OP_NEGATIVE_BOOL_FIELDS:
        if field in contract and _is_true_value(contract.get(field)):
            return True
    for field in _CUSTOM_OP_POSITIVE_COUNT_FIELDS:
        count = _coerce_int(contract.get(field))
        if count is not None and count > 0:
            return True
    for field in _CUSTOM_OP_POSITIVE_LIST_FIELDS:
        value = contract.get(field)
        if isinstance(value, list) and value:
            return True

    custom_op_surface = contract.get("custom_op_surface")
    if isinstance(custom_op_surface, Mapping):
        surface = cast(Mapping[str, object], custom_op_surface)
        if _has_positive_custom_op_inventory(surface):
            return True
        for field in ("fine_grained_operator_units", "operator_units", "native_operator_symbols"):
            value = surface.get(field)
            if isinstance(value, list) and value:
                return True

    source_inventory = contract.get("source_inventory")
    if isinstance(source_inventory, Mapping):
        inventory = cast(Mapping[str, object], source_inventory)
        entries = inventory.get("entries")
        if isinstance(entries, list) and entries:
            return True

    return False


def _is_false_value(value: object) -> bool:
    if isinstance(value, bool):
        return value is False
    if isinstance(value, str):
        return value.strip().lower() in _FALSE_STRINGS
    return False


def _is_true_value(value: object) -> bool:
    if isinstance(value, bool):
        return value is True
    if isinstance(value, str):
        return value.strip().lower() in _TRUE_STRINGS
    return False


def _is_zero_value(value: object) -> bool:
    count = _coerce_int(value)
    return count == 0


def _coerce_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        stripped = value.strip()
        unsigned = stripped.lstrip("+-")
        if unsigned and unsigned.isdigit():
            try:
                return int(stripped)
            except ValueError:
                return None
    return None


def _value_contains_token(value: object, tokens: frozenset[str]) -> bool:
    if isinstance(value, str):
        normalized = value.lower()
        return any(token in normalized for token in tokens)
    if isinstance(value, Mapping):
        return any(_value_contains_token(item, tokens) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_value_contains_token(item, tokens) for item in cast(list[object] | tuple[object, ...] | set[object], value))
    return False


def validate_custom_op_opp_preflight(
    contract: Mapping[str, object],
    project_dir: str | Path,
    platform_policy: PlatformPolicy,
) -> dict[str, object] | None:
    """Fail closed when a custom-op contract has no concrete native producer evidence."""
    if not has_custom_op_contract(contract):
        return None

    project_root = Path(project_dir).resolve()
    result: dict[str, object] = {
        "operation": "custom_op_opp_preflight",
        "skipped": False,
        "passed": False,
        "project_root": str(project_root),
        "policy": platform_policy.custom_op_evidence.custom_op_evidence_policy,
        "platform": platform_policy.id,
        "platform_display_name": platform_policy.display_name,
        "errors": [],
        "evidence": {},
        "stale_route_signals": [],
    }

    if not project_root.exists() or not project_root.is_dir():
        result["errors"] = [f"project root does not exist: {project_root}"]
        return result

    evidence = _scan_opp_evidence(project_root, platform_policy)
    stale_route_signals = evidence.pop("stale_route_signals")
    result["evidence"] = evidence
    result["stale_route_signals"] = stale_route_signals

    missing: list[str] = []
    if not evidence["native_sources"]:
        missing.append("native source evidence")
    if not (
        evidence["native_build_scripts"]
        or evidence["generated_native_artifacts"]
        or evidence["build_install_evidence"]
    ):
        missing.append("native build, generated artifact, or install evidence")

    errors: list[str] = []
    if missing:
        errors.append(
            "missing "
            + ", ".join(missing)
            + f"; {platform_policy.guidance_native_label} is the configured custom-op target"
        )
    if missing and stale_route_signals:
        sample = ", ".join(stale_route_signals[:5])
        errors.append(
            f"extension-only custom-op route detected without separate strict {platform_policy.guidance_native_label} producer evidence: {sample}"
        )

    result["errors"] = errors
    result["passed"] = not errors
    return result


def format_custom_op_opp_preflight_failure(result: Mapping[str, object]) -> str:
    errors = result.get("errors")
    if isinstance(errors, list) and errors:
        error_items = cast(list[object], errors)
        return "Custom-op OPP preflight failed: " + "; ".join(str(error) for error in error_items[:5])
    return "Custom-op OPP preflight failed"


def _scan_opp_evidence(project_root: Path, platform_policy: PlatformPolicy) -> dict[str, list[str]]:
    evidence: dict[str, list[str]] = {
        "native_sources": [],
        "native_build_scripts": [],
        "generated_native_artifacts": [],
        "build_install_evidence": [],
        "runtime_native_artifacts": [],
        "stale_route_signals": [],
        # Compatibility aliases for existing report consumers.
        "op_host_sources": [],
        "op_kernel_sources": [],
        "opp_build_scripts": [],
        "generated_opp_artifacts": [],
    }

    scanned = 0
    for path in project_root.rglob("*"):
        if scanned >= _MAX_SCANNED_FILES:
            break
        rel_path = _relative_path(project_root, path)
        if _should_skip(rel_path):
            continue
        if path.is_dir():
            continue
        safe_path = _safe_regular_project_file(project_root, path)
        if safe_path is None:
            continue
        scanned += 1
        _collect_path_evidence(safe_path, rel_path, evidence, platform_policy)
    return evidence


def _collect_path_evidence(
    path: Path,
    rel_path: str,
    evidence: dict[str, list[str]],
    platform_policy: PlatformPolicy,
) -> None:
    rel_lower = rel_path.replace("\\", "/").lower()
    name_lower = path.name.lower()
    suffix = path.suffix.lower()
    text = _read_small_text(path)
    scan_text = f"{rel_lower}\n{text.lower()}"

    source_tokens = tuple(token.lower() for token in get_native_source_tokens(platform_policy))
    build_tokens = tuple(token.lower() for token in get_native_build_log_tokens(platform_policy))
    artifact_tokens = tuple(token.lower() for token in get_artifact_path_tokens(platform_policy))

    if suffix in _SOURCE_SUFFIXES and _has_any_token(scan_text, source_tokens):
        _append_evidence(evidence, "native_sources", rel_path)
        if "op_host" in rel_lower:
            _append_evidence(evidence, "op_host_sources", rel_path)
        if "op_kernel" in rel_lower:
            _append_evidence(evidence, "op_kernel_sources", rel_path)
    if name_lower in {"build.sh", "cmakelists.txt"} and _has_any_token(scan_text, build_tokens):
        _append_evidence(evidence, "native_build_scripts", rel_path)
        _append_evidence(evidence, "opp_build_scripts", rel_path)
    if _has_any_token(rel_lower, artifact_tokens):
        _append_evidence(evidence, "generated_native_artifacts", rel_path)
        _append_evidence(evidence, "generated_opp_artifacts", rel_path)
    if suffix in {".so", ".o", ".run"} and _has_any_token(rel_lower, artifact_tokens):
        _append_evidence(evidence, "runtime_native_artifacts", rel_path)
    if _has_policy_build_install_signal(name_lower, scan_text, build_tokens, artifact_tokens):
        _append_evidence(evidence, "build_install_evidence", rel_path)
    for signal in _stale_route_signals(scan_text, platform_policy):
        _append_evidence(evidence, "stale_route_signals", f"{rel_path}:{signal}")


def _read_small_text(path: Path) -> str:
    if path.suffix.lower() not in _TEXT_SUFFIXES:
        return ""
    try:
        if path.stat().st_size > _MAX_TEXT_SCAN_BYTES:
            return ""
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _safe_regular_project_file(project_root: Path, path: Path) -> Path | None:
    try:
        if path.is_symlink():
            return None
        resolved_root = project_root.resolve(strict=True)
        resolved_path = path.resolve(strict=True)
        if not resolved_path.is_relative_to(resolved_root):
            return None
        if not resolved_path.is_file():
            return None
        return resolved_path
    except OSError:
        return None


def _has_any_token(value: str, tokens: tuple[str, ...]) -> bool:
    return any(token and token in value for token in tokens)


def _has_policy_build_install_signal(
    name_lower: str,
    scan_text: str,
    build_tokens: tuple[str, ...],
    artifact_tokens: tuple[str, ...],
) -> bool:
    if not any(token in name_lower for token in ("build", "install", "package", "deploy")):
        return False
    return _has_any_token(scan_text, build_tokens) or _has_any_token(scan_text, artifact_tokens)


def _stale_route_signals(scan_text: str, platform_policy: PlatformPolicy) -> list[str]:
    signals: list[str] = []
    signal_terms = {
        "CppExtension": "cppextension",
        "cpp_extension": "cpp_extension",
        "torch.utils.cpp_extension": "torch.utils.cpp_extension",
        "ATen": "aten",
        "torch/extension.h": "torch/extension.h",
        "libtorch": "libtorch",
        "torch_cpu": "torch_cpu",
    }
    for target in platform_policy.custom_op_evidence.target_device_values:
        normalized = target.strip().lower().replace("-", "_").replace(".", "_")
        if not normalized:
            continue
        signal_terms[f"{normalized}_extension"] = f"{normalized}_extension"
        signal_terms[f"{normalized}Extension"] = f"{normalized}extension"
        signal_terms[f"{normalized}_ops.cpp"] = f"{normalized}_ops.cpp"
        if not normalized.startswith("torch_"):
            signal_terms[f"torch_{normalized}.utils.cpp_extension"] = f"torch_{normalized}.utils.cpp_extension"
    for label, token in signal_terms.items():
        if token in scan_text:
            signals.append(label)
    return signals


def _relative_path(project_root: Path, path: Path) -> str:
    try:
        return path.relative_to(project_root).as_posix()
    except ValueError:
        return path.as_posix()


def _should_skip(rel_path: str) -> bool:
    parts = rel_path.replace("\\", "/").split("/")
    return any(part in _SKIP_DIR_NAMES for part in parts)


_LEGACY_OPP_TEMPLATE_REL_PATH = (
    "cuda-custom-op-to-npu-custom-op/templates/ascend_custom_op"
)
"""Legacy Fallback OPP template path; retained for backward compatibility.
Overridable via ``SEAM_OPP_TEMPLATE_REL_PATH`` env var or policy attribute."""


def _resolve_opp_template_rel_path(policy: object | None) -> str:
    """Resolve the OPP template relative path from policy, env var, or fallback.

    Resolution order:
    1. ``policy.opp_template_rel_path`` (when policy provides one)
    2. ``SEAM_OPP_TEMPLATE_REL_PATH`` environment variable
    3. Legacy hardcoded path (Ascend-specific, for backward compat)
    """
    if policy is not None:
        from_policy = getattr(policy, "opp_template_rel_path", None)
        if from_policy:
            return from_policy
    return os.environ.get(
        "SEAM_OPP_TEMPLATE_REL_PATH",
        _LEGACY_OPP_TEMPLATE_REL_PATH,
    )


def ensure_opp_source_evidence(
    project_root: Path,
    platform_policy: object | None = None,
) -> bool:
    """Scaffold OPP source evidence into project_root from .skills/ templates if missing.

    Args:
        project_root: Target project directory to scaffold evidence into.
        platform_policy: ``PlatformPolicy`` for the active platform.
            When ``None``, checks the ``SEAM_DEFAULT_POLICY_PRESET`` env var.
            If that env var is set to a known preset name, that preset is used;
            otherwise the ``generic_accelerator`` preset is used as default.
    """
    from core.platform_policy import BUILTIN_PRESETS

    _default_preset = os.environ.get("SEAM_DEFAULT_POLICY_PRESET")
    if platform_policy is not None:
        policy = platform_policy
    elif _default_preset:
        policy = BUILTIN_PRESETS.get(_default_preset)
        if policy is None:
            # Unknown preset via env var: fall back to generic.
            policy = BUILTIN_PRESETS["generic_accelerator"]
    else:
        policy = BUILTIN_PRESETS["generic_accelerator"]
    existing = _scan_opp_evidence(project_root, policy)
    has_host = bool(existing["op_host_sources"])
    has_kernel = bool(existing["op_kernel_sources"])
    has_build = bool(existing["opp_build_scripts"])
    if has_host and has_kernel and has_build:
        return True

    skills_dir: Path | None = None
    for parent in project_root.parents:
        candidate = parent / ".skills" / _resolve_opp_template_rel_path(policy)
        if candidate.is_dir():
            skills_dir = candidate
            break

    if skills_dir is None:
        return False

    for item_name in ("op_host", "op_kernel", "build.sh", "CMakeLists.txt", "CMakePresets.json"):
        src = skills_dir / item_name
        dst = project_root / item_name
        if src.exists() and not dst.exists():
            try:
                if src.is_dir():
                    _ = shutil.copytree(src, dst)
                else:
                    _ = shutil.copy2(src, dst)
            except OSError:
                pass

    final = _scan_opp_evidence(project_root, policy)
    return bool(final["op_host_sources"]) and bool(final["op_kernel_sources"]) and bool(final["opp_build_scripts"])


def _append_evidence(evidence: dict[str, list[str]], key: str, value: str) -> None:
    values = evidence[key]
    if value in values or len(values) >= _MAX_EVIDENCE_PER_KIND:
        return
    values.append(value)
