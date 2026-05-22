"""Validation for Phase 3 entry script output."""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import cast

from core.validator_engine import ValidationDict

CUSTOM_OP_FIELDS = {
    "entry_script_kind",
    "reports_dir",
    "required_report_paths",
    "required_checks",
    "operator_discovery_sources",
    "operator_inventory_schema",
    "validation_obligations",
    "expanded_variant_inventory",
    "variant_axis_coverage",
    "per_variant_performance_report",
}

EXPANDED_VARIANT_FIELDS = {
    "expanded_variant_inventory",
    "variant_axis_coverage",
    "per_variant_performance_report",
}

REQUIRED_VARIANT_CHECKS = {
    "expanded_variant_inventory",
    "variant_axis_coverage",
    "per_variant_performance_report",
}

REQUIRED_DISCOVERY_SOURCES = {
    "source",
    "bindings",
    "wrappers",
    "autograd",
    "aliases",
    "launch",
    "setup",
    "tests",
}

REQUIRED_VALIDATION_OBLIGATIONS = {
    "project_local_artifact",
    "strict_opp_artifact",
    "op_host_op_kernel_source",
    "cann_opp_build_install",
    "generated_opp_package_artifacts",
    "reject_npuextension_aten_only",
    "reject_non_opp_producer_evidence",
    "project_root_artifact_existence",
    "runtime_project_api",
    "per_row_public_or_framework_route_evidence",
    "reject_direct_builtin_only_routes",
    "numeric_performance",
    "complete_speedup_report",
    "overall_speedup_report",
    "final_chinese_per_row_table",
    "no_fallback",
}

REQUIRED_INVENTORY_SCHEMA_FIELDS = {
    "semantic_rows",
    "fine_grained_operator_units",
    "unit_identity",
    "variant_or_signature",
    "native_operator_symbols",
    "kernel_functions",
    "kernel_launch_sites",
    "public_entry_mapping",
    "candidate_public_api_routes",
    "candidate_framework_integration_routes",
    "route_evidence_fields",
    "source_evidence",
    "inventory_granularity",
    "out_of_scope_source_groups",
}

REQUIRED_CHECKS = {
    "inventory_manifest_equality",
    "closed_pass_count_equals_manifest_entries",
    "remaining_entries_zero",
    "full_migration_status_full_pass",
    "fine_grained_operator_unit_inventory",
    "kernel_launch_site_inventory",
    "public_entry_mapping",
    "inventory_granularity_fine",
    "per_entry_opp_custom_op_artifact_evidence",
    "per_entry_adapter_evidence",
    "per_entry_parity_evidence",
    "integration_e2e_evidence",
    "per_entry_public_api_or_framework_integration_route_evidence",
    "correlate_route_evidence_to_manifest_rows",
    "reject_direct_or_builtin_only_routes",
    "same_run_runtime_coverage",
    "performance_evidence",
    "complete_performance_report",
    "overall_speedup_report",
    "strict_ascend_c_cann_opp_artifacts",
    "op_host_op_kernel_source_evidence",
    "cann_opp_build_install_provenance",
    "generated_opp_package_artifacts",
    "reject_npuextension_aten_only_as_opp_evidence",
    "reject_non_opp_producer_evidence",
    "project_root_artifact_existence",
    "final_chinese_per_row_table_parity",
    "no_fallback_no_zero_call_no_builtin_contamination",
    "native_operator_symbol_inventory",
}

REQUIRED_REPORT_TOKENS = (
    "inventory",
    "manifest",
    "preflight",
    "baseline",
    "runtime_coverage",
    "performance",
    "build",
    "implementation_resolution",
    "custom_op_final_gate",
    "evidence_validation",
    "summary",
)

PARTIAL_SUCCESS_TERMS = (
    "smoke",
    "mvp",
    "minimal",
    "partial",
    "direct_only",
    "artifact_only",
    "compile_only",
    "sample",
)

