"""Validation for Phase 5 final validation output."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from core.validator_engine import ValidationDict
from core.platform_policy import (
    PlatformPolicy,
    get_artifact_path_tokens,
    get_native_build_log_tokens,
    get_native_source_tokens,
    get_native_binary_tokens,
    get_target_device_values,
    get_positive_boolean_fields,
)

PASS_STATES = {"PASS", "FULL_PASS", "DONE", "CLOSED_PASS"}
EVIDENCE_PASS_STATES = PASS_STATES | {"PASSED", "SUCCESS", "OK", "VERIFIED"}

BLOCKING_STATUSES = {
    "MVP_ONLY",
    "SMOKE_ONLY",
    "PARTIAL",
    "DIRECT_ONLY",
    "ARTIFACT_ONLY",
    "INCOMPLETE",
    "FAILED",
    "BLOCKED",
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

ASCEND_NATIVE_ARTIFACT_FIELDS = (
    "ascend_custom_op_artifact",
    "ascend_custom_op_built",
    "native_custom_op_artifact",
    "opp_custom_op_built",
    "op_plugin_built",
    "cann_build_log_present",
    "ascendc_kernel_built",
    "tiling_kernel_built",
    "acl_op_registered",
    "aclnn_op_registered",
    "torch_npu_custom_op_loaded",
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


def validate_custom_op_final_gate(
    data: dict[str, object],
    project_root: str | Path | None = None,
    platform_policy: PlatformPolicy | None = None,
) -> ValidationDict:
    """Validate the machine-checkable custom-op final evidence gate report.

    Args:
        data: The custom-op final gate report as a dict.
        project_root: Path to the project root for file validation.
        platform_policy: Optional platform policy; when None, defaults to
            legacy NPU/Ascend behaviour for backward compatibility.
    """
    errors: list[str] = []
    resolved_project_root = _resolve_existing_project_root(project_root, errors)

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
            _validate_gate_row(row, index, errors, resolved_project_root, platform_policy)
        _validate_source_inventory_completeness(cast(Mapping[object, object], data), row_items, errors)
        _validate_performance_report_completeness(
            cast(Mapping[object, object], data), row_items, manifest_entries, errors, platform_policy
        )
        _validate_required_manifest_units(
            row_items,
            inventory_count,
            manifest_entries,
            closed_pass_entries,
            resolved_project_root,
            errors,
        )

    return {"passed": not errors, "errors": errors, "warnings": []}


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
) -> None:
    status = row.get("status")
    _reject_blocking_status(status, f"rows[{index}].status", errors)
    if _normalize_status(status) not in PASS_STATES:
        errors.append(f"rows[{index}].status must be a pass state")

    for field_name in REQUIRED_ROW_EVIDENCE_FIELDS:
        if field_name == "no_fallback_no_zero_call_no_builtin_contamination":
            continue
        if not _has_evidence(row.get(field_name)):
            errors.append(f"rows[{index}].{field_name} must contain evidence")

    _validate_project_local_artifact(row.get("opp_custom_op_artifact_evidence"), row, index, errors, project_root, platform_policy)
    _validate_integration_route(row.get("integration_e2e_evidence"), index, errors)
    _validate_runtime_coverage(row.get("same_run_runtime_coverage"), index, errors)
    _validate_performance(row.get("performance_evidence"), index, errors, platform_policy)
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
    )
    for field_name in positive_numeric_fields:
        value = evidence.get(field_name)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
            return True
    return False


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
                    entry["name"] = entry.get("name", key)
                    entries.append(entry)
    elif isinstance(value, list):
        entries = cast(list[object], value)

    for item in entries:
        if not isinstance(item, Mapping):
            continue
        entry = cast(Mapping[object, object], item)
        name = _first_string_field(entry, ("name", "operator", "op_name", "row_id", "id"))
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
    return _first_string_field(row, ("name", "operator", "op_name", "row_id", "id"))


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
        if platform_policy is not None and platform_policy.id != "npu_ascend":
            native_label = platform_policy.guidance_native_label
            errors.append(f"rows[{index}].opp_custom_op_artifact_evidence must prove a native compiled {native_label} custom-op artifact")
        else:
            errors.append(f"rows[{index}].opp_custom_op_artifact_evidence must prove a native compiled Ascend custom-op artifact")
    elif not _runtime_loaded_native_artifact_matches(evidence, native_paths, project_root):
        errors.append(f"rows[{index}].opp_custom_op_artifact_evidence must prove the same-run runtime loaded the native compiled artifact, not a Python shim")

    existing_paths: list[Path] = []
    if project_root is not None and native_paths:
        existing_paths = _existing_native_artifacts(project_root, native_paths)
        if not existing_paths:
            errors.append(f"rows[{index}].opp_custom_op_artifact_evidence native artifact path must exist under the project root and be a non-empty compiled binary")

    build_log_path = _validate_build_provenance(evidence, index, errors, project_root)
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
) -> None:
    if not isinstance(value, Mapping):
        errors.append(f"rows[{index}].performance_evidence must be an object with numeric timings")
        return
    evidence = cast(Mapping[object, object], value)
    if _mapping_is_disallowed_surrogate(evidence):
        errors.append(f"rows[{index}].performance_evidence must not be report-only or benchmark-only without project API proof")
    required_positive = ("baseline_seconds", "custom_seconds", "speedup_vs_baseline")
    missing = [field_name for field_name in required_positive if not _positive_number(evidence.get(field_name))]
    if missing:
        errors.append(f"rows[{index}].performance_evidence missing positive numeric fields: " + ", ".join(missing))
    if not _has_positive_boolean(evidence, ("project_api_invoked", "public_api_invoked", "custom_op_route_executed")):
        errors.append(f"rows[{index}].performance_evidence must prove timing came from public/project API custom-op route")
    _validate_baseline_and_custom_device_proof(evidence, f"rows[{index}].performance_evidence", errors, platform_policy)


def _validate_performance_report_completeness(
    data: Mapping[object, object],
    row_items: list[object],
    manifest_entries: int | None,
    errors: list[str],
    platform_policy: PlatformPolicy | None = None,
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
    _validate_baseline_and_custom_device_proof(report_map, "performance_report", errors, platform_policy)
    _validate_overall_performance_report(report_map, errors)

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
            _validate_performance_report_entry(entry, unit_name, errors, platform_policy)


def _validate_overall_performance_report(report: Mapping[object, object], errors: list[str]) -> None:
    required_positive = (
        "overall_baseline_seconds",
        "overall_custom_seconds",
        "overall_speedup_vs_baseline",
    )
    missing = [field_name for field_name in required_positive if not _positive_number(report.get(field_name))]
    if missing:
        errors.append("performance_report missing positive overall speedup fields: " + ", ".join(missing))

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
) -> None:
    required_positive = ("baseline_seconds", "custom_seconds", "speedup_vs_baseline")
    missing = [field_name for field_name in required_positive if not _positive_number(entry.get(field_name))]
    if missing:
        errors.append(f"performance_report.entries[{unit_name}] missing positive numeric fields: " + ", ".join(missing))
    if not _has_positive_boolean(entry, ("project_api_invoked", "public_api_invoked", "custom_op_route_executed")):
        errors.append(f"performance_report.entries[{unit_name}] must prove public/project API custom-op timing route")
    _validate_baseline_and_custom_device_proof(entry, f"performance_report.entries[{unit_name}]", errors, platform_policy)


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

_NATIVE_BUILD_LOG_TOKENS = (
    "aclrt",
    "aclnn",
    "acl_op",
    "-lascendcl",
    "libascendcl",
    "libacl",
    "kernel_operator.h",
    "op_host",
    "op_kernel",
    "op_proto",
    "msopgen",
    "tikcpp",
    "aicore",
    "aicpu",
)

_NATIVE_SOURCE_TOKENS = (
    "kernel_operator.h",
    "aclrt",
    "aclnn",
    "acl_op",
    "op_host",
    "op_kernel",
    "op_proto",
    "tilingdata",
    "getblockidx",
    "aicore",
    "aicpu",
)

_NATIVE_BINARY_TOKENS = (
    b"aclrt",
    b"aclnn",
    b"acl_op",
    b"kernel_operator",
    b"libascendcl",
    b"libacl",
    b"op_host",
    b"op_kernel",
    b"op_proto",
    b"aicore",
    b"aicpu",
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


def _native_compiled_artifact_paths(
    evidence: Mapping[object, object],
    platform_policy: PlatformPolicy | None = None,
) -> list[str]:
    return [
        value
        for value in _native_artifact_path_candidates(evidence)
        if _is_native_compiled_platform_artifact_path(value, platform_policy)
    ]


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


def _is_native_compiled_ascend_artifact_path(value: str) -> bool:
    """Legacy NPU-only check.  Prefer ``_is_native_compiled_platform_artifact_path``."""
    if not _is_safe_project_relative_path(value):
        return False
    normalized = value.strip().lower().replace("\\", "/")
    if not normalized.endswith((".so", ".o", ".a", ".om", ".bin")):
        return False
    return _path_has_ascend_artifact_signal(normalized)


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
        return runtime_resolved is not None and native_resolved is not None and runtime_resolved == native_resolved
    return _normalize_reported_path(runtime_path) == _normalize_reported_path(native_path)


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

    When ``platform_policy`` is None, falls back to legacy NPU/CANN behaviour.
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
        build_log_error = "must contain CANN/ACL/AscendC/OPP build or link evidence, not a torch-only extension build"
        binary_source_error = "must include independent CANN/ACL/AscendC binary or source evidence; an ELF under an Ascend-looking path is not sufficient"

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


# Keep legacy name as an alias for backward compatibility
def _validate_native_cann_evidence(
    evidence: Mapping[object, object],
    row: Mapping[object, object],
    index: int,
    errors: list[str],
    project_root: Path,
    native_artifact_paths: list[Path],
    build_log_path: Path | None,
) -> None:
    _validate_native_platform_evidence(
        evidence, row, index, errors, project_root,
        native_artifact_paths, build_log_path, platform_policy=None,
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


def _binary_has_native_token(path: Path) -> bool:
    """Legacy NPU-only check. Prefer ``_binary_has_platform_native_token``."""
    return _binary_has_platform_native_token(path, _NATIVE_BINARY_TOKENS)


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


def _source_evidence_has_native_token(
    project_root: Path,
    evidence: Mapping[object, object],
    row: Mapping[object, object],
) -> bool:
    """Legacy NPU-only check. Prefer ``_source_evidence_has_platform_native_token``."""
    return _source_evidence_has_platform_native_token(
        project_root, evidence, row, _NATIVE_SOURCE_TOKENS
    )


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


def _path_has_ascend_artifact_signal(normalized_path: str) -> bool:
    padded = f"/{normalized_path}"
    path_tokens = (
        "/opp/",
        "/op_plugin",
        "ascend",
        "cann",
        "acl",
        "aclnn",
        "aicpu",
        "ascendc",
        "custom_op",
        "torch_npu",
    )
    return any(token in padded for token in path_tokens)


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
) -> None:
    if _has_diagnostic_baseline(evidence):
        errors.append(f"{label} must not use diagnostic-only or metadata-only baseline timings")
    if not _has_cuda_baseline_proof(evidence):
        errors.append(f"{label} must prove timings include a CUDA baseline path")
    if not _has_target_device_custom_proof(evidence, platform_policy):
        errors.append(f"{label} must prove timings include a target-device custom-op path")


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


def _has_cuda_baseline_proof(evidence: Mapping[object, object]) -> bool:
    if _has_positive_boolean(evidence, ("cuda_baseline", "baseline_cuda", "cuda_baseline_invoked", "baseline_cuda_invoked")):
        return True
    return _has_device_value(evidence, ("baseline_device", "baseline_backend", "source_device", "overall_baseline_device"), {"cuda", "gpu", "torch_cuda"})


def _has_target_device_custom_proof(
    evidence: Mapping[object, object],
    platform_policy: PlatformPolicy | None = None,
) -> bool:
    target_device_values = set(get_target_device_values(platform_policy))
    positive_boolean_fields = get_positive_boolean_fields(platform_policy)
    if _has_positive_boolean(evidence, tuple(positive_boolean_fields)):
        return True
    return _has_device_value(
        evidence,
        ("custom_device", "custom_backend", "target_device", "overall_custom_device"),
        target_device_values,
    )


def _has_device_value(evidence: Mapping[object, object], fields: tuple[str, ...], accepted: set[str]) -> bool:
    for field_name in fields:
        value = evidence.get(field_name)
        if isinstance(value, str):
            normalized = value.strip().lower().replace("-", "_").replace(" ", "_").replace(".", "_")
            for accepted_value in accepted:
                if normalized == accepted_value:
                    return True
                if normalized.startswith((f"{accepted_value}:", f"{accepted_value}_")):
                    return True
            if "ascend" in accepted and normalized.startswith("ascend"):
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
