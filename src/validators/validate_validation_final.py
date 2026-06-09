"""Validation for Phase 5 final validation output."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
import fnmatch
import re
from typing import cast

from core.custom_op_variants import _OPP_HOST_DIRNAMES, _OPP_KERNEL_DIRNAMES
from core.routes import DEFAULT_PROMPT_FALLBACK_SUFFIXES, is_serving_route, serving_framework_for_route
from core.validator_engine import ValidationDict
from core.platform_policy import (
    PlatformPolicy,
    _GENERIC_EVIDENCE,
    get_artifact_path_tokens,
    get_native_build_log_tokens,
    get_native_source_tokens,
    get_native_binary_tokens,
    get_target_device_values,
    get_positive_boolean_fields,
    get_performance_validation_mode,
    get_performance_baseline_device_values,
    get_performance_baseline_boolean_fields,
)

from validators.serving_validator import validate_serving_final_gate as _validate_serving_final_gate

PASS_STATES = {"PASS", "FULL_PASS", "DONE", "CLOSED_PASS"}
EVIDENCE_PASS_STATES = PASS_STATES | {"PASSED", "SUCCESS", "OK", "VERIFIED"}

_OPP_GENERATED_KERNEL_PATH_PATTERN = os.environ.get(
    "SEAM_OPP_GENERATED_KERNEL_PATH_PATTERN",
    "/op_impl/ai_core/tbe/kernel/",
)
_GENERATED_OPP_API_PREFIX = os.environ.get(
    "SEAM_GENERATED_OPP_API_PREFIX", "aclnn_"
)
_OPP_TBE_CONFIG_PATH_PATTERN = os.environ.get(
    "SEAM_OPP_TBE_CONFIG_PATH_PATTERN", "/tbe/config/"
)
_OPP_INFO_CFG_PATH_PATTERN = os.environ.get(
    "SEAM_OPP_INFO_CFG_PATH_PATTERN", "/op_info_cfg/"
)

_SEAM_OPP_GENERATED_ARTIFACT_TOKENS = tuple(
    t.strip() for t in os.environ.get(
        "SEAM_OPP_GENERATED_ARTIFACT_TOKENS",
        "/op_info/,op_info.json,/kernel_meta/,kernel_meta,/vendors/,/packages/,.run,.opp",
    ).split(",") if t.strip()
)
"""Comma-separated tokens identifying generated OPP artifacts.
Override via SEAM_OPP_GENERATED_ARTIFACT_TOKENS."""

_SEAM_OPP_INVENTORY_PATH_TOKENS = tuple(
    t.strip() for t in os.environ.get(
        "SEAM_OPP_INVENTORY_PATH_TOKENS",
        "/op_api/include/,/autogen/",
    ).split(",") if t.strip()
)
"""Comma-separated path tokens identifying generated OPP inventory entries.
Override via SEAM_OPP_INVENTORY_PATH_TOKENS."""

_SEAM_OPP_CONFIG_CONTAINER_TOKENS = tuple(
    t.strip() for t in os.environ.get(
        "SEAM_OPP_CONFIG_CONTAINER_TOKENS",
        "/kernel/config/,/op_proto/",
    ).split(",") if t.strip()
)
"""Comma-separated path tokens identifying OPP config/container paths.
Override via SEAM_OPP_CONFIG_CONTAINER_TOKENS."""

_SEAM_OPP_ARTIFACT_CATEGORY_TOKENS = tuple(
    t.strip() for t in os.environ.get(
        "SEAM_OPP_ARTIFACT_CATEGORY_TOKENS",
        "/autogen/,/generated/,/include/,/op_info/,/kernel_meta/",
    ).split(",") if t.strip()
)
"""Comma-separated path tokens for generated OPP artifact category matching.
Override via SEAM_OPP_ARTIFACT_CATEGORY_TOKENS."""

_SEAM_OPP_BUILD_SCRIPT_NAMES = tuple(
    t.strip() for t in os.environ.get(
        "SEAM_OPP_BUILD_SCRIPT_NAMES",
        "cmakelists.txt,build.sh,build_opp.sh,build_custom_op.sh",
    ).split(",") if t.strip()
)
"""Comma-separated basenames identifying OPP build scripts.
Override via SEAM_OPP_BUILD_SCRIPT_NAMES."""

_SEAM_OPP_BUILD_SCRIPT_EXTENSIONS = tuple(
    t.strip() for t in os.environ.get(
        "SEAM_OPP_BUILD_SCRIPT_EXTENSIONS",
        ".cmake,.sh",
    ).split(",") if t.strip()
)
"""Comma-separated extensions identifying OPP build scripts.
Override via SEAM_OPP_BUILD_SCRIPT_EXTENSIONS."""

_SEAM_OPP_STRICT_BUILD_LOG_TOKENS = tuple(
    t.strip() for t in os.environ.get(
        "SEAM_OPP_STRICT_BUILD_LOG_TOKENS",
        "cann,opp,msopgen,opc,tikcpp,ascendc,kernel_operator.h,-lascendcl",
    ).split(",") if t.strip()
)
"""Comma-separated tokens used as fallback for strict OPP build-log detection.
Override via SEAM_OPP_STRICT_BUILD_LOG_TOKENS."""

"""Subpath pattern identifying platform-generated OPP kernel artifacts.
Override via SEAM_OPP_GENERATED_KERNEL_PATH_PATTERN (e.g. ``/ppu_op/ppu_core/ppu_kernel/``)."""

_OPP_UNIT_SUFFIX_PATTERN = re.compile(
    r"_(?:" + "|".join(
        re.escape(s.removeprefix("_")) for s in DEFAULT_PROMPT_FALLBACK_SUFFIXES
    ) + r")$"
)

__all__ = ["validate_custom_op_final_gate", "_path_has_platform_artifact_signal"]

BLOCKING_STATUSES = {
    "MVP_ONLY",
    "SMOKE_ONLY",
    "PARTIAL",
    "DIRECT_ONLY",
    "ARTIFACT_ONLY",
    "INCOMPLETE",
    "FAILED",
    "BLOCKED",
    "HARDWARE_LIMITATION_ACCEPTED",
    "TODO",
    "FOLLOW_UP",
    "FUTURE_WORK",
}

REQUIRED_ROW_EVIDENCE_FIELDS = (
    "opp_custom_op_artifact_evidence",
    "adapter_evidence",
    "parity_evidence",
    "integration_e2e_evidence",
    "same_run_runtime_coverage",
    "performance_evidence",
    "no_fallback_no_zero_call_no_builtin_contamination",
)

ROUTE_EVIDENCE_FIELDS = ("public_api_route_evidence", "framework_integration_route_evidence")

SYNTHETIC_ONLY_FLAGS = (
    "synthetic_only",
    "monkeypatch_only",
    "report_only",
    "manifest_only",
    "benchmark_only",
    "mock_only",
)

PYTHON_SHIM_FLAGS = (
    "python_shim",
    "python_binding_surface",
    "python_only",
    "source_only",
    "comment_only",
    "delegates_to_python_binding",
)


DIAGNOSTIC_BASELINE_VALUES = {
    "diagnostic_only",
    "diagnostic",
    "report_only",
    "metadata_only",
    "not_measured",
    "none",
    "unknown",
}

REQUIRED_SOURCE_DISCOVERY_SOURCES = {
    "source",
    "bindings",
    "wrappers",
    "autograd",
    "aliases",
    "launch",
    "setup",
    "tests",
}

NEGATIVE_FALLBACK_FIELDS = (
    "fallback_detected",
    "zero_call_detected",
    "builtin_contamination_detected",
    "baseline_only_detected",
    "stub_detected",
)

NEGATIVE_ROUTE_FIELDS = (
    "direct_only",
    "direct_invocation_only",
    "direct_custom_op_only",
    "builtin_only",
    "aten_only",
    "cppextension_only",
    "python_shim_only",
    "fallback_detected",
    "zero_call_detected",
    "builtin_contamination_detected",
    "baseline_only_detected",
    "stub_detected",
)

ROUTE_BLOCKING_TEXT_TOKENS = (
    "direct_only",
    "direct-only",
    "builtin_only",
    "builtin-only",
    "aten_only",
    "aten-only",
    "cppextension_only",
    "fallback",
    "zero_call",
    "zero-call",
    "baseline_only",
    "stub",
    "synthetic",
    "mock",
    "report_only",
)


def _get_negative_route_fields(
    platform_policy: PlatformPolicy | None = None,
) -> tuple[str, ...]:
    """Return negative route field names augmented with policy-specific skip patterns."""
    skip = ()
    if platform_policy is not None:
        skip = platform_policy.custom_op_evidence.skip_patterns
    return NEGATIVE_ROUTE_FIELDS + skip


def _get_route_blocking_tokens(
    platform_policy: PlatformPolicy | None = None,
) -> tuple[str, ...]:
    """Return route blocking text tokens augmented with policy-specific skip patterns."""
    skip = ()
    if platform_policy is not None:
        skip = platform_policy.custom_op_evidence.skip_patterns
    return ROUTE_BLOCKING_TEXT_TOKENS + skip


REQUIRED_FINE_GRAINED_FIELDS = (
    "unit_identity",
    "variant_or_signature",
    "kernel_launch_sites",
    "public_entry_mapping",
    "inventory_granularity",
)

COARSE_SIGNAL_FIELDS = (
    "family_only",
    "row_count_only",
    "source_name_only",
    "coarse_only",
    "collapsed",
)

COARSE_GRANULARITY_VALUES = {
    "COARSE",
    "FAMILY",
    "FAMILY_ONLY",
    "ROW_COUNT_ONLY",
    "SOURCE_NAME_ONLY",
    "COLLAPSED",
}

FINE_GRAINED_GRANULARITY_VALUES = {"FINE_GRAINED", "FINE_GRAINED_UNIT", "UNIT", "UNIT_LEVEL"}
_PYTORCH_EXTENSION_ONLY_TOKENS = (
    "torch.utils.cpp_extension",
    "torch.utils.cpp_extension.cppextension",
    "cppextension",
    "cudaextension",
    "torch/extension.h",
    "<torch/extension.h>",
    "aten/",
    "aten::",
    " at::",
    "torch::tensor",
    "pybind11_module",
    "-ltorch_cpu",
    "-ltorch_python",
    "libtorch",
    "torch_cpu",
    "setup.py build_ext",
    "build_ext --inplace",
)


def _extension_only_tokens(platform_policy: PlatformPolicy | None = None) -> tuple[str, ...]:
    tokens: list[str] = list(_PYTORCH_EXTENSION_ONLY_TOKENS)
    if platform_policy is not None:
        for target in platform_policy.custom_op_evidence.target_device_values:
            normalized = _normalize_token(target)
            if not normalized:
                continue
            tokens.extend((
                f"{normalized}extension",
                f"{normalized}_extension",
                f"-ltorch_{normalized}",
            ))
            if not normalized.startswith("torch_"):
                tokens.extend((
                    f"torch_{normalized}.utils.cpp_extension",
                    f"torch_{normalized}.utils.cpp_extension.{normalized}extension",
                    f"-ltorch_{normalized}",
                ))
    return tuple(dict.fromkeys(tokens))

_OP_HOST_SOURCE_FIELDS = (
    "op_host",
    "op_host_path",
    "op_host_paths",
    "op_host_source_path",
    "op_host_sources",
    "op_host_source_paths",
    "host_source_path",
    "host_source_paths",
)

_OP_KERNEL_SOURCE_FIELDS = (
    "op_kernel",
    "op_kernel_path",
    "op_kernel_paths",
    "op_kernel_source_path",
    "op_kernel_sources",
    "op_kernel_source_paths",
    "kernel_source_path",
    "kernel_source_paths",
    "ascendc_kernel_sources",
)


def _get_kernel_source_fields(platform_policy: PlatformPolicy | None = None) -> tuple[str, ...]:
    """Return kernel source field names, extended with a policy-specific name.

    When a platform policy is available, the helper appends a field like
    ``{prefix}c_kernel_sources`` (e.g. ``ppuc_kernel_sources`` for
    ``ppu_ali``) so the validator can recognise platform-specific kernel
    evidence fields.  Without a policy the legacy constant is used as-is.
    """
    if platform_policy is None:
        return _OP_KERNEL_SOURCE_FIELDS
    prefix = platform_policy.id.split("_")[0]  # "ppu_ali" → "ppu"
    return _OP_KERNEL_SOURCE_FIELDS + (f"{prefix}c_kernel_sources",)


_OPP_BUILD_SCRIPT_FIELDS = (
    "opp_build_script",
    "opp_build_script_path",
    "build_script",
    "build_script_path",
    "build_file",
    "build_files",
    "cmake_path",
    "cmake_lists_path",
    "cmakelists_path",
)

_OPP_INSTALL_FIELDS = (
    "install_provenance",
    "install_evidence",
    "opp_install_provenance",
    "opp_install_evidence",
    "install_log_path",
    "opp_install_log_path",
    "install_path",
    "installed_path",
    "opp_install_path",
)

_OPP_INSTALL_PATH_FIELDS = (
    "path",
    "paths",
    "log_path",
    "log_paths",
    "install_log_path",
    "install_log_paths",
    "opp_install_log_path",
    "opp_install_log_paths",
    "install_path",
    "install_paths",
    "installed_path",
    "installed_paths",
    "opp_install_path",
    "opp_install_paths",
    "package_path",
    "package_paths",
    "provenance_path",
    "provenance_paths",
)

_OPP_GENERATED_ARTIFACT_FIELDS = (
    "generated_header_path",
    "generated_header_paths",
    "op_info_path",
    "op_info_paths",
    "kernel_meta_path",
    "kernel_meta_paths",
    "producer_artifact_path",
    "producer_artifact_paths",
    "opp_package_artifact",
    "opp_package_artifacts",
    "opp_package_path",
    "opp_package_paths",
    "cann_package_artifacts",
    "generated_artifacts",
)

_MAX_MANIFEST_BYTES = 2 * 1024 * 1024
_MAX_NATIVE_ARTIFACT_SCAN_BYTES = 128 * 1024 * 1024
_BINARY_SCAN_CHUNK_BYTES = 64 * 1024


def validate(data: dict[str, object]) -> ValidationDict:
    errors: list[str] = []

    if not isinstance(data.get("success"), bool):
        errors.append("success must be a boolean")

    iteration_count = data.get("iteration_count")
    if not isinstance(iteration_count, int) or isinstance(iteration_count, bool) or iteration_count < 0:
        errors.append("iteration_count must be an integer >= 0")

    if not isinstance(data.get("errors"), list):
        errors.append("errors must be a list")

    return {"passed": not errors, "errors": errors, "warnings": []}


def validate_serving_final_gate(
    data: dict[str, object],
    expected_route: str | None = None,
) -> ValidationDict:
    """Validate a strict vLLM/SGLang serving final-gate report.

    Delegates to ``validators.serving_validator.validate_serving_final_gate``
    to avoid code duplication.
    """
    return _validate_serving_final_gate(data, expected_route=expected_route)


def validate_custom_op_final_gate(
    data: dict[str, object],
    project_root: str | Path | None = None,
    platform_policy: PlatformPolicy | None = None,
) -> ValidationDict:
    errors: list[str] = []
    resolved_project_root = _resolve_existing_project_root(project_root, errors)

    custom_op_detected = data.get("custom_op_detected")
    if custom_op_detected is False and data.get("discovery_complete") is True:
        inventory_count_val = data.get("inventory_count", 0)
        manifest_entries_val = data.get("manifest_entries", 0)
        closed_pass_entries_val = data.get("closed_pass_entries", 0)
        if (
            (isinstance(inventory_count_val, int) and inventory_count_val == 0)
            and (isinstance(manifest_entries_val, int) and manifest_entries_val == 0)
            and (isinstance(closed_pass_entries_val, int) and closed_pass_entries_val == 0)
        ):
            return {"passed": True, "errors": [], "warnings": []}

    inventory_count = _int_field(data, "inventory_count", errors)
    manifest_entries = _int_field(data, "manifest_entries", errors)
    closed_pass_entries = _int_field(data, "closed_pass_entries", errors)
    remaining_entries = _int_field(data, "remaining_entries", errors)

    if inventory_count is not None and manifest_entries is not None and closed_pass_entries is not None:
        if not (inventory_count == manifest_entries == closed_pass_entries):
            errors.append("inventory_count, manifest_entries, and closed_pass_entries must match")
        if inventory_count <= 0:
            errors.append("inventory_count, manifest_entries, and closed_pass_entries must be > 0")
    if remaining_entries is not None and remaining_entries != 0:
        errors.append("remaining_entries must be 0")

    full_status = data.get("full_migration_status")
    _reject_blocking_status(full_status, "full_migration_status", errors)
    if full_status != "FULL_PASS":
        errors.append("full_migration_status must be 'FULL_PASS'")

    if data.get("project_e2e_passed") is not True:
        errors.append("project_e2e_passed must be true")
    if data.get("report_parity_passed") is not True:
        errors.append("report_parity_passed must be true")

    data_mapping = cast(Mapping[object, object], data)
    expanded_variant_units = _extract_expanded_variant_units(data_mapping, errors)
    require_strict_native_producer = _requires_strict_native_producer_closure(
        data_mapping,
        platform_policy,
        expanded_variant_units,
    )

    rows = data.get("rows")
    if not isinstance(rows, list) or not rows:
        errors.append("rows must be a non-empty list")
    else:
        row_items = cast(list[object], rows)
        if manifest_entries is not None and len(row_items) != manifest_entries:
            errors.append("rows length must equal manifest_entries")
        for index, row_obj in enumerate(row_items):
            if not isinstance(row_obj, dict):
                errors.append(f"rows[{index}] must be an object")
                continue
            row = cast(dict[object, object], row_obj)
            _validate_gate_row(
                row,
                index,
                errors,
                resolved_project_root,
                platform_policy,
                require_strict_native_producer,
            )
        _validate_source_inventory_completeness(data_mapping, row_items, errors)
        perf_mode = get_performance_validation_mode(platform_policy)
        if perf_mode != "disabled":
            _validate_performance_report_completeness(
                data_mapping,
                row_items,
                manifest_entries,
                errors,
                platform_policy,
                require_strict_native_producer,
            )
        _validate_required_manifest_units(
            row_items,
            inventory_count,
            manifest_entries,
            closed_pass_entries,
            resolved_project_root,
            errors,
        )
        if require_strict_native_producer:
            _validate_generated_opp_inventory_closure(
                row_items,
                inventory_count,
                manifest_entries,
                closed_pass_entries,
                resolved_project_root,
                errors,
                platform_policy,
            )
        if expanded_variant_units:
            _validate_expanded_variant_closure(
                data_mapping,
                row_items,
                expanded_variant_units,
                inventory_count,
                manifest_entries,
                closed_pass_entries,
                errors,
            )

    return {"passed": not errors, "errors": errors, "warnings": []}


def custom_op_final_gate_unit_ledger(
    data: object,
    *,
    target_units: list[str] | None = None,
    project_root: str | Path | None = None,
    custom_op_surface: Mapping[object, object] | None = None,
) -> dict[str, object]:
    """Build a diagnostic strict per-unit progress ledger for custom-op repair prompts.

    This helper never relaxes ``validate_custom_op_final_gate``. It classifies each
    target unit from the same strict row/source/performance checks so repair prompts
    can say which units are genuinely closed and which still need evidence.

    When *custom_op_surface* is provided it is forwarded to the parallelization-
    group builder so variant-heavy projects can be grouped by shared source files
    even when the gate file is missing.
    """

    root_errors: list[str] = []
    resolved_project_root = _resolve_existing_project_root(project_root, root_errors)
    gate: Mapping[object, object] = cast(Mapping[object, object], data) if isinstance(data, Mapping) else {}
    units = _dedupe_strings(target_units or [])
    if not units:
        units = _infer_custom_op_ledger_target_units(gate, resolved_project_root)

    raw_rows = gate.get("rows")
    row_items = cast(list[object], raw_rows) if isinstance(raw_rows, list) else []
    rows_by_name: dict[str, tuple[int, Mapping[object, object]]] = {}
    for index, row_obj in enumerate(row_items):
        if not isinstance(row_obj, Mapping):
            continue
        row = cast(Mapping[object, object], row_obj)
        row_name = _extract_row_name(row)
        if row_name and row_name not in rows_by_name:
            rows_by_name[row_name] = (index, row)

    source_entries = _extract_inventory_entries(gate.get("source_inventory"))
    performance_report = gate.get("performance_report") or gate.get("performance_report_evidence")
    performance_entries = (
        _extract_performance_report_entries(cast(Mapping[object, object], performance_report))
        if isinstance(performance_report, Mapping)
        else {}
    )

    ledger_rows: list[dict[str, object]] = []
    strict_pass_units: list[str] = []
    remaining_units: list[str] = []

    for unit in units:
        unit_errors = list(root_errors)
        row_pair = rows_by_name.get(unit)
        if row_pair is None:
            unit_errors.append("missing custom_op_final_gate row")
        else:
            index, row = row_pair
            _validate_gate_row(row, index, unit_errors, resolved_project_root)
            _validate_native_inventory_entry(row, f"rows[{index}]", unit_errors)

        if source_entries:
            source_entry = source_entries.get(unit)
            if source_entry is None:
                unit_errors.append("missing source_inventory entry")
            else:
                _validate_native_inventory_entry(source_entry, f"source_inventory.entries[{unit}]", unit_errors)
        else:
            unit_errors.append("missing source_inventory entries")

        if isinstance(performance_report, Mapping):
            performance_entry = performance_entries.get(unit)
            if performance_entry is None:
                unit_errors.append("missing performance_report entry")
            else:
                _validate_performance_report_entry(performance_entry, unit, unit_errors)
        else:
            unit_errors.append("missing performance_report")

        deduped_errors = _dedupe_strings(unit_errors)
        if deduped_errors:
            status = "remaining"
            remaining_units.append(unit)
        else:
            status = "strict_pass"
            strict_pass_units.append(unit)
        ledger_rows.append(
            {
                "unit_identity": unit,
                "status": status,
                "missing_evidence": deduped_errors,
            }
        )

    groups, summary = _build_parallelization_groups(gate, remaining_units, rows_by_name, custom_op_surface=custom_op_surface)

    return {
        "total_count": len(units),
        "strict_pass_count": len(strict_pass_units),
        "remaining_count": len(remaining_units),
        "strict_pass_units": strict_pass_units,
        "remaining_units": remaining_units,
        "units": ledger_rows,
        "global_errors": _dedupe_strings(root_errors),
        "parallelization_groups": groups,
        "parallelization_summary": summary,
    }


def _build_parallelization_groups(
    gate: Mapping[object, object],
    remaining_units: list[str],
    rows_by_name: dict[str, tuple[int, Mapping[object, object]]],
    custom_op_surface: Mapping[object, object] | None = None,
) -> tuple[list[dict[str, object]], str]:
    """Build parallelization groups for remaining custom-op units.

    Groups units that share source files (op_host or op_kernel paths)
    into the same parallelization group, since they cannot be
    parallelized safely.  Independent units (no shared source files
    with any other unit) each get their own group.

    When *custom_op_surface* is provided and gate-file rows lack source
    evidence (e.g. gate file is missing or empty), source paths are
    derived from the Phase 3.5 contract surface so that variant-heavy
    projects still receive properly merged groups instead of every unit
    becoming its own singleton.

    Returns a tuple of ``(groups, summary)`` where *groups* is a list
    of ``{group_id, units, shared_sources, is_independent}`` dicts and
    *summary* is a human-readable string like ``"3 groups (1 shared-kernel, 2 independent)"``.
    """
    if not remaining_units:
        return [], "0 groups (0 shared-kernel, 0 independent)"

    # Build unit → set of normalized source paths (host + kernel).
    unit_paths: dict[str, set[str]] = {}
    for unit in remaining_units:
        row_pair = rows_by_name.get(unit)
        if row_pair is None:
            unit_paths[unit] = set()
            continue
        _index, row = row_pair
        evidence = row.get("opp_custom_op_artifact_evidence")
        if not isinstance(evidence, Mapping):
            unit_paths[unit] = set()
            continue
        evidence_map = cast(Mapping[object, object], evidence)

        host_paths = _path_candidates_from_fields(evidence_map, _OP_HOST_SOURCE_FIELDS)
        kernel_paths = _path_candidates_from_fields(evidence_map, _OP_KERNEL_SOURCE_FIELDS)

        all_paths = host_paths + kernel_paths
        normalized: set[str] = set()
        for p in all_paths:
            norm = _normalize_reported_path(p)
            if norm:
                normalized.add(norm)
        unit_paths[unit] = normalized

    # Fallback: populate unit_paths from Phase 3.5 custom_op_surface when
    # gate-file rows lack source evidence (e.g. gate file missing, or rows
    # present but without opp_custom_op_artifact_evidence).  This prevents
    # variant-heavy projects with N expanded units from degrading to N
    # singleton parallel groups.
    if custom_op_surface is not None:
        _populate_unit_paths_from_contract_surface(
            unit_paths, remaining_units, custom_op_surface,
        )

    # Build reverse index: path → set of units that reference it.
    path_to_units: dict[str, set[str]] = {}
    for unit, paths in unit_paths.items():
        for p in paths:
            if p not in path_to_units:
                path_to_units[p] = set()
            path_to_units[p].add(unit)

    # Find connected components (units that share at least one path).
    visited: set[str] = set()
    components: list[list[str]] = []

    for unit in remaining_units:
        if unit in visited:
            continue
        component: list[str] = []
        queue: list[str] = [unit]
        visited.add(unit)
        while queue:
            u = queue.pop(0)
            component.append(u)
            for p in unit_paths.get(u, set()):
                for neighbor in path_to_units.get(p, set()):
                    if neighbor not in visited:
                        visited.add(neighbor)
                        queue.append(neighbor)
        components.append(component)

    # Build result group dicts.
    groups: list[dict[str, object]] = []
    shared_kernel_count = 0
    independent_count = 0

    for group_id, group_units in enumerate(components):
        if len(group_units) == 1:
            is_independent = True
            shared_sources: list[str] = []
            independent_count += 1
        else:
            is_independent = False
            shared_kernel_count += 1
            group_set = set(group_units)
            shared_sources = sorted(
                p for p, u_set in path_to_units.items()
                if len(u_set & group_set) >= 2
            )

        groups.append({
            "group_id": group_id,
            "units": group_units,
            "shared_sources": shared_sources,
            "is_independent": is_independent,
        })

    summary = f"{len(groups)} groups ({shared_kernel_count} shared-kernel, {independent_count} independent)"
    return groups, summary


def _populate_unit_paths_from_contract_surface(
    unit_paths: dict[str, set[str]],
    remaining_units: list[str],
    custom_op_surface: Mapping[object, object],
) -> None:
    """Populate *unit_paths* for units that lack source evidence from the gate file.

    Derives source file paths from ``fine_grained_operator_unit_evidence`` (base
    operator → source files) and ``expanded_operator_variants`` (variant →
    base_unit_identity) in the Phase 3.5 contract surface.  Only touches entries
    where *unit_paths* is currently empty.
    """
    # Build base_unit → source file paths from evidence entries.
    evidence_list = custom_op_surface.get("fine_grained_operator_unit_evidence")
    base_source_files: dict[str, set[str]] = {}
    if isinstance(evidence_list, list):
        for ev in cast(list[object], evidence_list):
            if not isinstance(ev, Mapping):
                continue
            ev_map = cast(Mapping[object, object], ev)
            unit_id = str(ev_map.get("unit_identity", "")).strip()
            src_evidence = ev_map.get("source_evidence")
            if not unit_id or not isinstance(src_evidence, list):
                continue
            paths: set[str] = set()
            for se in cast(list[object], src_evidence):
                if not isinstance(se, str):
                    continue
                path = se.split(":", 1)[0].strip()
                if path:
                    paths.add(path)
            if paths:
                base_source_files[unit_id] = paths

    if not base_source_files:
        return

    # Build variant → base_unit mapping from expanded_operator_variants.
    variants = custom_op_surface.get("expanded_operator_variants")
    variant_base_map: dict[str, str] = {}
    variant_own_paths: dict[str, set[str]] = {}
    if isinstance(variants, list):
        for v in cast(list[object], variants):
            if not isinstance(v, Mapping):
                continue
            v_map = cast(Mapping[object, object], v)
            unit_id = str(v_map.get("unit_identity", "")).strip()
            base_id = str(v_map.get("base_unit_identity", "")).strip()
            if not unit_id:
                continue
            if base_id:
                variant_base_map[unit_id] = base_id
            v_se = v_map.get("source_evidence")
            if isinstance(v_se, list):
                variant_paths: set[str] = set()
                for se in cast(list[object], v_se):
                    if not isinstance(se, str):
                        continue
                    path = se.split(":", 1)[0].strip()
                    if path:
                        variant_paths.add(path)
                if variant_paths:
                    variant_own_paths[unit_id] = variant_paths

    # Populate empty unit_paths entries.
    for unit in remaining_units:
        if unit_paths.get(unit):
            continue
        paths: set[str] = set()
        if unit in base_source_files:
            paths.update(base_source_files[unit])
        base_id = variant_base_map.get(unit, "")
        if base_id and base_id in base_source_files:
            paths.update(base_source_files[base_id])
        if unit in variant_own_paths:
            paths.update(variant_own_paths[unit])
        # Last resort: strip axis suffix and try base lookup.
        if not paths and ":" in unit:
            axis_sep = unit.rfind(":")
            base_guess = unit[:axis_sep]
            if base_guess in base_source_files:
                paths.update(base_source_files[base_guess])
        if paths:
            unit_paths[unit] = set(_normalize_reported_path(p) for p in paths if _normalize_reported_path(p))


def _dedupe_strings(values: list[str]) -> list[str]:
    deduped: list[str] = []
    for value in values:
        cleaned = str(value).strip()
        if cleaned and cleaned not in deduped:
            deduped.append(cleaned)
    return deduped


def _infer_custom_op_ledger_target_units(gate: Mapping[object, object], project_root: Path | None) -> list[str]:
    units: list[str] = []
    expanded_variant_units = _extract_expanded_variant_units(gate, [])
    units.extend(sorted(expanded_variant_units))
    units.extend(_load_required_manifest_units(project_root, []))

    rows = gate.get("rows")
    if isinstance(rows, list):
        for row_obj in cast(list[object], rows):
            if isinstance(row_obj, Mapping):
                row_name = _extract_row_name(cast(Mapping[object, object], row_obj))
                if row_name:
                    units.append(row_name)
    units.extend(_extract_inventory_entries(gate.get("source_inventory")).keys())
    return _dedupe_strings(units)


def _extract_expanded_variant_units(data: Mapping[object, object], errors: list[str]) -> set[str]:
    metadata = data.get("expanded_variant_inventory")
    if metadata is None:
        metadata = data.get("expanded_operator_variants")
    if metadata is None:
        return set()

    units: list[str] = []
    expected_count: int | None = None
    if isinstance(metadata, Mapping):
        inventory = cast(Mapping[object, object], metadata)
        if inventory.get("variant_axes_detected") is False:
            return set()
        raw_count = inventory.get("expanded_operator_instances_count") or inventory.get("unit_count")
        if isinstance(raw_count, int) and not isinstance(raw_count, bool):
            expected_count = raw_count
        raw_units = inventory.get("unit_identities") or inventory.get("expanded_unit_identities")
        if isinstance(raw_units, list):
            units.extend(_non_empty_strings(cast(list[object], raw_units)))
        raw_variants = inventory.get("variants") or inventory.get("expanded_operator_variants")
        if isinstance(raw_variants, list):
            units.extend(_unit_identities_from_variant_objects(cast(list[object], raw_variants)))
    elif isinstance(metadata, list):
        units.extend(_unit_identities_from_variant_objects(cast(list[object], metadata)))

    if not units:
        errors.append("expanded_variant_inventory must list expanded variant unit identities when present")
        return set()
    unit_set = set(units)
    if len(unit_set) != len(units):
        errors.append("expanded_variant_inventory unit identities must not contain duplicates")
    if expected_count is not None and expected_count != len(unit_set):
        errors.append("expanded_variant_inventory count must equal expanded variant unit identity count")
    return unit_set


def _requires_strict_native_producer_closure(
    data: Mapping[object, object],
    platform_policy: PlatformPolicy | None,
    expanded_variant_units: set[str],
) -> bool:
    if platform_policy is None:
        return False
    if not platform_policy.custom_op_evidence.strict_producer_closure_required:
        return False
    policy_value = data.get("custom_op_evidence_policy")
    policy_text = policy_value.strip().lower() if isinstance(policy_value, str) else ""
    expected_policy = platform_policy.custom_op_evidence.custom_op_evidence_policy.strip().lower()
    if expected_policy and policy_text == expected_policy:
        return True
    if bool(expanded_variant_units) or _has_strict_expanded_variant_metadata(data):
        return True
    return _gate_declares_policy_custom_op_target(data, platform_policy)


def _gate_declares_policy_custom_op_target(
    data: Mapping[object, object],
    platform_policy: PlatformPolicy,
) -> bool:
    device = data.get("device") or data.get("target_device")
    if isinstance(device, str) and _is_policy_target_device(_normalize_token(device), platform_policy):
        return True
    for field_name in ("custom_device", "custom_backend", "target_backend", "route", "migration_route"):
        value = data.get(field_name)
        if isinstance(value, str) and _is_policy_target_device(_normalize_token(value), platform_policy):
            return True
    return False


def _has_strict_expanded_variant_metadata(data: Mapping[object, object]) -> bool:
    for field_name in (
        "strict_expanded_variant_validation",
        "strict_expanded_variant_closure",
        "expanded_variant_static_required",
        "expanded_variant_runtime_required",
        "expanded_variant_contract",
    ):
        value = data.get(field_name)
        if value not in (None, False, "", [], {}):
            return True

    for metadata_field in ("expanded_variant_inventory", "expanded_operator_variants"):
        metadata = data.get(metadata_field)
        if isinstance(metadata, Mapping):
            inventory = cast(Mapping[object, object], metadata)
            for field_name in (
                "strict_validation_required",
                "strict_closure_required",
                "expanded_variant_static_required",
                "expanded_variant_runtime_required",
            ):
                if inventory.get(field_name) is True:
                    return True
    return False


def _non_empty_strings(values: list[object]) -> list[str]:
    return [value.strip() for value in values if isinstance(value, str) and value.strip()]


def _unit_identities_from_variant_objects(values: list[object]) -> list[str]:
    units: list[str] = []
    for value in values:
        if not isinstance(value, Mapping):
            continue
        unit_identity = cast(Mapping[object, object], value).get("unit_identity")
        if isinstance(unit_identity, str) and unit_identity.strip():
            units.append(unit_identity.strip())
    return units


def _validate_expanded_variant_closure(
    data: Mapping[object, object],
    row_items: list[object],
    expanded_variant_units: set[str],
    inventory_count: int | None,
    manifest_entries: int | None,
    closed_pass_entries: int | None,
    errors: list[str],
) -> None:
    row_names = _extract_row_names(row_items)
    _reject_collapsed_variant_identities(row_names, "rows", errors)
    if row_names != expanded_variant_units:
        _append_identity_set_error("rows must exactly match expanded variant unit identities", expanded_variant_units, row_names, errors)

    source_entries = _extract_inventory_entries(data.get("source_inventory"))
    source_names = set(source_entries)
    _reject_collapsed_variant_identities(source_names, "source_inventory", errors)
    if source_names != expanded_variant_units:
        _append_identity_set_error("source_inventory must exactly match expanded variant unit identities", expanded_variant_units, source_names, errors)

    report = data.get("performance_report") or data.get("performance_report_evidence")
    if isinstance(report, Mapping):
        performance_names = set(_extract_performance_report_entries(cast(Mapping[object, object], report)))
        _reject_collapsed_variant_identities(performance_names, "performance_report", errors)
        if performance_names != expanded_variant_units:
            _append_identity_set_error("performance_report must exactly match expanded variant unit identities", expanded_variant_units, performance_names, errors)
        unit_count = cast(Mapping[object, object], report).get("unit_count")
        if unit_count != len(expanded_variant_units):
            errors.append("performance_report.unit_count must equal expanded variant unit count")

    _validate_expanded_variant_runtime_coverage_report(data, expanded_variant_units, errors)

    expected_count = len(expanded_variant_units)
    counts = {
        "inventory_count": inventory_count,
        "manifest_entries": manifest_entries,
        "closed_pass_entries": closed_pass_entries,
        "rows length": len(row_items),
    }
    mismatched = [name for name, value in counts.items() if value != expected_count]
    if mismatched:
        errors.append("custom-op final gate counts must equal expanded variant unit count (%d): %s" % (expected_count, ", ".join(mismatched)))


def _validate_expanded_variant_runtime_coverage_report(
    data: Mapping[object, object],
    expanded_variant_units: set[str],
    errors: list[str],
) -> None:
    report = data.get("runtime_coverage_report") or data.get("runtime_coverage") or data.get("runtime_coverage_evidence")
    if not isinstance(report, Mapping):
        errors.append("runtime_coverage_report must be an object matching expanded variant unit identities")
        return
    report_map = cast(Mapping[object, object], report)
    if report_map.get("complete") is not True and report_map.get("all_units_covered") is not True:
        errors.append("runtime_coverage_report.complete must be true")
    if not _runtime_coverage_report_path_proves_required_file(report_map):
        errors.append("runtime_coverage_report must prove migration_reports/runtime_coverage.json was written")
    if _mapping_is_disallowed_surrogate(report_map):
        errors.append("runtime_coverage_report must not be report-only, benchmark-only, synthetic, mock, or manifest-only")

    unit_count = report_map.get("unit_count")
    if unit_count != len(expanded_variant_units):
        errors.append("runtime_coverage_report.unit_count must equal expanded variant unit count")

    coverage_entries = _extract_runtime_coverage_entries(report_map)
    coverage_names = set(coverage_entries)
    _reject_collapsed_variant_identities(coverage_names, "runtime_coverage_report", errors)
    if coverage_names != expanded_variant_units:
        _append_identity_set_error("runtime_coverage_report must exactly match expanded variant unit identities", expanded_variant_units, coverage_names, errors)
    for unit_name, entry in coverage_entries.items():
        _validate_runtime_coverage_report_entry(entry, unit_name, errors)


def _runtime_coverage_report_path_proves_required_file(report: Mapping[object, object]) -> bool:
    for field_name in ("path", "report_path", "project_relative_path"):
        value = report.get(field_name)
        if isinstance(value, str) and value.strip().replace("\\", "/").endswith("migration_reports/runtime_coverage.json"):
            return True
    return False


def _extract_runtime_coverage_entries(report: Mapping[object, object]) -> dict[str, Mapping[object, object]]:
    entries_by_name: dict[str, Mapping[object, object]] = {}
    raw_entries = report.get("entries") or report.get("coverage_entries") or report.get("rows")
    entries: list[object] = []
    if isinstance(raw_entries, list):
        entries = cast(list[object], raw_entries)
    elif isinstance(raw_entries, Mapping):
        for key, value in cast(Mapping[object, object], raw_entries).items():
            if isinstance(value, Mapping):
                entry = dict(cast(Mapping[object, object], value))
                _ = entry.setdefault("unit_identity", key)
                entries.append(entry)
    for item in entries:
        if not isinstance(item, Mapping):
            continue
        entry = cast(Mapping[object, object], item)
        name = _first_string_field(entry, ("unit_identity", "row_id", "manifest_row_id", "name", "operator", "op_name", "id"))
        if name:
            entries_by_name[name] = entry
    return entries_by_name


def _validate_runtime_coverage_report_entry(entry: Mapping[object, object], unit_name: str, errors: list[str]) -> None:
    label = f"runtime_coverage_report.entries[{unit_name}]"
    if _mapping_reports_failure(entry) or _mapping_is_disallowed_surrogate(entry):
        errors.append(f"{label} must be real passing runtime evidence, not report/synthetic/mock/benchmark-only")
    if entry.get("same_run") is not True:
        errors.append(f"{label} must prove same_run=true")
    custom_call_count = _extract_custom_call_count(entry)
    if custom_call_count is None or custom_call_count <= 0:
        errors.append(f"{label} must include custom call count > 0")
    if not _has_positive_boolean(entry, ("project_api_route", "public_api_route", "custom_op_route_executed", "project_api_invoked", "public_api_invoked")):
        errors.append(f"{label} must prove runtime coverage through the project/public API route")
    if not _has_positive_boolean(entry, ("native_custom_op_route_executed", "compiled_kernel_executed", "opp_kernel_executed", "opp_custom_op_executed", "native_custom_op_executed")):
        errors.append(f"{label} must prove native compiled custom-op runtime coverage")


def _reject_collapsed_variant_identities(identities: set[str], label: str, errors: list[str]) -> None:
    collapsed = sorted(identity for identity in identities if _identity_looks_collapsed_variant(identity))
    if collapsed:
        errors.append(f"{label} must not use collapsed expanded-variant identities: " + ", ".join(collapsed))


def _identity_looks_collapsed_variant(identity: str) -> bool:
    normalized = identity.lower()
    return "{" in normalized or "}" in normalized or "|" in normalized or "..." in normalized or "all_" in normalized or "all-" in normalized


def _append_identity_set_error(label: str, expected: set[str], observed: set[str], errors: list[str]) -> None:
    missing = sorted(expected - observed)
    extra = sorted(observed - expected)
    details: list[str] = []
    if missing:
        details.append("missing: " + ", ".join(missing))
    if extra:
        details.append("extra: " + ", ".join(extra))
    errors.append(label + " (" + "; ".join(details) + ")")


def _int_field(data: dict[str, object], field_name: str, errors: list[str]) -> int | None:
    value = data.get(field_name)
    if not isinstance(value, int) or isinstance(value, bool):
        errors.append(f"{field_name} must be an integer")
        return None
    return value


def _validate_gate_row(
    row: Mapping[object, object],
    index: int,
    errors: list[str],
    project_root: Path | None,
    platform_policy: PlatformPolicy | None = None,
    require_strict_native_producer: bool = False,
) -> None:
    status = row.get("status")
    _reject_blocking_status(status, f"rows[{index}].status", errors)
    if _normalize_status(status) not in PASS_STATES:
        errors.append(f"rows[{index}].status must be a pass state")

    perf_mode = get_performance_validation_mode(platform_policy)
    for field_name in REQUIRED_ROW_EVIDENCE_FIELDS:
        if field_name in {"adapter_evidence", "parity_evidence", "no_fallback_no_zero_call_no_builtin_contamination"}:
            continue
        if field_name == "performance_evidence" and perf_mode == "disabled":
            continue
        if not _has_evidence(row.get(field_name)):
            errors.append(f"rows[{index}].{field_name} must contain evidence")

    _validate_project_local_artifact(
        row.get("opp_custom_op_artifact_evidence"),
        row,
        index,
        errors,
        project_root,
        platform_policy,
        require_strict_native_producer,
    )
    _validate_adapter_evidence(row.get("adapter_evidence"), index, errors)
    _validate_parity_evidence(row.get("parity_evidence"), index, errors, require_strict_native_producer)
    _validate_integration_route(row.get("integration_e2e_evidence"), index, errors)
    if require_strict_native_producer:
        _validate_per_row_route_evidence(row, index, errors, platform_policy)
    _validate_runtime_coverage(row.get("same_run_runtime_coverage"), index, errors)
    if perf_mode != "disabled":
        _validate_performance(row.get("performance_evidence"), index, errors, platform_policy, require_strict_native_producer)
    _validate_no_fallback_evidence(row.get("no_fallback_no_zero_call_no_builtin_contamination"), index, errors)

    custom_call_count = _extract_custom_call_count(row)
    if custom_call_count is None or custom_call_count <= 0:
        errors.append("rows[%d].same_run_runtime_coverage must include custom call count > 0" % index)

    contamination = row.get("no_fallback_no_zero_call_no_builtin_contamination")
    if _has_negative_contamination_signal(contamination):
        errors.append(f"rows[{index}].no_fallback_no_zero_call_no_builtin_contamination reports contamination")


def _reject_blocking_status(value: object, label: str, errors: list[str]) -> None:
    status = _normalize_status(value)
    if status in BLOCKING_STATUSES:
        errors.append(f"{label} must not be {status}")


def _normalize_status(value: object) -> str:
    return str(value or "").strip().upper().replace("-", "_").replace(" ", "_")


def _has_evidence(value: object) -> bool:
    if value is True:
        return True
    if value in (None, False):
        return False
    if isinstance(value, (str, list, tuple, set)):
        return False
    if isinstance(value, Mapping):
        evidence = cast(Mapping[object, object], value)
        if not evidence:
            return False
        if _mapping_reports_failure(evidence):
            return False
        if _mapping_is_disallowed_surrogate(evidence):
            return False
        if not _mapping_reports_positive_evidence(evidence):
            return False
        return True
    return True


def _mapping_reports_failure(evidence: Mapping[object, object]) -> bool:
    for boolean_field in ("passed", "present", "verified", "checked", "ok", "success"):
        if evidence.get(boolean_field) is False:
            return True
    for negative_field in ("failed", "not_checked", "missing", "missing_positive", "incomplete"):
        if evidence.get(negative_field) is True:
            return True
    status = evidence.get("status")
    if status is None:
        return False
    normalized_status = _normalize_status(status)
    return normalized_status in BLOCKING_STATUSES or normalized_status not in EVIDENCE_PASS_STATES


def _mapping_is_disallowed_surrogate(evidence: Mapping[object, object]) -> bool:
    return any(evidence.get(flag_name) is True for flag_name in SYNTHETIC_ONLY_FLAGS)


def _mapping_reports_positive_evidence(evidence: Mapping[object, object]) -> bool:
    positive_boolean_fields = (
        "passed",
        "present",
        "verified",
        "imported",
        "built",
        "loaded",
        "executed",
        "covered",
        "project_api_invoked",
        "public_api_invoked",
        "custom_op_route_executed",
        "native_custom_op_route_executed",
        "compiled_kernel_executed",
        "project_api_route",
        "public_api_route",
    )
    for field_name in positive_boolean_fields:
        if evidence.get(field_name) is True:
            return True
    positive_numeric_fields = (
        "custom_call_count",
        "runtime_call_count",
        "speedup_vs_baseline",
        "throughput_ratio",
        "max_abs_error",
        "baseline_seconds",
        "custom_seconds",
    )
    for field_name in positive_numeric_fields:
        value = evidence.get(field_name)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
            return True
    return False


def _string_values(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in cast(list[object], value) if isinstance(item, str) and item.strip()]


def _non_empty_string_list(value: object) -> bool:
    return bool(_string_values(value))


def _truthy_evidence(value: object) -> bool:
    if isinstance(value, Mapping):
        evidence = cast(Mapping[object, object], value)
        if evidence.get("passed") is False or evidence.get("success") is False:
            return False
        return any(
            item not in (None, False, "", [], {})
            for item in evidence.values()
        )
    if isinstance(value, list):
        evidence_items = cast(list[object], value)
        return bool(evidence_items) and all(_truthy_evidence(item) for item in evidence_items)
    if isinstance(value, str):
        return bool(value.strip())
    return value is True


def _validate_adapter_evidence(value: object, index: int, errors: list[str]) -> None:
    if not isinstance(value, Mapping):
        errors.append(f"rows[{index}].adapter_evidence must be an object with adapter/import/link proof")
        return
    evidence = cast(Mapping[object, object], value)
    if not evidence or _mapping_reports_failure(evidence) or _mapping_is_disallowed_surrogate(evidence):
        errors.append(f"rows[{index}].adapter_evidence must contain passing adapter/import/link evidence")
        return
    if not _has_positive_boolean(
        evidence,
        (
            "imported",
            "loaded",
            "linked",
            "registered",
            "adapter_imported",
            "adapter_loaded",
            "adapter_linked",
            "adapter_callable",
            "callable_resolved",
        ),
    ):
        errors.append(f"rows[{index}].adapter_evidence must prove adapter import/link/callable success")


def _validate_parity_evidence(
    value: object,
    index: int,
    errors: list[str],
    require_strict_native_producer: bool = False,
) -> None:
    if not isinstance(value, Mapping):
        errors.append(f"rows[{index}].parity_evidence must be an object with direct/reference parity proof")
        return
    evidence = cast(Mapping[object, object], value)
    if not evidence or _mapping_reports_failure(evidence) or _mapping_is_disallowed_surrogate(evidence):
        errors.append(f"rows[{index}].parity_evidence must contain passing direct/reference parity evidence")
        return
    if _has_positive_boolean(evidence, ("passed", "verified", "ok", "success", "parity_passed", "comparison_passed")):
        return
    if _normalize_status(evidence.get("status")) in EVIDENCE_PASS_STATES:
        return
    max_abs_error = evidence.get("max_abs_error")
    tolerance = evidence.get("tolerance") or evidence.get("atol") or evidence.get("max_abs_error_tolerance")
    if _non_negative_number(max_abs_error) and not require_strict_native_producer:
        return
    if _non_negative_number(max_abs_error) and _non_negative_number(tolerance) and cast(float, max_abs_error) <= cast(float, tolerance):
        return
    errors.append(f"rows[{index}].parity_evidence must prove a passing direct/reference parity comparison")


def _non_negative_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0


def _validate_source_inventory_completeness(
    data: Mapping[object, object], row_items: list[object], errors: list[str]
) -> None:
    source_inventory = data.get("source_inventory")
    _validate_source_inventory_metadata(source_inventory, errors)
    inventory_entries = _extract_inventory_entries(source_inventory)
    inventory_names = set(inventory_entries)
    if not inventory_names:
        errors.append("source_inventory must contain source-discovered entries for every manifest row")
        return

    row_names: set[str] = set()
    for index, row_obj in enumerate(row_items):
        if not isinstance(row_obj, Mapping):
            continue
        row = cast(Mapping[object, object], row_obj)
        row_name = _extract_row_name(row)
        if not row_name:
            errors.append(f"rows[{index}] must identify the manifest custom op for source inventory matching")
            continue
        row_names.add(row_name)
        entry = inventory_entries.get(row_name)
        if entry is None:
            continue
        _validate_native_inventory_entry(entry, f"source_inventory.entries[{row_name}]", errors)
        _validate_native_inventory_entry(row, f"rows[{index}]", errors)

    if row_names and inventory_names != row_names:
        missing = sorted(row_names - inventory_names)
        extra = sorted(inventory_names - row_names)
        details: list[str] = []
        if missing:
            details.append("missing source inventory entries: " + ", ".join(missing))
        if extra:
            details.append("source inventory entries without manifest rows: " + ", ".join(extra))
        errors.append("source_inventory must match manifest rows (" + "; ".join(details) + ")")


def _validate_required_manifest_units(
    row_items: list[object],
    inventory_count: int | None,
    manifest_entries: int | None,
    closed_pass_entries: int | None,
    project_root: Path | None,
    errors: list[str],
) -> None:
    required_units = _load_required_manifest_units(project_root, errors)
    if not required_units:
        return

    required_set = set(required_units)
    row_names = _extract_row_names(row_items)
    if row_names != required_set:
        missing = sorted(required_set - row_names)
        extra = sorted(row_names - required_set)
        details: list[str] = []
        if missing:
            details.append("missing required units: " + ", ".join(missing))
        if extra:
            details.append("unexpected units: " + ", ".join(extra))
        errors.append("rows must exactly match migration_manifest.required_units (" + "; ".join(details) + ")")

    expected_count = len(required_units)
    counts = {
        "inventory_count": inventory_count,
        "manifest_entries": manifest_entries,
        "closed_pass_entries": closed_pass_entries,
        "rows length": len(row_items),
    }
    mismatched = [name for name, value in counts.items() if value != expected_count]
    if mismatched:
        errors.append(
            f"custom-op final gate counts must equal migration_manifest.required_units length ({expected_count}): "
            + ", ".join(mismatched)
        )


def _validate_generated_opp_inventory_closure(
    row_items: list[object],
    inventory_count: int | None,
    manifest_entries: int | None,
    closed_pass_entries: int | None,
    project_root: Path | None,
    errors: list[str],
    platform_policy: PlatformPolicy | None = None,
) -> None:
    generated_units = _discover_generated_opp_units(project_root, platform_policy)
    if not generated_units:
        return

    row_names = _extract_row_names(row_items)
    if not row_names:
        errors.append("generated OPP inventory closure requires manifest rows to identify every custom op")
        return

    row_generated_tokens: set[str] = set()
    for row_item in row_items:
        if isinstance(row_item, Mapping):
            row_generated_tokens.update(_canonical_generated_opp_tokens_from_value(cast(Mapping[object, object], row_item)))

    missing_generated = sorted(
        generated_unit
        for generated_unit in generated_units
        if not _generated_opp_unit_is_covered(generated_unit, row_names, row_generated_tokens)
    )
    if missing_generated:
        errors.append(
            "generated OPP inventory contains project-local generated operators not covered by final gate rows: "
            + _format_limited_list(missing_generated)
        )

    expected_count = len(generated_units)
    counts = {
        "inventory_count": inventory_count,
        "manifest_entries": manifest_entries,
        "closed_pass_entries": closed_pass_entries,
        "rows length": len(row_items),
    }
    mismatched = [name for name, value in counts.items() if value is not None and value < expected_count]
    if mismatched:
        errors.append(
            "custom-op final gate counts must cover all generated OPP operator entries discovered on disk "
            +
            f"({expected_count}): "
            + ", ".join(mismatched)
        )


def _load_required_manifest_units(project_root: Path | None, errors: list[str]) -> list[str]:
    if project_root is None:
        return []
    manifest_path = project_root / "migration_reports" / "migration_manifest.json"
    try:
        if not manifest_path.is_file():
            errors.append("migration_reports/migration_manifest.json must exist for custom-op final gate validation")
            return []
        if manifest_path.stat().st_size > _MAX_MANIFEST_BYTES:
            errors.append("migration_reports/migration_manifest.json is too large for custom-op final gate validation")
            return []
        with manifest_path.open("r", encoding="utf-8") as handle:
            manifest_data = cast(object, json.load(handle))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"migration_reports/migration_manifest.json could not be read: {exc}")
        return []
    if not isinstance(manifest_data, Mapping):
        errors.append("migration_reports/migration_manifest.json must be a JSON object")
        return []
    manifest = cast(Mapping[object, object], manifest_data)
    raw_required_units = manifest.get("required_units")
    if not isinstance(raw_required_units, list) or not raw_required_units:
        errors.append("migration_reports/migration_manifest.json must contain non-empty required_units")
        return []
    required_units = cast(list[object], raw_required_units)
    units = [unit for unit in required_units if isinstance(unit, str) and unit.strip()]
    if len(units) != len(required_units):
        errors.append("migration_reports/migration_manifest.json required_units must contain only non-empty strings")
        return []
    if len(set(units)) != len(units):
        errors.append("migration_reports/migration_manifest.json required_units must not contain duplicates")
        return []
    return units


def _discover_generated_opp_units(project_root: Path | None, platform_policy: PlatformPolicy | None = None) -> set[str]:
    if project_root is None:
        return set()
    units: set[str] = set()
    for path in _generated_opp_scan_files(project_root, platform_policy):
        unit = _generated_opp_unit_from_path(path, project_root)
        if unit:
            units.add(unit)
    return units


def _generated_opp_scan_files(project_root: Path, platform_policy: PlatformPolicy | None = None) -> list[Path]:
    candidates: list[Path] = []
    for path in project_root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(project_root).as_posix().lower()
        if not _path_is_generated_opp_inventory_entry(relative):
            continue
        if _generated_opp_path_is_config_or_container(relative, platform_policy):
            continue
        candidates.append(path)
    return candidates


def _path_is_generated_opp_inventory_entry(relative_path: str) -> bool:
    name = Path(relative_path).name
    if relative_path.endswith(('.h', '.hpp')) and any(token in relative_path for token in _SEAM_OPP_INVENTORY_PATH_TOKENS):
        return name.startswith(_GENERATED_OPP_API_PREFIX) or "op_proto" not in name
    if relative_path.endswith(".o") and _OPP_GENERATED_KERNEL_PATH_PATTERN in relative_path:
        return True
    if relative_path.endswith(".json") and _OPP_GENERATED_KERNEL_PATH_PATTERN in relative_path:
        return True
    return False


def _generated_opp_path_is_config_or_container(relative_path: str, platform_policy: PlatformPolicy | None = None) -> bool:
    basename = Path(relative_path).name.lower()
    validated_configs: set[str] = {"binary_info_config.json", "aic-ascend*-ops-info.json", "npu_supported_ops.json"}
    if platform_policy is not None and platform_policy.custom_op_evidence.validated_config_files:
        validated_configs = set(platform_policy.custom_op_evidence.validated_config_files)
    if basename in validated_configs:
        return True
    if any(fnmatch.fnmatch(basename, pattern) for pattern in validated_configs):
        return True
    if _OPP_TBE_CONFIG_PATH_PATTERN in relative_path or _OPP_INFO_CFG_PATH_PATTERN in relative_path:
        return True
    if any(token in relative_path for token in _SEAM_OPP_CONFIG_CONTAINER_TOKENS):
        return True
    if basename == "op_proto.h":
        return True
    return False


def _generated_opp_unit_from_path(path: Path, project_root: Path) -> str | None:
    try:
        relative = path.relative_to(project_root).as_posix()
    except ValueError:
        return None
    basename = path.name
    lowered = relative.lower()
    if basename.startswith(_GENERATED_OPP_API_PREFIX) and basename.endswith((".h", ".hpp")):
        stem = basename.rsplit(".", 1)[0]
        return _canonical_generated_opp_unit(stem.removeprefix(_GENERATED_OPP_API_PREFIX))
    if path.suffix.lower() in {".o", ".json"} and _OPP_GENERATED_KERNEL_PATH_PATTERN in f"/{lowered}":
        return _canonical_generated_opp_unit(_strip_generated_kernel_hash(path.stem))
    return None


def _strip_generated_kernel_hash(stem: str) -> str:
    match = re.match(r"^(?P<name>.+)_[0-9a-fA-F]{16,}$", stem)
    if match:
        return match.group("name")
    return stem


def _generated_opp_unit_is_covered(generated_unit: str, row_names: set[str], row_generated_tokens: set[str]) -> bool:
    if generated_unit in row_generated_tokens:
        return True
    for row_name in row_names:
        row_tokens = _canonical_row_identity_tokens(row_name)
        if generated_unit in row_tokens:
            return True
    return False


def _canonical_generated_opp_tokens_from_value(value: object) -> set[str]:
    tokens: set[str] = set()
    if isinstance(value, str):
        path = _strip_source_location(value)
        path_like = "/" in path or "\\" in path or Path(path).suffix
        if path_like and _is_safe_project_relative_path(path):
            basename = Path(path).name
            if basename.startswith(_GENERATED_OPP_API_PREFIX) and basename.endswith((".h", ".hpp")):
                tokens.add(_canonical_generated_opp_unit(basename.rsplit(".", 1)[0].removeprefix(_GENERATED_OPP_API_PREFIX)))
            elif basename.endswith((".o", ".json")):
                tokens.add(_canonical_generated_opp_unit(_strip_generated_kernel_hash(Path(basename).stem)))
            path_part = Path(path).parent.name
            if path_part:
                tokens.add(_canonical_generated_opp_unit(path_part))
        else:
            tokens.add(_canonical_generated_opp_unit(value))
    elif isinstance(value, Mapping):
        for item in cast(Mapping[object, object], value).values():
            tokens.update(_canonical_generated_opp_tokens_from_value(item))
    elif isinstance(value, (list, tuple, set)):
        for item in cast(list[object] | tuple[object, ...] | set[object], value):
            tokens.update(_canonical_generated_opp_tokens_from_value(item))
    return {token for token in tokens if token}


def _canonical_row_identity_tokens(row_name: str) -> set[str]:
    tokens = {_canonical_generated_opp_unit(row_name)}
    for part in row_name.split(":"):
        if not part or "=" in part:
            continue
        tokens.add(_canonical_generated_opp_unit(part))
    return {token for token in tokens if token}


def _canonical_generated_opp_unit(value: str) -> str:
    raw = value.strip()
    if not raw:
        return ""
    snake = _camel_to_snake(raw).replace("-", "_").replace(".", "_")
    snake = re.sub(r"[^a-zA-Z0-9_]+", "_", snake).lower()
    snake = re.sub(r"_+", "_", snake).strip("_")
    snake = snake.removeprefix(_GENERATED_OPP_API_PREFIX)
    snake = _OPP_UNIT_SUFFIX_PATTERN.sub("", snake)
    replacements = {
        "scalar_iso": "scalar",
        "scalar_born_iso": "scalar_born",
        "acoustic_iso": "acoustic",
        "elastic_iso": "elastic",
        "forward": "fwd",
        "backward": "bwd",
        "compress": "simple_compress",
        "decompress": "decompress",
        "save_snapshot": "storage_snapshot",
        "load_snapshot": "storage_snapshot",
    }
    return replacements.get(snake, snake)


def _camel_to_snake(value: str) -> str:
    first_pass = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", value)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", first_pass)


def _format_limited_list(values: list[str], limit: int = 20) -> str:
    formatted = ", ".join(values[:limit])
    if len(values) > limit:
        formatted += f", ... +{len(values) - limit} more"
    return formatted


def _extract_inventory_entries(value: object) -> dict[str, Mapping[object, object]]:
    entries_by_name: dict[str, Mapping[object, object]] = {}
    entries: list[object] = []
    if isinstance(value, Mapping):
        inventory = cast(Mapping[object, object], value)
        raw_entries = inventory.get("entries")
        if isinstance(raw_entries, list):
            entries = cast(list[object], raw_entries)
        else:
            for key, item in inventory.items():
                if key in {"discovery_complete", "discovery_sources_checked", "out_of_scope_source_groups"}:
                    continue
                if isinstance(item, Mapping):
                    entry = dict(cast(Mapping[object, object], item))
                    if "name" not in entry:
                        entry["name"] = _first_string_field(entry, ("unit_identity", "name")) or key
                    entries.append(entry)
    elif isinstance(value, list):
        entries = cast(list[object], value)

    for item in entries:
        if not isinstance(item, Mapping):
            continue
        entry = cast(Mapping[object, object], item)
        name = _first_string_field(entry, ("unit_identity", "name", "operator", "op_name", "row_id", "id"))
        if name:
            entries_by_name[name] = entry
    return entries_by_name


def _validate_native_inventory_entry(entry: Mapping[object, object], label: str, errors: list[str]) -> None:
    missing_fields = [
        field_name
        for field_name in ("native_operator_symbols", "kernel_functions", "source_evidence")
        if not _has_non_empty_inventory_value(entry.get(field_name))
    ]
    if missing_fields:
        errors.append(label + " missing native inventory fields: " + ", ".join(missing_fields))
    _validate_fine_grained_inventory_entry(entry, label, errors)


def _validate_fine_grained_inventory_entry(entry: Mapping[object, object], label: str, errors: list[str]) -> None:
    missing_fields = [
        field_name
        for field_name in REQUIRED_FINE_GRAINED_FIELDS
        if not _has_non_empty_inventory_value(entry.get(field_name))
    ]
    if missing_fields:
        errors.append(label + " missing fine-grained unit fields: " + ", ".join(missing_fields))

    if any(entry.get(field_name) is True for field_name in COARSE_SIGNAL_FIELDS):
        errors.append(label + " reports coarse/collapsed inventory signals")

    granularity = _normalize_status(entry.get("inventory_granularity"))
    if granularity in COARSE_GRANULARITY_VALUES:
        errors.append(label + ".inventory_granularity must be fine_grained, not " + granularity)
    elif entry.get("inventory_granularity") is not None and granularity not in FINE_GRAINED_GRANULARITY_VALUES:
        errors.append(label + ".inventory_granularity must be fine_grained")

    for field_name in ("native_operator_symbols", "kernel_functions", "kernel_launch_sites"):
        value = entry.get(field_name)
        if isinstance(value, Mapping):
            errors.append(label + f".{field_name} must describe one fine-grained unit, not nested family/group mappings")

    unit_identity = _first_string_field(entry, ("unit_identity",))
    row_name = _first_string_field(entry, ("name", "operator", "op_name", "row_id", "id"))
    if unit_identity and row_name and unit_identity != row_name:
        errors.append(label + ".unit_identity must match the source_inventory/manifest row identity")


def _has_non_empty_inventory_value(value: object) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, Mapping):
        mapped_value = cast(Mapping[object, object], value)
        for raw_item in mapped_value.values():
            item: object = raw_item
            if _has_non_empty_inventory_value(item):
                return True
        return False
    if isinstance(value, list):
        return any(_has_non_empty_inventory_value(item) for item in cast(list[object], value))
    if isinstance(value, tuple):
        return any(_has_non_empty_inventory_value(item) for item in cast(tuple[object, ...], value))
    if isinstance(value, set):
        return any(_has_non_empty_inventory_value(item) for item in cast(set[object], value))
    return value is not None and value is not False


def _validate_source_inventory_metadata(value: object, errors: list[str]) -> None:
    if not isinstance(value, Mapping):
        errors.append("source_inventory must include discovery_complete and discovery_sources_checked metadata")
        return
    inventory = cast(Mapping[object, object], value)
    if inventory.get("discovery_complete") is not True:
        errors.append("source_inventory.discovery_complete must be true")
    if "out_of_scope_source_groups" not in inventory:
        errors.append("source_inventory.out_of_scope_source_groups must list excluded source groups, even when empty")
    elif not isinstance(inventory.get("out_of_scope_source_groups"), list):
        errors.append("source_inventory.out_of_scope_source_groups must be a list")

    sources = inventory.get("discovery_sources_checked")
    if not isinstance(sources, list):
        errors.append("source_inventory.discovery_sources_checked must list source discovery categories")
        return
    source_values = {str(source).strip().lower().replace("-", "_") for source in cast(list[object], sources)}
    if "requirements_doc" in source_values:
        errors.append("source_inventory.discovery_sources_checked must be source-driven and must not include requirements_doc as a completion source")
    missing_sources = sorted(REQUIRED_SOURCE_DISCOVERY_SOURCES - source_values)
    if missing_sources:
        errors.append("source_inventory.discovery_sources_checked missing required sources: " + ", ".join(missing_sources))


def _extract_row_name(row: Mapping[object, object]) -> str | None:
    return _first_string_field(row, ("unit_identity", "name", "operator", "op_name", "row_id", "id"))


def _first_string_field(row: Mapping[object, object], fields: tuple[str, ...]) -> str | None:
    for field_name in fields:
        value = row.get(field_name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _validate_project_local_artifact(
    value: object,
    row: Mapping[object, object],
    index: int,
    errors: list[str],
    project_root: Path | None,
    platform_policy: PlatformPolicy | None = None,
    require_strict_native_producer: bool = False,
) -> None:
    if not isinstance(value, Mapping):
        errors.append(f"rows[{index}].opp_custom_op_artifact_evidence must be an object with project-local proof")
        return
    evidence = cast(Mapping[object, object], value)
    if evidence.get("project_local") is not True and evidence.get("in_project") is not True:
        errors.append(f"rows[{index}].opp_custom_op_artifact_evidence must prove project-local artifact creation")
    if not _has_project_local_path_proof(evidence):
        errors.append(f"rows[{index}].opp_custom_op_artifact_evidence must include project-local path proof")
    if not _has_positive_boolean(evidence, ("built", "present", "loaded", "verified")):
        errors.append(f"rows[{index}].opp_custom_op_artifact_evidence must prove artifact was built/loaded")
    if _is_python_shim_artifact(evidence):
        errors.append(f"rows[{index}].opp_custom_op_artifact_evidence must not be a Python shim or Python binding surface")
    native_paths = _native_compiled_artifact_paths(evidence, platform_policy)
    if not native_paths:
        native_label = platform_policy.guidance_native_label if platform_policy is not None else "target-platform"
        errors.append(f"rows[{index}].opp_custom_op_artifact_evidence must prove a native compiled {native_label} custom-op artifact")
    elif not _runtime_loaded_native_artifact_matches(evidence, native_paths, project_root):
        errors.append(f"rows[{index}].opp_custom_op_artifact_evidence must prove the same-run runtime loaded the native compiled artifact, not a Python shim")

    existing_paths: list[Path] = []
    if project_root is not None and native_paths:
        existing_paths = _existing_native_artifacts(project_root, native_paths)
        if not existing_paths:
            errors.append(f"rows[{index}].opp_custom_op_artifact_evidence native artifact path must exist under the project root and be a non-empty compiled binary")

    build_log_path = _validate_build_provenance(evidence, index, errors, project_root)
    if require_strict_native_producer:
        _validate_strict_native_producer_evidence(evidence, row, index, errors, project_root, build_log_path, platform_policy)
    if project_root is not None and existing_paths:
        _validate_native_platform_evidence(
            evidence, row, index, errors, project_root,
            existing_paths, build_log_path, platform_policy,
        )


def _validate_integration_route(value: object, index: int, errors: list[str]) -> None:
    if not isinstance(value, Mapping):
        errors.append(f"rows[{index}].integration_e2e_evidence must be an object with project API proof")
        return
    evidence = cast(Mapping[object, object], value)
    if _mapping_is_disallowed_surrogate(evidence):
        errors.append(f"rows[{index}].integration_e2e_evidence must not be synthetic/monkeypatch/report/benchmark-only")
    if not _has_positive_boolean(evidence, ("project_api_invoked", "public_api_invoked", "custom_op_route_executed")):
        errors.append(f"rows[{index}].integration_e2e_evidence must prove public/project API custom-op execution")
    if not _has_positive_boolean(evidence, ("native_custom_op_route_executed", "compiled_kernel_executed", "torch_ops_route", "opp_kernel_executed")):
        errors.append(f"rows[{index}].integration_e2e_evidence must prove native compiled custom-op route execution")


def _validate_per_row_route_evidence(
    row: Mapping[object, object],
    index: int,
    errors: list[str],
    platform_policy: PlatformPolicy | None = None,
) -> None:
    route_errors: list[str] = []
    valid_route_found = False
    for field_name in ROUTE_EVIDENCE_FIELDS:
        value = row.get(field_name)
        if value is None:
            continue
        field_errors: list[str] = []
        route_items = _route_evidence_items(value, field_name, index, field_errors)
        for label, evidence in route_items:
            _validate_single_route_evidence(row, evidence, field_name, index, field_errors, label, platform_policy)
        if field_errors:
            route_errors.extend(field_errors)
        else:
            valid_route_found = True
    if not valid_route_found:
        errors.append(
            f"rows[{index}] must include valid public_api_route_evidence or framework_integration_route_evidence for same-run custom-op execution"
        )
    errors.extend(route_errors)


def _route_evidence_items(
    value: object,
    field_name: str,
    index: int,
    errors: list[str],
) -> list[tuple[str, Mapping[object, object]]]:
    label = f"rows[{index}].{field_name}"
    if isinstance(value, Mapping):
        return [(label, cast(Mapping[object, object], value))]
    if isinstance(value, list):
        if not value:
            errors.append(f"{label} must be a non-empty object list when encoded as an array")
            return []
        items: list[tuple[str, Mapping[object, object]]] = []
        for item_index, item in enumerate(cast(list[object], value)):
            item_label = f"{label}[{item_index}]"
            if not isinstance(item, Mapping):
                errors.append(f"{item_label} must be an object")
                continue
            items.append((item_label, cast(Mapping[object, object], item)))
        return items
    errors.append(f"{label} must be an object or non-empty object list")
    return []


def _validate_single_route_evidence(
    row: Mapping[object, object],
    evidence: Mapping[object, object],
    field_name: str,
    index: int,
    errors: list[str],
    label: str | None = None,
    platform_policy: PlatformPolicy | None = None,
) -> None:
    label = label or f"rows[{index}].{field_name}"
    if not evidence:
        errors.append(f"{label} must not be empty")
        return
    if _mapping_reports_failure(evidence) or _mapping_is_disallowed_surrogate(evidence):
        errors.append(f"{label} must be real passing evidence, not report/synthetic/mock/benchmark-only")
    if _has_negative_route_signal(evidence, platform_policy):
        errors.append(f"{label} must not be direct-only, builtin-only, fallback, zero-call, baseline-only, stub, ATen-only, or Python-shim evidence")
    if evidence.get("same_run") is not True:
        errors.append(f"{label} must prove same_run=true")
    custom_call_count = _extract_custom_call_count(evidence)
    if custom_call_count is None or custom_call_count <= 0:
        errors.append(f"{label} must include custom call count > 0")
    if not _has_positive_boolean(
        evidence,
        (
            "native_custom_op_route_executed",
            "compiled_kernel_executed",
            "opp_kernel_executed",
            "opp_custom_op_executed",
            "native_opp_execution",
            "native_custom_op_executed",
        ),
    ):
        errors.append(f"{label} must prove native custom-op/OPP execution")
    if field_name == "public_api_route_evidence":
        if not _has_positive_boolean(
            evidence,
            (
                "public_api_invoked",
                "project_api_invoked",
                "public_entry_invoked",
                "project_public_api_invoked",
            ),
        ):
            errors.append(f"{label} must prove public/project API entry invocation")
    else:
        if not _has_positive_boolean(
            evidence,
            (
                "framework_integration_invoked",
                "framework_entry_invoked",
                "module_forward_invoked",
                "autograd_invoked",
                "training_step_invoked",
            ),
        ):
            errors.append(f"{label} must prove framework integration entry invocation")
    _validate_route_identity(row, evidence, label, errors)


def _has_negative_route_signal(
    evidence: Mapping[object, object],
    platform_policy: PlatformPolicy | None = None,
) -> bool:
    if any(evidence.get(field_name) is True for field_name in _get_negative_route_fields(platform_policy)):
        return True
    if _normalize_status(evidence.get("route_type")) in {"DIRECT_ONLY", "BUILTIN_ONLY", "ATEN_ONLY", "FALLBACK", "STUB"}:
        return True
    text = _flatten_string_values(evidence)
    return any(token in text for token in _get_route_blocking_tokens(platform_policy))


def _flatten_string_values(value: object) -> str:
    if isinstance(value, str):
        return value.lower()
    if isinstance(value, Mapping):
        parts: list[str] = []
        for item in cast(Mapping[object, object], value).values():
            parts.append(_flatten_string_values(item))
        return "\n".join(parts)
    if isinstance(value, (list, tuple, set)):
        return "\n".join(_flatten_string_values(item) for item in cast(list[object] | tuple[object, ...] | set[object], value))
    return ""


def _validate_route_identity(
    row: Mapping[object, object],
    evidence: Mapping[object, object],
    label: str,
    errors: list[str],
) -> None:
    row_identity = _first_string_field(row, ("unit_identity", "row_id", "name", "operator", "op_name", "id"))
    evidence_identity = _first_string_field(evidence, ("unit_identity", "row_id", "manifest_row_id", "name", "operator", "op_name", "id"))
    if row_identity and evidence_identity and row_identity != evidence_identity:
        errors.append(f"{label} identity must match the manifest row identity")


def _validate_runtime_coverage(value: object, index: int, errors: list[str]) -> None:
    if not isinstance(value, Mapping):
        errors.append(f"rows[{index}].same_run_runtime_coverage must be an object with runtime proof")
        return
    evidence = cast(Mapping[object, object], value)
    if _mapping_is_disallowed_surrogate(evidence):
        errors.append(f"rows[{index}].same_run_runtime_coverage must not be synthetic/monkeypatch/report/benchmark-only")
    if evidence.get("same_run") is not True:
        errors.append(f"rows[{index}].same_run_runtime_coverage must prove same-run coverage")
    if not _has_positive_boolean(evidence, ("project_api_route", "public_api_route", "custom_op_route_executed")):
        errors.append(f"rows[{index}].same_run_runtime_coverage must prove runtime coverage through the project API route")
    if not _has_positive_boolean(evidence, ("native_custom_op_route_executed", "compiled_kernel_executed", "torch_ops_route", "opp_kernel_executed")):
        errors.append(f"rows[{index}].same_run_runtime_coverage must prove native compiled custom-op runtime coverage")


def _validate_performance(
    value: object,
    index: int,
    errors: list[str],
    platform_policy: PlatformPolicy | None = None,
    require_strict_native_producer: bool = False,
) -> None:
    if not isinstance(value, Mapping):
        errors.append(f"rows[{index}].performance_evidence must be an object with numeric timings")
        return
    evidence = cast(Mapping[object, object], value)
    if _mapping_is_disallowed_surrogate(evidence):
        errors.append(f"rows[{index}].performance_evidence must not be report-only or benchmark-only without project API proof")

    perf_mode = get_performance_validation_mode(platform_policy)
    if perf_mode in ("full", "presence_only"):
        required_positive = ("baseline_seconds", "custom_seconds")
        missing = [field_name for field_name in required_positive if not _positive_number(evidence.get(field_name))]
        if missing:
            errors.append(f"rows[{index}].performance_evidence missing positive numeric fields: " + ", ".join(missing))
    if perf_mode == "full":
        if not _positive_number(evidence.get("speedup_vs_baseline")):
            errors.append(f"rows[{index}].performance_evidence.speedup_vs_baseline must be a positive number")

    if not _has_positive_boolean(evidence, ("project_api_invoked", "public_api_invoked", "custom_op_route_executed")):
        errors.append(f"rows[{index}].performance_evidence must prove timing came from public/project API custom-op route")
    _validate_baseline_and_custom_device_proof(
        evidence,
        f"rows[{index}].performance_evidence",
        errors,
        platform_policy,
        require_strict_native_producer,
    )


def _validate_performance_report_completeness(
    data: Mapping[object, object],
    row_items: list[object],
    manifest_entries: int | None,
    errors: list[str],
    platform_policy: PlatformPolicy | None = None,
    require_strict_native_producer: bool = False,
) -> None:
    report = data.get("performance_report")
    if report is None:
        report = data.get("performance_report_evidence")
    if not isinstance(report, Mapping):
        errors.append("performance_report must be an object proving complete migration_reports/performance.json coverage")
        return
    report_map = cast(Mapping[object, object], report)

    if report_map.get("complete") is not True:
        errors.append("performance_report.complete must be true")
    if not _performance_report_path_proves_required_file(report_map):
        errors.append("performance_report must prove migration_reports/performance.json was written")
    if _mapping_is_disallowed_surrogate(report_map):
        errors.append("performance_report must not be report-only, benchmark-only, synthetic, mock, or manifest-only")
    if not _has_positive_boolean(report_map, ("project_api_invoked", "public_api_invoked", "custom_op_route_executed", "verified")):
        errors.append("performance_report must prove speedup timings came from public/project API custom-op routes")
    _validate_baseline_and_custom_device_proof(
        report_map,
        "performance_report",
        errors,
        platform_policy,
        require_strict_native_producer,
    )
    _validate_overall_performance_report(
        report_map, errors, platform_policy, require_strict_native_producer
    )

    row_names = _extract_row_names(row_items)
    if manifest_entries is not None:
        unit_count = report_map.get("unit_count")
        if not isinstance(unit_count, int) or isinstance(unit_count, bool):
            errors.append("performance_report.unit_count must equal manifest_entries")
        elif unit_count != manifest_entries:
            errors.append("performance_report.unit_count must equal manifest_entries")
    if row_names:
        report_entries = _extract_performance_report_entries(report_map)
        report_names = set(report_entries)
        if not report_names:
            errors.append("performance_report must contain per-unit speedup entries for every manifest row")
            return
        if report_names != row_names:
            missing = sorted(row_names - report_names)
            extra = sorted(report_names - row_names)
            details: list[str] = []
            if missing:
                details.append("missing performance entries: " + ", ".join(missing))
            if extra:
                details.append("performance entries without manifest rows: " + ", ".join(extra))
            errors.append("performance_report must match manifest rows (" + "; ".join(details) + ")")
        for unit_name, entry in report_entries.items():
            _validate_performance_report_entry(entry, unit_name, errors, platform_policy, require_strict_native_producer)


def _validate_overall_performance_report(
    report: Mapping[object, object],
    errors: list[str],
    platform_policy: PlatformPolicy | None = None,
    require_strict_native_producer: bool = False,
) -> None:
    perf_mode = get_performance_validation_mode(platform_policy)
    if perf_mode == "full":
        required_positive = (
            "overall_baseline_seconds",
            "overall_custom_seconds",
            "overall_speedup_vs_baseline",
        )
        missing = [field_name for field_name in required_positive if not _positive_number(report.get(field_name))]
        if missing:
            errors.append("performance_report missing positive overall speedup fields: " + ", ".join(missing))
    elif perf_mode == "presence_only":
        required_positive = ("overall_baseline_seconds", "overall_custom_seconds")
        missing = [field_name for field_name in required_positive if not _positive_number(report.get(field_name))]
        if missing:
            errors.append("performance_report missing positive overall timing fields: " + ", ".join(missing))

    route_proven = _has_positive_boolean(report, ("overall_project_api_invoked", "overall_custom_op_route_executed"))
    all_units_replaced_proven = _has_positive_boolean(
        report,
        ("overall_all_units_replaced", "all_custom_op_units_replaced", "all_units_replaced"),
    )

    overall_evidence = report.get("overall_evidence")
    if isinstance(overall_evidence, Mapping):
        evidence = cast(Mapping[object, object], overall_evidence)
        route_proven = route_proven or _has_positive_boolean(
            evidence,
            ("project_api_invoked", "public_api_invoked", "custom_op_route_executed"),
        )
        all_units_replaced_proven = all_units_replaced_proven or _has_positive_boolean(
            evidence,
            ("overall_all_units_replaced", "all_custom_op_units_replaced", "all_units_replaced"),
        )

    if not route_proven:
        errors.append("performance_report must prove overall timing ran through the project API after all custom-op units were replaced")
    if not all_units_replaced_proven:
        errors.append("performance_report must prove overall timing was measured after all source-discovered custom-op units were replaced")
    _validate_independent_performance_measurement(
        report, "performance_report", errors, require_strict_native_producer
    )


def _performance_report_path_proves_required_file(report: Mapping[object, object]) -> bool:
    for field_name in ("path", "report_path", "project_relative_path"):
        value = report.get(field_name)
        if isinstance(value, str) and value.strip().replace("\\", "/").endswith("migration_reports/performance.json"):
            return True
    return False


def _extract_row_names(row_items: list[object]) -> set[str]:
    row_names: set[str] = set()
    for row_obj in row_items:
        if not isinstance(row_obj, Mapping):
            continue
        row_name = _extract_row_name(cast(Mapping[object, object], row_obj))
        if row_name:
            row_names.add(row_name)
    return row_names


def _extract_performance_report_entries(report: Mapping[object, object]) -> dict[str, Mapping[object, object]]:
    entries_by_name: dict[str, Mapping[object, object]] = {}
    raw_entries = report.get("entries")
    entries: list[object] = []
    if isinstance(raw_entries, list):
        entries = cast(list[object], raw_entries)
    elif isinstance(raw_entries, Mapping):
        for key, value in cast(Mapping[object, object], raw_entries).items():
            if isinstance(value, Mapping):
                entry = dict(cast(Mapping[object, object], value))
                entry["unit_identity"] = entry.get("unit_identity", key)
                entries.append(entry)
    for item in entries:
        if not isinstance(item, Mapping):
            continue
        entry = cast(Mapping[object, object], item)
        name = _first_string_field(entry, ("unit_identity", "row_id", "name", "operator", "op_name", "id"))
        if name:
            entries_by_name[name] = entry
    return entries_by_name


def _validate_performance_report_entry(
    entry: Mapping[object, object],
    unit_name: str,
    errors: list[str],
    platform_policy: PlatformPolicy | None = None,
    require_strict_native_producer: bool = False,
) -> None:
    perf_mode = get_performance_validation_mode(platform_policy)
    if perf_mode in ("full", "presence_only"):
        required_positive = ("baseline_seconds", "custom_seconds")
        missing = [field_name for field_name in required_positive if not _positive_number(entry.get(field_name))]
        if missing:
            errors.append(f"performance_report.entries[{unit_name}] missing positive numeric fields: " + ", ".join(missing))
    if perf_mode == "full":
        if not _positive_number(entry.get("speedup_vs_baseline")):
            errors.append(f"performance_report.entries[{unit_name}].speedup_vs_baseline must be a positive number")
    if not _has_positive_boolean(entry, ("project_api_invoked", "public_api_invoked", "custom_op_route_executed")):
        errors.append(f"performance_report.entries[{unit_name}] must prove public/project API custom-op timing route")
    _validate_baseline_and_custom_device_proof(
        entry,
        f"performance_report.entries[{unit_name}]",
        errors,
        platform_policy,
        require_strict_native_producer,
    )


def _validate_no_fallback_evidence(value: object, index: int, errors: list[str]) -> None:
    if not isinstance(value, Mapping):
        errors.append(f"rows[{index}].no_fallback_no_zero_call_no_builtin_contamination must be an object with explicit no-fallback proof")
        return
    evidence = cast(Mapping[object, object], value)
    if _mapping_is_disallowed_surrogate(evidence):
        errors.append(f"rows[{index}].no_fallback_no_zero_call_no_builtin_contamination must not be synthetic/monkeypatch/report/benchmark-only")
    if any(evidence.get(field_name) is True for field_name in NEGATIVE_FALLBACK_FIELDS):
        errors.append(f"rows[{index}].no_fallback_no_zero_call_no_builtin_contamination reports contamination")
        return
    all_negative_flags_false = all(evidence.get(field_name) is False for field_name in NEGATIVE_FALLBACK_FIELDS)
    if not all_negative_flags_false:
        errors.append(f"rows[{index}].no_fallback_no_zero_call_no_builtin_contamination must explicitly set all fallback/zero-call/builtin/baseline/stub flags to false")


def _has_positive_boolean(evidence: Mapping[object, object], fields: tuple[str, ...]) -> bool:
    return any(evidence.get(field_name) is True for field_name in fields)


def _positive_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


def _has_project_local_path_proof(evidence: Mapping[object, object]) -> bool:
    for field_name in ("project_relative_path", "path"):
        value = evidence.get(field_name)
        if isinstance(value, str) and _is_safe_project_relative_path(value):
            return True
    return False


def _is_python_shim_artifact(evidence: Mapping[object, object]) -> bool:
    if any(evidence.get(flag_name) is True for flag_name in PYTHON_SHIM_FLAGS):
        return True
    for field_name in (*_ARTIFACT_PATH_FIELDS, *_RUNTIME_LOADED_PATH_FIELDS):
        value = evidence.get(field_name)
        if isinstance(value, str) and value.strip().lower().replace("\\", "/").endswith(".py"):
            return True
    for field_name in ("artifact_type", "kind", "type", "surface", "description"):
        value = evidence.get(field_name)
        if not isinstance(value, str):
            continue
        normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
        if any(token in normalized for token in ("python_shim", "python_binding", "binding_surface", "delegates_to_python", "source_only", "comment_only")):
            return True
    return False


_ARTIFACT_PATH_FIELDS = (
    "path",
    "project_relative_path",
    "binary_path",
    "artifact_path",
    "library_path",
    "opp_artifact_path",
    "custom_opp_path",
)

_ARTIFACT_SEQUENCE_FIELDS = (
    "paths",
    "artifacts",
    "artifact_paths",
    "library_paths",
    "native_artifacts",
    "opp_artifacts",
)

_ARTIFACT_NESTED_FIELDS = (
    "artifact",
    "compiled_artifact",
    "native_artifact",
    "project_local_artifact",
    "project_local_artifact_proof",
    "runtime_project_local_artifact",
)

_RUNTIME_LOADED_PATH_FIELDS = (
    "runtime_module_file",
    "runtime_loaded_module_file",
    "runtime_loaded_module_path",
    "runtime_loaded_artifact",
    "runtime_loaded_artifact_path",
    "loaded_artifact_path",
    "loaded_library_path",
    "loaded_path",
    "module_file",
    "module_origin",
    "origin",
    "__file__",
)


_EVIDENCE_ONLY_PATH_TOKENS = (
    "evidence",
    "stub",
    "dummy",
    "fake",
    "placeholder",
    "mock",
)

_EVIDENCE_ONLY_SOURCE_TOKENS = (
    "evidence",
    "stub",
    "dummy",
    "fake",
    "placeholder",
    "mock",
    "marker",
)


def _validate_strict_native_producer_evidence(
    evidence: Mapping[object, object],
    row: Mapping[object, object],
    index: int,
    errors: list[str],
    project_root: Path | None,
    build_log_path: Path | None,
    platform_policy: PlatformPolicy | None = None,
) -> None:
    _ = row
    producer_text = _flatten_evidence_text(evidence)
    build_log_text = _read_text_limited(build_log_path)
    extension_only_tokens = _extension_only_tokens(platform_policy)
    if _text_has_any_token(f"{producer_text}\n{build_log_text}", extension_only_tokens):
        errors.append(
            f"rows[{index}].opp_custom_op_artifact_evidence must be strict native producer evidence, not NpuExtension/CppExtension/ATen/libtorch-only native-extension evidence"
        )

    op_host_paths = [path for path in _path_candidates_from_fields(evidence, _OP_HOST_SOURCE_FIELDS) if _is_op_host_source_path(path)]
    op_kernel_paths = [path for path in _path_candidates_from_fields(evidence, _get_kernel_source_fields(platform_policy)) if _is_op_kernel_source_path(path)]
    build_script_paths = [path for path in _path_candidates_from_fields(evidence, _OPP_BUILD_SCRIPT_FIELDS) if _is_opp_build_script_path(path)]
    generated_artifact_paths, generated_artifact_categories = _strict_opp_generated_artifacts(evidence)

    if not op_host_paths:
        errors.append(f"rows[{index}].opp_custom_op_artifact_evidence must include an op_host source path")
    if not op_kernel_paths:
        errors.append(f"rows[{index}].opp_custom_op_artifact_evidence must include an op_kernel/AscendC source path")
    if not build_script_paths:
        errors.append(f"rows[{index}].opp_custom_op_artifact_evidence must include CMakeLists.txt, build.sh, or equivalent OPP build script path")
    if not _has_install_provenance(evidence):
        errors.append(f"rows[{index}].opp_custom_op_artifact_evidence must include OPP install/provenance evidence")
    missing_generated_categories = _missing_generated_opp_categories(generated_artifact_categories)
    if missing_generated_categories:
        errors.append(
            f"rows[{index}].opp_custom_op_artifact_evidence must include generated OPP artifact categories: "
            + ", ".join(missing_generated_categories)
            + "; a path that merely looks like /opp/ is not enough"
        )

    if build_log_path is not None and not _build_log_is_strict_opp(build_log_text, platform_policy):
        errors.append(
            f"rows[{index}].opp_custom_op_artifact_evidence.build_provenance.log_path must show a CANN/OPP build-install flow with op_host/op_kernel producer artifacts"
        )

    if project_root is None:
        return

    _validate_existing_source_paths(project_root, op_host_paths, f"rows[{index}].opp_custom_op_artifact_evidence.op_host source", errors)
    _validate_existing_source_paths(project_root, op_kernel_paths, f"rows[{index}].opp_custom_op_artifact_evidence.op_kernel source", errors)
    _validate_existing_paths(project_root, build_script_paths, f"rows[{index}].opp_custom_op_artifact_evidence OPP build script", errors)
    _validate_existing_paths(project_root, generated_artifact_paths, f"rows[{index}].opp_custom_op_artifact_evidence generated OPP artifact", errors)
    _validate_existing_install_paths(project_root, evidence, f"rows[{index}].opp_custom_op_artifact_evidence install/provenance", errors)

    source_text = "\n".join(
        _read_text_limited(_resolve_path_under_project_root(project_root, path))
        for path in [*op_host_paths, *op_kernel_paths]
    )
    if _text_has_any_token(source_text, extension_only_tokens):
        errors.append(
            f"rows[{index}].opp_custom_op_artifact_evidence op_host/op_kernel sources must not be ATen/torch extension sources"
        )


def _flatten_evidence_text(value: object) -> str:
    if isinstance(value, str):
        return value.lower()
    if isinstance(value, Mapping):
        parts: list[str] = []
        for key, item in cast(Mapping[object, object], value).items():
            parts.append(str(key).lower())
            parts.append(_flatten_evidence_text(item))
        return "\n".join(parts)
    if isinstance(value, (list, tuple, set)):
        return "\n".join(_flatten_evidence_text(item) for item in cast(list[object] | tuple[object, ...] | set[object], value))
    return str(value).lower() if value is not None else ""


def _path_candidates_from_fields(evidence: Mapping[object, object], fields: tuple[str, ...]) -> list[str]:
    candidates: list[str] = []
    for field_name in fields:
        candidates.extend(_path_candidates_from_value(evidence.get(field_name)))
    return candidates


def _path_candidates_from_value(value: object) -> list[str]:
    if isinstance(value, str):
        parts = re.split(r"[;\n]+", value)
        return [_strip_source_location(part.strip()) for part in parts if part.strip()]
    if isinstance(value, Mapping):
        candidates: list[str] = []
        for item in cast(Mapping[object, object], value).values():
            candidates.extend(_path_candidates_from_value(item))
        return candidates
    if isinstance(value, (list, tuple, set)):
        candidates = []
        for item in cast(list[object] | tuple[object, ...] | set[object], value):
            candidates.extend(_path_candidates_from_value(item))
        return candidates
    return []


def _is_op_host_source_path(value: str) -> bool:
    normalized = value.strip().lower().replace("\\", "/")
    padded = f"/{normalized}"
    has_host_dir = any(f"/{d}/" in padded for d in _OPP_HOST_DIRNAMES)
    return _is_safe_project_relative_path(value) and has_host_dir and normalized.endswith((".c", ".cc", ".cpp", ".cxx", ".h", ".hpp"))


def _is_op_kernel_source_path(value: str) -> bool:
    normalized = value.strip().lower().replace("\\", "/")
    padded = f"/{normalized}"
    has_kernel_dir = any(f"/{d}/" in padded for d in _OPP_KERNEL_DIRNAMES)
    return _is_safe_project_relative_path(value) and has_kernel_dir and normalized.endswith((".c", ".cc", ".cpp", ".cxx", ".h", ".hpp"))


def _is_opp_build_script_path(value: str) -> bool:
    normalized = value.strip().lower().replace("\\", "/")
    basename = Path(normalized).name
    return _is_safe_project_relative_path(value) and (
        basename in _SEAM_OPP_BUILD_SCRIPT_NAMES
        or ("opp" in normalized and basename.endswith(_SEAM_OPP_BUILD_SCRIPT_EXTENSIONS))
    )


def _is_opp_generated_artifact_path(value: str) -> bool:
    normalized = value.strip().lower().replace("\\", "/")
    if not _is_safe_project_relative_path(value):
        return False
    return any(token in normalized for token in _SEAM_OPP_GENERATED_ARTIFACT_TOKENS)


def _strict_opp_generated_artifacts(evidence: Mapping[object, object]) -> tuple[list[str], set[str]]:
    paths: list[str] = []
    categories: set[str] = set()
    for field_name in _OPP_GENERATED_ARTIFACT_FIELDS:
        for path in _path_candidates_from_value(evidence.get(field_name)):
            category = _generated_opp_artifact_category(field_name, path)
            if category is None:
                continue
            paths.append(path)
            categories.add(category)
    return _dedupe_paths(paths), categories


def _generated_opp_artifact_category(field_name: str, value: str) -> str | None:
    if not _is_safe_project_relative_path(value):
        return None
    normalized = value.strip().lower().replace("\\", "/")
    basename = Path(normalized).name
    if field_name in {"generated_header_path", "generated_header_paths"}:
        return "generated_header" if basename.endswith((".h", ".hpp")) else None
    if field_name in {"op_info_path", "op_info_paths"}:
        return "op_info" if basename.endswith((".json", ".ini", ".yaml", ".yml")) else None
    if field_name in {"kernel_meta_path", "kernel_meta_paths"}:
        return "kernel_meta" if basename.endswith((".o", ".bin", ".json")) else None
    if field_name in {
        "producer_artifact_path",
        "producer_artifact_paths",
        "opp_package_artifact",
        "opp_package_artifacts",
        "opp_package_path",
        "opp_package_paths",
        "cann_package_artifacts",
    }:
        return "package" if _is_opp_generated_artifact_path(value) else None
    if basename.endswith((".h", ".hpp")) and any(token in normalized for token in _SEAM_OPP_ARTIFACT_CATEGORY_TOKENS[:3]):
        return "generated_header"
    if basename.endswith((".json", ".ini", ".yaml", ".yml")) and (_SEAM_OPP_ARTIFACT_CATEGORY_TOKENS[3] in normalized or "op_info" in basename):
        return "op_info"
    if basename.endswith((".o", ".bin", ".json")) and (_SEAM_OPP_ARTIFACT_CATEGORY_TOKENS[4] in normalized or "kernel_meta" in basename):
        return "kernel_meta"
    if _is_opp_generated_artifact_path(value):
        return "package"
    return None


def _missing_generated_opp_categories(categories: set[str]) -> list[str]:
    if "package" in categories:
        return []
    required = ("generated_header", "op_info", "kernel_meta")
    return [category for category in required if category not in categories]


def _dedupe_paths(paths: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for path in paths:
        normalized = _normalize_reported_path(path)
        if normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(path)
    return deduped


def _has_install_provenance(evidence: Mapping[object, object]) -> bool:
    if _has_positive_boolean(evidence, ("installed", "install_verified", "opp_installed", "package_installed")):
        return True
    for field_name in _OPP_INSTALL_FIELDS:
        value = evidence.get(field_name)
        if isinstance(value, str) and _is_safe_project_relative_path(value):
            return True
        if isinstance(value, Mapping):
            mapped_value = cast(Mapping[object, object], value)
            if _has_non_empty_inventory_value(mapped_value):
                return True
    return False


def _build_log_is_strict_opp(text: str, platform_policy: PlatformPolicy | None = None) -> bool:
    normalized = text.lower()
    if not normalized.strip():
        return False
    native_tokens: tuple[str, ...] = _SEAM_OPP_STRICT_BUILD_LOG_TOKENS
    if platform_policy is not None:
        policy_tokens = get_native_build_log_tokens(platform_policy)
        if policy_tokens:
            native_tokens = tuple(policy_tokens)
    has_cann = any(token in normalized for token in native_tokens)
    has_layout = "op_host" in normalized and "op_kernel" in normalized
    install_tokens: list[str] = ["install", "vendors", "package", "deploy"]
    if platform_policy is not None:
        prefix = platform_policy.id.split("_")[0]  # "npu_ascend" → "npu"
        install_tokens.extend([f"{prefix}_opp", f"{prefix}_path"])
    else:
        install_tokens.extend(["opp_path", "ascend_opp"])  # legacy fallback
    has_install = any(token in normalized for token in install_tokens)
    return has_cann and has_layout and has_install


def _validate_existing_source_paths(project_root: Path, paths: list[str], label: str, errors: list[str]) -> None:
    for path in paths:
        resolved = _resolve_path_under_project_root(project_root, path)
        if resolved is None or not resolved.is_file() or resolved.stat().st_size <= 0:
            errors.append(f"{label} path must exist under the project root and be non-empty: {_normalize_reported_path(path)}")


def _validate_existing_paths(project_root: Path, paths: list[str], label: str, errors: list[str]) -> None:
    for path in paths:
        resolved = _resolve_path_under_project_root(project_root, path)
        if resolved is None or not resolved.exists():
            errors.append(f"{label} path must exist under the project root: {_normalize_reported_path(path)}")


def _validate_existing_install_paths(project_root: Path, evidence: Mapping[object, object], label: str, errors: list[str]) -> None:
    paths = _install_path_candidates(evidence)
    if not paths:
        return
    for path in paths:
        if _is_safe_project_relative_path(path):
            resolved = _resolve_path_under_project_root(project_root, path)
            if resolved is None or not resolved.exists():
                errors.append(f"{label} path must exist under the project root: {_normalize_reported_path(path)}")


def _install_path_candidates(evidence: Mapping[object, object]) -> list[str]:
    paths: list[str] = []
    for field_name in _OPP_INSTALL_FIELDS:
        value = evidence.get(field_name)
        if isinstance(value, str):
            paths.append(_strip_source_location(value))
        elif isinstance(value, Mapping):
            paths.extend(_install_path_candidates_from_mapping(cast(Mapping[object, object], value)))
        elif isinstance(value, (list, tuple, set)):
            for item in cast(list[object] | tuple[object, ...] | set[object], value):
                if isinstance(item, str):
                    paths.append(_strip_source_location(item))
                elif isinstance(item, Mapping):
                    paths.extend(_install_path_candidates_from_mapping(cast(Mapping[object, object], item)))
    return _dedupe_paths(paths)


def _install_path_candidates_from_mapping(value: Mapping[object, object]) -> list[str]:
    paths: list[str] = []
    for field_name in _OPP_INSTALL_PATH_FIELDS:
        paths.extend(_path_candidates_from_value(value.get(field_name)))
    return paths


def _native_compiled_artifact_paths(
    evidence: Mapping[object, object],
    platform_policy: PlatformPolicy | None = None,
) -> list[str]:
    candidates = _native_artifact_path_candidates(evidence)
    platform_signaled = [
        value
        for value in candidates
        if _is_native_compiled_platform_artifact_path(value, platform_policy)
    ]
    if platform_signaled:
        return platform_signaled
    return [value for value in candidates if _is_compiled_project_artifact_path(value)]


def _is_compiled_project_artifact_path(value: str) -> bool:
    if not _is_safe_project_relative_path(value):
        return False
    normalized = value.strip().lower().replace("\\", "/")
    return normalized.endswith((".so", ".o", ".a", ".om", ".bin"))


def _native_artifact_path_candidates(evidence: Mapping[object, object]) -> list[str]:
    candidates: list[str] = []

    for field_name in _ARTIFACT_PATH_FIELDS:
        value = evidence.get(field_name)
        if isinstance(value, str):
            candidates.append(value)
    for field_name in _ARTIFACT_SEQUENCE_FIELDS:
        value = evidence.get(field_name)
        if isinstance(value, (list, tuple, set)):
            items = cast(list[object] | tuple[object, ...] | set[object], value)
            for item in items:
                if isinstance(item, str):
                    candidates.append(item)
    for field_name in _ARTIFACT_NESTED_FIELDS:
        value = evidence.get(field_name)
        if isinstance(value, Mapping):
            candidates.extend(_native_artifact_path_candidates(cast(Mapping[object, object], value)))
    return candidates


def _is_native_compiled_platform_artifact_path(
    value: str,
    platform_policy: PlatformPolicy | None = None,
) -> bool:
    if not _is_safe_project_relative_path(value):
        return False
    normalized = value.strip().lower().replace("\\", "/")
    if not normalized.endswith((".so", ".o", ".a", ".om", ".bin")):
        return False
    return _path_has_platform_artifact_signal(normalized, platform_policy)


def _runtime_loaded_native_artifact_matches(
    evidence: Mapping[object, object],
    native_paths: list[str],
    project_root: Path | None,
) -> bool:
    runtime_paths = _runtime_loaded_path_candidates(evidence)
    if not runtime_paths:
        return False
    for runtime_path in runtime_paths:
        if runtime_path.strip().lower().replace("\\", "/").endswith(".py"):
            continue
        for native_path in native_paths:
            if _paths_match(runtime_path, native_path, project_root):
                return True
    return False


def _runtime_loaded_path_candidates(evidence: Mapping[object, object]) -> list[str]:
    candidates: list[str] = []
    for field_name in _RUNTIME_LOADED_PATH_FIELDS:
        value = evidence.get(field_name)
        if isinstance(value, str):
            candidates.append(value)
    for field_name in _ARTIFACT_NESTED_FIELDS:
        value = evidence.get(field_name)
        if isinstance(value, Mapping):
            candidates.extend(_runtime_loaded_path_candidates(cast(Mapping[object, object], value)))
    return candidates


def _paths_match(runtime_path: str, native_path: str, project_root: Path | None) -> bool:
    if project_root is not None:
        runtime_resolved = _resolve_path_under_project_root(project_root, runtime_path)
        native_resolved = _resolve_path_under_project_root(project_root, native_path)
        if runtime_resolved is not None and native_resolved is not None:
            return runtime_resolved == native_resolved
        if native_resolved is not None and _container_runtime_path_matches_project_relative(runtime_path, native_path):
            return True
        return False
    return _normalize_reported_path(runtime_path) == _normalize_reported_path(native_path)


def _container_runtime_path_matches_project_relative(runtime_path: str, native_path: str) -> bool:
    native_normalized = _normalize_reported_path(native_path)
    if not native_normalized or native_normalized.startswith("/") or ".." in Path(native_normalized).parts:
        return False
    runtime_normalized = runtime_path.strip().replace("\\", "/")
    return runtime_normalized.endswith("/" + native_normalized) or runtime_normalized == native_normalized


def _normalize_reported_path(value: str) -> str:
    return value.strip().replace("\\", "/").lstrip("./")


def _existing_native_artifacts(project_root: Path, native_paths: list[str]) -> list[Path]:
    existing: list[Path] = []
    for native_path in native_paths:
        resolved = _resolve_path_under_project_root(project_root, native_path)
        if resolved is None:
            continue
        if _is_non_empty_compiled_binary(resolved):
            existing.append(resolved)
    return existing


def _resolve_existing_project_root(project_root: str | Path | None, errors: list[str]) -> Path | None:
    if project_root is None:
        return None
    try:
        resolved = Path(project_root).resolve(strict=True)
    except OSError as exc:
        errors.append(f"project_root must exist for custom-op artifact validation: {exc}")
        return None
    if not resolved.is_dir():
        errors.append("project_root must be a directory for custom-op artifact validation")
        return None
    return resolved


def _resolve_path_under_project_root(project_root: Path, value: str) -> Path | None:
    raw = value.strip()
    if not raw:
        return None
    candidate = Path(raw)
    if not candidate.is_absolute():
        if not _is_safe_project_relative_path(raw):
            return None
        candidate = project_root / candidate
    try:
        resolved = candidate.resolve(strict=True)
    except OSError:
        return None
    try:
        _ = resolved.relative_to(project_root)
    except ValueError:
        return None
    return resolved


def _is_non_empty_compiled_binary(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        if path.stat().st_size <= 0:
            return False
        with path.open("rb") as handle:
            prefix = handle.read(4096)
    except OSError:
        return False
    suffix = path.suffix.lower()
    if suffix in {".so", ".o"}:
        return prefix.startswith(b"\x7fELF")
    if suffix == ".a":
        return prefix.startswith(b"!<arch>\n")
    if suffix in {".om", ".bin"}:
        return b"\x00" in prefix or any(byte > 0x7F for byte in prefix)
    return False


def _validate_build_provenance(
    evidence: Mapping[object, object],
    index: int,
    errors: list[str],
    project_root: Path | None,
) -> Path | None:
    provenance = evidence.get("build_provenance")
    if not isinstance(provenance, Mapping):
        errors.append(f"rows[{index}].opp_custom_op_artifact_evidence must include build_provenance with command and project-local log_path")
        return None
    provenance_map = cast(Mapping[object, object], provenance)
    command = provenance_map.get("command") or provenance_map.get("build_command")
    if not isinstance(command, str) or not command.strip():
        errors.append(f"rows[{index}].opp_custom_op_artifact_evidence.build_provenance.command must be non-empty")
    log_path = provenance_map.get("log_path") or provenance_map.get("build_log_path") or provenance_map.get("cann_build_log_path")
    if not isinstance(log_path, str) or not _is_safe_project_relative_path(log_path):
        errors.append(f"rows[{index}].opp_custom_op_artifact_evidence.build_provenance.log_path must be a safe project-relative path")
        return None
    if project_root is not None:
        resolved = _resolve_path_under_project_root(project_root, log_path)
        if resolved is None or not resolved.is_file() or resolved.stat().st_size <= 0:
            errors.append(f"rows[{index}].opp_custom_op_artifact_evidence.build_provenance.log_path must exist under the project root and be non-empty")
            return None
        return resolved
    return None


def _validate_native_platform_evidence(
    evidence: Mapping[object, object],
    row: Mapping[object, object],
    index: int,
    errors: list[str],
    project_root: Path,
    native_artifact_paths: list[Path],
    build_log_path: Path | None,
    platform_policy: PlatformPolicy | None = None,
) -> None:
    """Validate native platform evidence using policy-aware tokens.

    When ``platform_policy`` is None, generic native evidence tokens are used.
    """
    build_log_tokens = get_native_build_log_tokens(platform_policy)
    source_tokens = get_native_source_tokens(platform_policy)
    binary_tokens = get_native_binary_tokens(platform_policy)

    build_log_text = _read_text_limited(build_log_path) if build_log_path is not None else ""
    build_log_has_native_evidence = _text_has_any_token(build_log_text, build_log_tokens)
    binary_has_native_evidence = any(
        _binary_has_platform_native_token(path, binary_tokens) for path in native_artifact_paths
    )
    source_has_native_evidence = _source_evidence_has_platform_native_token(
        project_root, evidence, row, source_tokens
    )
    evidence_only_paths = _evidence_only_native_artifact_labels(evidence, row)

    if platform_policy is not None:
        build_log_error = platform_policy.custom_op_evidence.build_log_error_message
        binary_source_error = platform_policy.custom_op_evidence.binary_source_error_message
    else:
        build_log_error = "must contain build or link evidence, not a stub-only or no-build claim"
        binary_source_error = "must include independent binary or source evidence; a path alone is not sufficient"

    if not build_log_has_native_evidence:
        errors.append(
            f"rows[{index}].opp_custom_op_artifact_evidence.build_provenance.log_path {build_log_error}"
        )
    if evidence_only_paths:
        errors.append(
            f"rows[{index}].opp_custom_op_artifact_evidence must not use evidence-only/stub/native-marker artifact or source paths: "
            + ", ".join(sorted(evidence_only_paths))
        )
    if not (binary_has_native_evidence or source_has_native_evidence):
        errors.append(
            f"rows[{index}].opp_custom_op_artifact_evidence {binary_source_error}"
        )


def _read_text_limited(path: Path | None, limit: int = 1_000_000) -> str:
    if path is None:
        return ""
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            return handle.read(limit)
    except OSError:
        return ""


def _text_has_any_token(text: str, tokens: tuple[str, ...]) -> bool:
    normalized = text.lower()
    return any(token.lower() in normalized for token in tokens)


def _binary_has_platform_native_token(path: Path, tokens: tuple[bytes, ...]) -> bool:
    try:
        if path.stat().st_size > _MAX_NATIVE_ARTIFACT_SCAN_BYTES:
            return False
        overlap = b""
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(_BINARY_SCAN_CHUNK_BYTES)
                if not chunk:
                    return False
                window = (overlap + chunk).lower()
                if any(token.lower() in window for token in tokens):
                    return True
                overlap = window[-128:]
    except OSError:
        return False


def _source_evidence_has_platform_native_token(
    project_root: Path,
    evidence: Mapping[object, object],
    row: Mapping[object, object],
    tokens: tuple[str, ...],
) -> bool:
    for raw_path in _source_path_candidates(evidence, row):
        resolved = _resolve_path_under_project_root(project_root, raw_path)
        if resolved is None or not resolved.is_file():
            continue
        if _text_has_any_token(_read_text_limited(resolved), tokens):
            return True
    return False


def _source_path_candidates(evidence: Mapping[object, object], row: Mapping[object, object]) -> list[str]:
    candidates: list[str] = []
    for container in (evidence, row):
        for field_name in (
            "source_evidence",
            "source_paths",
            "native_source_paths",
            "build_source_paths",
            "kernel_source_paths",
            "op_host_sources",
            "op_kernel_sources",
            "op_proto_sources",
        ):
            value = container.get(field_name)
            if isinstance(value, str):
                candidates.append(_strip_source_location(value))
            elif isinstance(value, (list, tuple, set)):
                for item in cast(list[object] | tuple[object, ...] | set[object], value):
                    if isinstance(item, str):
                        candidates.append(_strip_source_location(item))
    return candidates


def _evidence_only_native_artifact_labels(
    evidence: Mapping[object, object],
    row: Mapping[object, object],
) -> set[str]:
    labels: set[str] = set()
    for raw_path in _native_artifact_path_candidates(evidence):
        if _path_basename_has_token(raw_path, _EVIDENCE_ONLY_PATH_TOKENS):
            labels.add(_normalize_reported_path(raw_path))
    for raw_path in _runtime_loaded_path_candidates(evidence):
        if _path_basename_has_token(raw_path, _EVIDENCE_ONLY_PATH_TOKENS):
            labels.add(_normalize_reported_path(raw_path))
    for raw_path in _source_path_candidates(evidence, row):
        if _path_basename_has_token(raw_path, _EVIDENCE_ONLY_SOURCE_TOKENS):
            labels.add(_normalize_reported_path(raw_path))

    provenance = evidence.get("build_provenance")
    if isinstance(provenance, Mapping):
        provenance_map = cast(Mapping[object, object], provenance)
        command = provenance_map.get("command") or provenance_map.get("build_command")
        if isinstance(command, str) and _text_mentions_evidence_only_path(command):
            labels.add("build_provenance.command")
    return labels


def _path_basename_has_token(path_value: str, tokens: tuple[str, ...]) -> bool:
    basename = Path(path_value.strip().replace("\\", "/")).name.lower()
    if not basename:
        return False
    return any(token in basename for token in tokens)


def _text_mentions_evidence_only_path(value: str) -> bool:
    normalized = value.strip().lower().replace("\\", "/")
    for suffix in (".so", ".o", ".a", ".om", ".bin", ".c", ".cc", ".cpp", ".cxx", ".h", ".hpp", ".cu", ".cuh"):
        for token in _EVIDENCE_ONLY_SOURCE_TOKENS:
            if f"{token}{suffix}" in normalized or f"_{token}{suffix}" in normalized or f"-{token}{suffix}" in normalized:
                return True
    return False


def _strip_source_location(value: str) -> str:
    normalized = value.strip()
    if ":" not in normalized:
        return normalized
    path_part, line_part = normalized.rsplit(":", 1)
    if line_part.isdigit():
        return path_part
    prefix = normalized.split(":", 1)[0]
    if prefix.endswith((".c", ".cc", ".cpp", ".cxx", ".h", ".hpp", ".cu", ".cuh", ".py")):
        return prefix
    return normalized


def _is_safe_project_relative_path(value: str) -> bool:
    raw = value.strip()
    if not raw:
        return False
    normalized = raw.replace("\\", "/")
    lowered = normalized.lower()
    if normalized.startswith(("/", "~")):
        return False
    if "://" in normalized or lowered.startswith(("file:", "http:", "https:", "s3:", "gs:")):
        return False
    if len(normalized) >= 2 and normalized[1] == ":" and normalized[0].isalpha():
        return False
    parts = [part for part in normalized.split("/") if part not in ("", ".")]
    return bool(parts) and ".." not in parts


def _path_has_platform_artifact_signal(
    normalized_path: str,
    platform_policy: PlatformPolicy | None = None,
) -> bool:
    padded = f"/{normalized_path}"
    tokens = get_artifact_path_tokens(platform_policy)
    return any(token in padded for token in tokens)


def _validate_baseline_and_custom_device_proof(
    evidence: Mapping[object, object],
    label: str,
    errors: list[str],
    platform_policy: PlatformPolicy | None = None,
    require_strict_native_producer: bool = False,
) -> None:
    if _has_diagnostic_baseline(evidence):
        errors.append(f"{label} must not use diagnostic-only or metadata-only baseline timings")
    perf_mode = get_performance_validation_mode(platform_policy)
    if perf_mode == "disabled":
        return
    effective_policy = platform_policy
    if not _has_baseline_proof(evidence, effective_policy):
        if effective_policy is None:
            errors.append(f"{label} must prove timings include a real CPU baseline path")
        else:
            baseline_devices = get_performance_baseline_device_values(effective_policy)
            errors.append(f"{label} must prove timings include a baseline path ({', '.join(sorted(baseline_devices))})")
    if not _has_target_device_custom_proof(evidence, effective_policy):
        errors.append(f"{label} must prove timings include a target-device custom-op path")
    if require_strict_native_producer:
        if _has_self_or_same_route_baseline(evidence, platform_policy):
            errors.append(f"{label} must not use self-baseline, same-route, or same-NPU placeholder timings; compare baseline runtime against target native custom-op runtime")
        _validate_independent_performance_measurement(
            evidence, label, errors, require_strict_native_producer
        )
        _validate_speedup_formula(evidence, label, errors)


def _validate_independent_performance_measurement(
    evidence: Mapping[object, object],
    label: str,
    errors: list[str],
    require_strict_native_producer: bool,
) -> None:
    if not require_strict_native_producer:
        return
    if not _has_positive_measurement_iterations(evidence):
        errors.append(f"{label} must include positive measured iteration counts for real performance evidence")
    if _has_zero_measurement_iterations(evidence):
        errors.append(f"{label} must not report zero measurement iterations for performance evidence")
    baseline = evidence.get("baseline_seconds") or evidence.get("overall_baseline_seconds")
    custom = evidence.get("custom_seconds") or evidence.get("overall_custom_seconds")
    if _positive_number(baseline) and _positive_number(custom):
        if abs(cast(float, baseline) - cast(float, custom)) <= 1e-12:
            errors.append(f"{label} must not copy identical baseline_seconds and custom_seconds; measure baseline and target custom-op runtime independently")


def _has_positive_measurement_iterations(evidence: Mapping[object, object]) -> bool:
    for field_name in (
        "measure_iterations",
        "measured_iterations",
        "iterations",
        "timing_iterations",
        "baseline_measure_iterations",
        "custom_measure_iterations",
        "overall_measure_iterations",
    ):
        value = evidence.get(field_name)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return True
    return False


def _has_zero_measurement_iterations(evidence: Mapping[object, object]) -> bool:
    for field_name in (
        "measure_iterations",
        "measured_iterations",
        "iterations",
        "timing_iterations",
        "baseline_measure_iterations",
        "custom_measure_iterations",
        "overall_measure_iterations",
    ):
        value = evidence.get(field_name)
        if isinstance(value, int) and not isinstance(value, bool) and value <= 0:
            return True
    return False


def _normalize_token(value: str) -> str:
    return value.strip().lower().replace("-", "_").replace(" ", "_").replace(".", "_")


def _has_self_or_same_route_baseline(evidence: Mapping[object, object], platform_policy: PlatformPolicy | None = None) -> bool:
    baseline_flags: list[str] = [
        "self_baseline",
        "same_route_baseline",
        "baseline_is_custom",
        "custom_as_baseline",
        "placeholder_speedup",
    ]
    if platform_policy is not None:
        prefix = platform_policy.id.split("_")[0]  # "npu_ascend" → "npu"
        baseline_flags.append(f"same_{prefix}_baseline")  # e.g. "same_npu_baseline", "same_ppu_baseline"
    else:
        baseline_flags.append("same_npu_baseline")  # legacy fallback
    if any(evidence.get(flag_name) is True for flag_name in baseline_flags):
        return True
    baseline = _normalized_device_value(evidence, ("baseline_device", "baseline_backend", "source_device", "overall_baseline_device", "baseline_route"))
    custom = _normalized_device_value(evidence, ("custom_device", "custom_backend", "target_device", "overall_custom_device", "custom_route"))
    if baseline and custom and baseline == custom:
        return True
    if baseline and custom and platform_policy is not None and _is_policy_target_device(baseline, platform_policy) and _is_policy_target_device(custom, platform_policy):
        return True
    baseline_route = _normalized_device_value(evidence, ("baseline_route", "comparison_route", "baseline_mode"))
    custom_route = _normalized_device_value(evidence, ("custom_route", "target_route", "route"))
    return bool(baseline_route and custom_route and baseline_route == custom_route)


def _validate_speedup_formula(evidence: Mapping[object, object], label: str, errors: list[str]) -> None:
    baseline = evidence.get("baseline_seconds") or evidence.get("overall_baseline_seconds")
    custom = evidence.get("custom_seconds") or evidence.get("overall_custom_seconds")
    speedup = evidence.get("speedup_vs_baseline") or evidence.get("overall_speedup_vs_baseline")
    if not (_positive_number(baseline) and _positive_number(custom) and _positive_number(speedup)):
        return
    expected = cast(float, baseline) / cast(float, custom)
    actual = cast(float, speedup)
    tolerance = max(1e-6, abs(expected) * 0.02)
    if abs(actual - expected) > tolerance:
        errors.append(f"{label} speedup_vs_baseline must approximately equal baseline_seconds / custom_seconds for baseline versus target native custom-op runtime")


def _normalized_device_value(evidence: Mapping[object, object], fields: tuple[str, ...]) -> str:
    for field_name in fields:
        value = evidence.get(field_name)
        if isinstance(value, str) and value.strip():
            return value.strip().lower().replace("-", "_").replace(" ", "_").replace(".", "_")
    return ""


def _is_policy_target_device(value: str, platform_policy: PlatformPolicy) -> bool:
    accepted = set(get_target_device_values(platform_policy))
    if not accepted:
        return False
    normalized = _normalize_token(value)
    for accepted_value in accepted:
        accepted_normalized = _normalize_token(accepted_value)
        if normalized == accepted_normalized:
            return True
        if normalized.startswith((f"{accepted_normalized}:", f"{accepted_normalized}_")):
            return True
    return False


def _has_diagnostic_baseline(evidence: Mapping[object, object]) -> bool:
    if any(evidence.get(flag_name) is True for flag_name in ("diagnostic_only", "baseline_diagnostic_only", "baseline_missing", "cuda_baseline_missing")):
        return True
    for field_name in ("baseline_mode", "baseline_source", "comparison_mode", "mode"):
        value = evidence.get(field_name)
        if isinstance(value, str):
            normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
            if normalized in DIAGNOSTIC_BASELINE_VALUES:
                return True
    return False


def _has_baseline_proof(evidence: Mapping[object, object], platform_policy: PlatformPolicy | None = None) -> bool:
    if platform_policy is None:
        boolean_fields = _GENERIC_EVIDENCE.performance_baseline_boolean_fields
        if _has_positive_boolean(evidence, tuple(boolean_fields)):
            return True
        baseline_devices = set(_GENERIC_EVIDENCE.performance_baseline_device_values)
        return _has_device_value(evidence, ("baseline_device", "baseline_backend", "source_device", "overall_baseline_device"), baseline_devices, None)
    boolean_fields = get_performance_baseline_boolean_fields(platform_policy)
    if _has_positive_boolean(evidence, tuple(boolean_fields)):
        return True
    baseline_devices = get_performance_baseline_device_values(platform_policy)
    return _has_device_value(evidence, ("baseline_device", "baseline_backend", "source_device", "overall_baseline_device"), baseline_devices, platform_policy)


def _has_target_device_custom_proof(
    evidence: Mapping[object, object],
    platform_policy: PlatformPolicy | None = None,
) -> bool:
    if platform_policy is None:
        target_device_values = set(get_target_device_values(None))
        positive_boolean_fields = get_positive_boolean_fields(None)
        if _has_positive_boolean(evidence, tuple(positive_boolean_fields)):
            return True
        return _has_device_value(
            evidence,
            ("custom_device", "custom_backend", "target_device", "overall_custom_device"),
            target_device_values,
            platform_policy,
        )
    target_device_values = set(get_target_device_values(platform_policy))
    positive_boolean_fields = get_positive_boolean_fields(platform_policy)
    if _has_positive_boolean(evidence, tuple(positive_boolean_fields)):
        return True
    return _has_device_value(
        evidence,
        ("custom_device", "custom_backend", "target_device", "overall_custom_device"),
        target_device_values,
        platform_policy,
    )


def _has_device_value(evidence: Mapping[object, object], fields: tuple[str, ...], accepted: set[str], platform_policy: PlatformPolicy | None = None) -> bool:
    for field_name in fields:
        value = evidence.get(field_name)
        if isinstance(value, str):
            normalized = value.strip().lower().replace("-", "_").replace(" ", "_").replace(".", "_")
            for accepted_value in accepted:
                if normalized == accepted_value:
                    return True
                if normalized.startswith((f"{accepted_value}:", f"{accepted_value}_")):
                    return True
            if platform_policy is not None and platform_policy.custom_op_evidence.target_device_values:
                for prefix in platform_policy.custom_op_evidence.target_device_values:
                    if normalized.startswith(f"{prefix}_") and prefix in accepted:
                        return True
            if any(normalized.startswith(accepted_value) for accepted_value in accepted):
                return True
    return False


def _extract_custom_call_count(row: Mapping[object, object]) -> int | None:
    coverage = row.get("same_run_runtime_coverage")
    candidates: list[object] = []
    if isinstance(coverage, Mapping):
        coverage_map = cast(Mapping[object, object], coverage)
        candidates.append(coverage_map.get("custom_call_count"))
    candidates.extend((row.get("runtime_call_count"), row.get("custom_call_count")))
    for candidate in candidates:
        if isinstance(candidate, int) and not isinstance(candidate, bool):
            return candidate
        if isinstance(candidate, str):
            try:
                return int(candidate.strip())
            except ValueError:
                continue
    return None


def _has_negative_contamination_signal(value: object) -> bool:
    if not isinstance(value, Mapping):
        return False
    evidence = cast(Mapping[object, object], value)
    if evidence.get("passed") is False:
        return True
    if evidence.get("not_checked") is True:
        return True
    negative_fields = (
        "fallback_detected",
        "zero_call_detected",
        "builtin_contamination_detected",
        "baseline_only_detected",
        "stub_detected",
    )
    if any(evidence.get(field_name) is True for field_name in negative_fields):
        return True
    perf_value = evidence.get("speedup_vs_baseline")
    return isinstance(perf_value, (int, float)) and not isinstance(perf_value, bool) and perf_value < 0