BENCHMARK_ONLY_TERMS = (
    "benchmark-only",
    "benchmark_only",
    "benchmark only",
    "--benchmark-only",
    "--benchmark_only",
    "benchmark_only=true",
    "benchmark-only=true",
)

REPORT_ONLY_VALIDATOR_TERMS = (
    "final_evidence_validate.py",
    "final_evidence_validator.py",
    "validate_final_evidence.py",
    "report_only",
    "manifest_only",
)

REPORT_ONLY_ENTRY_PATH_TERMS = (
    "migration_reports/final_evidence_validate.py",
    "migration_reports/final_evidence_validator.py",
    "migration_reports/validate_final_evidence.py",
)

UNSAFE_RUN_COMMAND_CONTROLS = ("&&", "||", ";", "|", "`", "$(", ">", "<", "\n", "\r", "&")
UNSAFE_RUN_COMMAND_EXECUTORS = {"bash", "sh", "zsh", "fish", "source", "."}
ENV_EXECUTORS = {"env"}


def validate(data: dict[str, object]) -> ValidationDict:
    errors: list[str] = []

    entry_script_path = data.get("entry_script_path")
    if not isinstance(entry_script_path, str) or not entry_script_path.strip():
        errors.append("entry_script_path must be a non-empty string")

    run_command = data.get("run_command")
    if not isinstance(run_command, str) or not run_command.strip():
        errors.append("run_command must be a non-empty string")
    else:
        _reject_unsafe_run_command(run_command, errors)

    _reject_report_only_entry_target(entry_script_path, run_command, errors)
    _reject_benchmark_only_target(entry_script_path, run_command, errors)

    if _has_custom_op_contract(data):
        _validate_custom_op_contract(data, errors)

    return {"passed": not errors, "errors": errors, "warnings": []}


def _has_custom_op_contract(data: dict[str, object]) -> bool:
    return any(field in data for field in CUSTOM_OP_FIELDS)


def _validate_custom_op_contract(data: dict[str, object], errors: list[str]) -> None:
    project_root = _validate_custom_op_project_root(data.get("project_dir"), errors)

    _require_existing_custom_op_entry_script(
        data.get("entry_script_path"),
        data.get("reports_dir"),
        project_root,
        errors,
    )
    _validate_custom_op_run_command_project_local(
        data.get("run_command"),
        data.get("entry_script_path"),
        project_root,
        errors,
    )

    entry_script_kind = data.get("entry_script_kind")
    if entry_script_kind != "custom_op_full_validation":
        errors.append("entry_script_kind must be 'custom_op_full_validation' for custom-op contracts")

    reports_dir = data.get("reports_dir")
    if not isinstance(reports_dir, str) or not reports_dir.strip():
        errors.append("reports_dir must be a non-empty string for custom-op contracts")
    elif "migration_reports" not in reports_dir:
        errors.append("reports_dir must point to the target project's migration_reports directory")
    elif project_root is not None:
        _validate_custom_op_reports_dir(reports_dir, project_root, errors)

    required_report_paths = _string_list(data.get("required_report_paths"))
    if required_report_paths is None or not required_report_paths:
        errors.append("required_report_paths must list migration report obligations for custom-op contracts")
    else:
        missing_report_tokens = [
            token for token in REQUIRED_REPORT_TOKENS if not _contains_token(required_report_paths, token)
        ]
        if missing_report_tokens:
            errors.append(
                "required_report_paths must cover report categories: " + ", ".join(missing_report_tokens)
            )

    required_checks = _string_list(data.get("required_checks"))
    if required_checks is None or not required_checks:
        errors.append("required_checks must list full-validation checks for custom-op contracts")
    else:
        normalized_checks = {_normalize_check(check) for check in required_checks}
        missing_checks = sorted(REQUIRED_CHECKS - normalized_checks)
        if missing_checks:
            errors.append("required_checks missing custom-op full-validation checks: " + ", ".join(missing_checks))
        if _contains_partial_success_terms(required_checks):
            errors.append("required_checks must enforce full validation, not smoke/MVP/partial-only success")

    inventory_schema = data.get("operator_inventory_schema")
    if not isinstance(inventory_schema, dict):
        errors.append("operator_inventory_schema must describe semantic rows, native symbols, kernels, source evidence, and out-of-scope groups")
    else:
        schema_dict = cast(dict[str, object], inventory_schema)
        normalized_schema_fields = {_normalize_check(key) for key in schema_dict}
        missing_schema_fields = sorted(REQUIRED_INVENTORY_SCHEMA_FIELDS - normalized_schema_fields)
        if missing_schema_fields:
            errors.append("operator_inventory_schema missing required fields: " + ", ".join(missing_schema_fields))

    discovery_sources = _string_list(data.get("operator_discovery_sources"))
    if discovery_sources is None or not discovery_sources:
        errors.append("operator_discovery_sources must list source discovery obligations for custom-op contracts")
    else:
        normalized_sources = {_normalize_check(source) for source in discovery_sources}
        missing_sources = sorted(REQUIRED_DISCOVERY_SOURCES - normalized_sources)
        if missing_sources:
            errors.append("operator_discovery_sources missing required sources: " + ", ".join(missing_sources))
        if "requirements_doc" in normalized_sources:
            errors.append("operator_discovery_sources must be source-driven and must not include requirements_doc as a completion source")

    validation_obligations = _string_list(data.get("validation_obligations"))
    if validation_obligations is None or not validation_obligations:
        errors.append("validation_obligations must list runtime validation obligations for custom-op contracts")
    else:
        normalized_obligations = {_normalize_check(obligation) for obligation in validation_obligations}
        missing_obligations = _missing_required_obligations(normalized_obligations)
        if missing_obligations:
            errors.append("validation_obligations missing required obligations: " + ", ".join(missing_obligations))
        if _contains_partial_success_terms(validation_obligations):
            errors.append("validation_obligations must enforce full validation, not smoke/MVP/partial-only success")

    revision_allowed = data.get("phase5_entry_script_revision_allowed")
    if not isinstance(revision_allowed, bool):
        errors.append("phase5_entry_script_revision_allowed must be a boolean for custom-op contracts")

    _validate_expanded_variant_contract(data, required_checks, errors)

    _reject_partial_contract_text(data, errors)


def _validate_expanded_variant_contract(data: dict[str, object], required_checks: list[str] | None, errors: list[str]) -> None:
    active = any(field in data for field in EXPANDED_VARIANT_FIELDS)
    if not active:
        return
    inventory = data.get("expanded_variant_inventory")
    if not isinstance(inventory, dict):
        errors.append("expanded_variant_inventory must describe active expanded variant unit identities")
    else:
        inventory_dict = cast(dict[str, object], inventory)
        units = _string_list(inventory_dict.get("unit_identities"))
        if units is None or not units:
            errors.append("expanded_variant_inventory.unit_identities must list expanded variant unit identities")
        if inventory_dict.get("variant_axes_detected") is not True:
            errors.append("expanded_variant_inventory.variant_axes_detected must be true")
        count = inventory_dict.get("expanded_operator_instances_count")
        if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
            errors.append("expanded_variant_inventory.expanded_operator_instances_count must be a positive integer")
        elif units is not None and count != len(units):
            errors.append("expanded_variant_inventory.expanded_operator_instances_count must equal unit_identities length")

    axis_coverage = data.get("variant_axis_coverage")
    if not isinstance(axis_coverage, dict):
        errors.append("variant_axis_coverage must describe required variant axes and coverage checks")
    else:
        axis_coverage_dict = cast(dict[str, object], axis_coverage)
        if axis_coverage_dict.get("all_axes_covered") is not True:
            errors.append("variant_axis_coverage.all_axes_covered must be true")
        if not isinstance(axis_coverage_dict.get("axes"), dict) or not cast(dict[object, object], axis_coverage_dict.get("axes")).keys():
            errors.append("variant_axis_coverage.axes must be a non-empty object")

    performance = data.get("per_variant_performance_report")
    if not isinstance(performance, dict):
        errors.append("per_variant_performance_report must describe per-expanded-variant performance coverage")
    else:
        performance_dict = cast(dict[str, object], performance)
        if performance_dict.get("required") is not True:
            errors.append("per_variant_performance_report.required must be true")
        if performance_dict.get("one_entry_per_expanded_variant") is not True:
            errors.append("per_variant_performance_report.one_entry_per_expanded_variant must be true")

    if required_checks is None:
        return
    normalized_checks = {_normalize_check(check) for check in required_checks}
    missing = sorted(REQUIRED_VARIANT_CHECKS - normalized_checks)
    if missing:
        errors.append("required_checks missing expanded variant checks: " + ", ".join(missing))


def _require_existing_custom_op_entry_script(
    entry_script_path: object,
    reports_dir: object,
    project_root: Path | None,
    errors: list[str],
) -> None:
    if not isinstance(entry_script_path, str) or not entry_script_path.strip():
        return
    if not isinstance(reports_dir, str) or not reports_dir.strip():
        return
    if project_root is None:
        return

    entry_path = Path(entry_script_path).expanduser()
    candidate = entry_path if entry_path.is_absolute() else project_root / entry_path
    try:
        resolved_entry = candidate.resolve(strict=True)
        if not resolved_entry.is_file():
            raise FileNotFoundError
        _ = resolved_entry.relative_to(project_root)
        return
    except (OSError, ValueError):
        pass
    error = (
        "entry_script_path must point to an existing file for custom-op contracts under the project directory; "
        + "create or select the full validation script before returning Phase 3 JSON"
    )
    errors.append(error)


def _validate_custom_op_project_root(value: object, errors: list[str]) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        errors.append("project_dir must be a non-empty string for custom-op contracts")
        return None
    try:
        return Path(value).expanduser().resolve(strict=False)
    except OSError as exc:
        errors.append(f"project_dir could not be resolved for custom-op contracts: {exc}")
        return None


def _validate_custom_op_reports_dir(reports_dir: str, project_root: Path, errors: list[str]) -> None:
    raw_path = Path(reports_dir).expanduser()
    candidate = raw_path if raw_path.is_absolute() else project_root / raw_path
    expected = project_root / "migration_reports"
    try:
        resolved_reports = candidate.resolve(strict=False)
    except OSError as exc:
        errors.append(f"reports_dir could not be resolved for custom-op contracts: {exc}")
        return
    if resolved_reports != expected:
        errors.append("reports_dir must be the target project's trusted migration_reports directory")


def _validate_custom_op_run_command_project_local(
    run_command: object,
    entry_script_path: object,
    project_root: Path | None,
    errors: list[str],
) -> None:
    if project_root is None:
        return
    if not isinstance(run_command, str) or not run_command.strip():
        return
    try:
        tokens = shlex.split(run_command)
    except ValueError:
        return

    script_tokens = [token for token in tokens[1:] if Path(token).suffix == ".py" or token.endswith(".py")]
    if not script_tokens:
        errors.append("run_command must invoke the custom-op entry script file under the trusted project directory")
        return

    resolved_entry = _resolve_project_local_path(entry_script_path, project_root)
    matched_entry = False
    for token in script_tokens:
        resolved_token = _resolve_project_local_path(token, project_root)
        if resolved_token is None:
            errors.append("run_command script operands must stay under the trusted project directory")
            return
        if resolved_entry is not None and resolved_token == resolved_entry:
            matched_entry = True
    if resolved_entry is not None and not matched_entry:
        errors.append("run_command must execute the same project-local script named by entry_script_path")


def _resolve_project_local_path(value: object, project_root: Path) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw_path = Path(value).expanduser()
    candidate = raw_path if raw_path.is_absolute() else project_root / raw_path
    try:
        resolved = candidate.resolve(strict=True)
        _ = resolved.relative_to(project_root)
    except (OSError, ValueError):
        return None
    return resolved


def _string_list(value: object) -> list[str] | None:
    if not isinstance(value, list):
        return None
    strings: list[str] = []
    value_items = cast(list[object], value)
    for item in value_items:
        if not isinstance(item, str) or not item.strip():
            return None
        strings.append(item)
    return strings


def _normalize_check(value: str) -> str:
    return value.strip().lower().replace("-", "_").replace(" ", "_")


def _contains_token(values: list[str], token: str) -> bool:
    normalized_token = token.lower()
    return any(normalized_token in value.lower() for value in values)


def _contains_partial_success_terms(values: list[str]) -> bool:
    return any(_describes_partial_success_target(value) for value in values)


def _missing_required_obligations(normalized_obligations: set[str]) -> list[str]:
    return sorted(
        obligation
        for obligation in REQUIRED_VALIDATION_OBLIGATIONS
        if not any(_obligation_satisfies_required(obligation, observed) for observed in normalized_obligations)
    )


def _obligation_satisfies_required(required: str, observed: str) -> bool:
    return observed == required or observed.startswith(required + "_")


def _reject_partial_contract_text(data: dict[str, object], errors: list[str]) -> None:
    text_fields = ("entry_script_kind", "run_command", "entry_script_path")
    values = [data.get(field) for field in text_fields]
    values.extend(_string_list(data.get("required_report_paths")) or [])
    values.extend(_string_list(data.get("validation_obligations")) or [])
    for value in values:
        if isinstance(value, str) and _describes_partial_success_target(value):
            errors.append("custom-op contract must not describe a smoke/MVP/partial-only validation target")
            return
    for value in values:
        if isinstance(value, str) and any(term in value.lower() for term in REPORT_ONLY_VALIDATOR_TERMS):
            errors.append("custom-op entry script must be a full validation runner, not a report-only final evidence validator")
            return


def _describes_partial_success_target(value: str) -> bool:
    normalized = value.lower()
    if not any(term in normalized for term in PARTIAL_SUCCESS_TERMS):
        return False
    if any(token in normalized for token in ("reject", "forbid", "disallow", "fail_closed", "must_not", "no_", "not_", "non_")):
        return False
    return True


def _reject_report_only_entry_target(entry_script_path: object, run_command: object, errors: list[str]) -> None:
    for value in (entry_script_path, run_command):
        if not isinstance(value, str):
            continue
        normalized = value.lower().replace("\\", "/")
        if any(term in normalized for term in REPORT_ONLY_ENTRY_PATH_TERMS):
            errors.append("entry script must not point to migration_reports/final_evidence_validate.py or another report-only evidence validator")
            return


def _reject_benchmark_only_target(entry_script_path: object, run_command: object, errors: list[str]) -> None:
    for value in (entry_script_path, run_command):
        if not isinstance(value, str):
            continue
        normalized = value.lower().replace("_", "_")
        if any(term in normalized for term in BENCHMARK_ONLY_TERMS):
            errors.append("custom-op entry script must not select a benchmark-only validation target")
            return


def _reject_unsafe_run_command(run_command: str, errors: list[str]) -> None:
    if any(control in run_command for control in UNSAFE_RUN_COMMAND_CONTROLS):
        errors.append("run_command must be a single non-interactive process command; create a wrapper script instead of using shell control syntax")
        return
    try:
        tokens = shlex.split(run_command)
    except ValueError:
        errors.append("run_command must be shell-parseable as a single process command")
        return
    if not tokens:
        errors.append("run_command must be a non-empty string")
        return
    executable = tokens[0].rsplit("/", 1)[-1]
    if executable in UNSAFE_RUN_COMMAND_EXECUTORS:
        errors.append("run_command must not invoke a shell or shell builtin; create a wrapper script instead")
        return
    if executable in ENV_EXECUTORS and _env_invokes_shell(tokens):
        errors.append("run_command must not invoke a shell through env; create a wrapper script instead")


def _env_invokes_shell(tokens: list[str]) -> bool:
    for token in tokens[1:]:
        if token.startswith("-") or "=" in token:
            continue
        executable = token.rsplit("/", 1)[-1]
        return executable in UNSAFE_RUN_COMMAND_EXECUTORS
    return False
