"""Validation for Phase 3 entry script output."""

from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import cast

from core.validator_engine import ValidationDict

_ENV_VAR_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

CUSTOM_OP_FIELDS = {
    "entry_script_kind",
    "reports_dir",
    "required_report_paths",
    "required_checks",
    "operator_discovery_sources",
    "operator_inventory_schema",
    "validation_obligations",
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
    "runtime_project_api",
    "numeric_performance",
    "complete_speedup_report",
    "overall_speedup_report",
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
    "same_run_runtime_coverage",
    "performance_evidence",
    "complete_performance_report",
    "overall_speedup_report",
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
CONTAINER_RUNTIME_EXECUTORS = {"docker", "podman"}


def _extract_env_prefix(
    command: str,
) -> tuple[dict[str, str], str]:
    """Strip leading KEY=VALUE env assignments from *command*.

    Returns (env_dict, remaining_command) where remaining_command is the
    original command with the env-leading tokens removed.  Only tokens that
    look like ``KEY=VALUE`` with a valid shell-style env variable name are
    treated as env assignments; the first token that does not match ends the
    prefix region.
    """
    try:
        tokens = shlex.split(command)
    except ValueError:
        return {}, command

    env: dict[str, str] = {}
    first_non_env = 0
    for token in tokens:
        eq = token.find("=")
        if eq > 0:
            name = token[:eq]
            if _ENV_VAR_NAME.match(name):
                env[name] = token[eq + 1 :]
                first_non_env += 1
                continue
        break

    if not env:
        return {}, command

    # Reconstruct the remaining command from the tokens we did not consume.
    remaining_tokens = tokens[first_non_env:]
    if not remaining_tokens:
        return env, ""
    return env, shlex.join(remaining_tokens)


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
        _reject_container_runtime_run_command(run_command, errors)

    _reject_report_only_entry_target(entry_script_path, run_command, errors)
    _reject_benchmark_only_target(entry_script_path, run_command, errors)

    if _has_custom_op_contract(data):
        _validate_custom_op_contract(data, errors)

    return {"passed": not errors, "errors": errors, "warnings": []}


def _has_custom_op_contract(data: dict[str, object]) -> bool:
    return any(field in data for field in CUSTOM_OP_FIELDS)


# pylint: disable-next=too-many-branches,too-many-locals,too-many-statements; silent
def _validate_custom_op_contract(data: dict[str, object], errors: list[str]) -> None:
    _require_existing_custom_op_entry_script(
        data.get("entry_script_path"),
        data.get("reports_dir"),
        errors,
    )

    entry_script_kind = data.get("entry_script_kind")
    if entry_script_kind != "custom_op_full_validation":
        errors.append(
            "entry_script_kind must be 'custom_op_full_validation' for custom-op contracts"
        )

    reports_dir = data.get("reports_dir")
    if not isinstance(reports_dir, str) or not reports_dir.strip():
        errors.append("reports_dir must be a non-empty string for custom-op contracts")
    elif "migration_reports" not in reports_dir:
        errors.append("reports_dir must point to the target project's migration_reports directory")

    required_report_paths = _string_list(data.get("required_report_paths"))
    if required_report_paths is None or not required_report_paths:
        errors.append(
            "required_report_paths must list migration report obligations for custom-op contracts"
        )
    else:
        missing_report_tokens = [
            token
            for token in REQUIRED_REPORT_TOKENS
            if not _contains_token(required_report_paths, token)
        ]
        if missing_report_tokens:
            errors.append(
                "required_report_paths must cover report categories: "
                + ", ".join(missing_report_tokens)
            )

    required_checks = _string_list(data.get("required_checks"))
    if required_checks is None or not required_checks:
        errors.append("required_checks must list full-validation checks for custom-op contracts")
    else:
        normalized_checks = {_normalize_check(check) for check in required_checks}
        missing_checks = sorted(REQUIRED_CHECKS - normalized_checks)
        if missing_checks:
            errors.append(
                "required_checks missing custom-op full-validation checks: "
                + ", ".join(missing_checks)
            )
        if _contains_partial_success_terms(required_checks):
            errors.append(
                "required_checks must enforce full validation, not smoke/MVP/partial-only success"
            )

    inventory_schema = data.get("operator_inventory_schema")
    if not isinstance(inventory_schema, dict):
        errors.append(
            # pylint: disable-next=line-too-long; silent
            "operator_inventory_schema must describe semantic rows, native symbols, kernels, source evidence, and out-of-scope groups"
        )
    else:
        schema_dict = cast(dict[str, object], inventory_schema)
        normalized_schema_fields = {_normalize_check(key) for key in schema_dict}
        missing_schema_fields = sorted(REQUIRED_INVENTORY_SCHEMA_FIELDS - normalized_schema_fields)
        if missing_schema_fields:
            errors.append(
                "operator_inventory_schema missing required fields: "
                + ", ".join(missing_schema_fields)
            )

    discovery_sources = _string_list(data.get("operator_discovery_sources"))
    if discovery_sources is None or not discovery_sources:
        errors.append(
            # pylint: disable-next=line-too-long; silent
            "operator_discovery_sources must list source discovery obligations for custom-op contracts"
        )
    else:
        normalized_sources = {_normalize_check(source) for source in discovery_sources}
        missing_sources = sorted(REQUIRED_DISCOVERY_SOURCES - normalized_sources)
        if missing_sources:
            errors.append(
                "operator_discovery_sources missing required sources: " + ", ".join(missing_sources)
            )
        if "requirements_doc" in normalized_sources:
            errors.append(
                # pylint: disable-next=line-too-long; silent
                "operator_discovery_sources must be source-driven and must not include requirements_doc as a completion source"
            )

    validation_obligations = _string_list(data.get("validation_obligations"))
    if validation_obligations is None or not validation_obligations:
        errors.append(
            # pylint: disable-next=line-too-long; silent
            "validation_obligations must list runtime validation obligations for custom-op contracts"
        )
    else:
        normalized_obligations = {
            _normalize_check(obligation) for obligation in validation_obligations
        }
        missing_obligations = sorted(REQUIRED_VALIDATION_OBLIGATIONS - normalized_obligations)
        if missing_obligations:
            errors.append(
                "validation_obligations missing required obligations: "
                + ", ".join(missing_obligations)
            )
        if _contains_partial_success_terms(validation_obligations):
            errors.append(
                # pylint: disable-next=line-too-long; silent
                "validation_obligations must enforce full validation, not smoke/MVP/partial-only success"
            )

    revision_allowed = data.get("phase5_entry_script_revision_allowed")
    if not isinstance(revision_allowed, bool):
        errors.append(
            "phase5_entry_script_revision_allowed must be a boolean for custom-op contracts"
        )

    _reject_partial_contract_text(data, errors)


def _require_existing_custom_op_entry_script(
    entry_script_path: object,
    reports_dir: object,
    errors: list[str],
) -> None:
    if not isinstance(entry_script_path, str) or not entry_script_path.strip():
        return
    if not isinstance(reports_dir, str) or not reports_dir.strip():
        return

    project_dir = Path(reports_dir).expanduser().parent.resolve(strict=False)
    entry_path = Path(entry_script_path).expanduser()
    candidate = entry_path if entry_path.is_absolute() else project_dir / entry_path
    try:
        resolved_entry = candidate.resolve(strict=True)
        if not resolved_entry.is_file():
            raise FileNotFoundError
        _ = resolved_entry.relative_to(project_dir)
        return
    except (OSError, ValueError):
        pass
    error = (
    # pylint: disable-next=line-too-long; silent
    "entry_script_path must point to an existing file for custom-op contracts under the project directory; " +
     "create or select the full validation script before returning Phase 3 JSON" )
    errors.append(error)


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
    return any(term in value.lower() for value in values for term in PARTIAL_SUCCESS_TERMS)


def _reject_partial_contract_text(data: dict[str, object], errors: list[str]) -> None:
    text_fields = ("entry_script_kind", "run_command", "entry_script_path")
    values = [data.get(field) for field in text_fields]
    values.extend(_string_list(data.get("required_report_paths")) or [])
    values.extend(_string_list(data.get("validation_obligations")) or [])
    for value in values:
        if isinstance(value, str) and any(term in value.lower() for term in PARTIAL_SUCCESS_TERMS):
            errors.append(
                "custom-op contract must not describe a smoke/MVP/partial-only validation target"
            )
            return
    for value in values:
        if isinstance(value, str) and any(
            term in value.lower() for term in REPORT_ONLY_VALIDATOR_TERMS
        ):
            errors.append(
                # pylint: disable-next=line-too-long; silent
                "custom-op entry script must be a full validation runner, not a report-only final evidence validator"
            )
            return


def _reject_report_only_entry_target(
    entry_script_path: object, run_command: object, errors: list[str]
) -> None:
    for value in (entry_script_path, run_command):
        if not isinstance(value, str):
            continue
        normalized = value.lower().replace("\\", "/")
        if any(term in normalized for term in REPORT_ONLY_ENTRY_PATH_TERMS):
            errors.append(
                # pylint: disable-next=line-too-long; silent
                "entry script must not point to migration_reports/final_evidence_validate.py or another report-only evidence validator"
            )
            return


def _reject_benchmark_only_target(
    entry_script_path: object, run_command: object, errors: list[str]
) -> None:
    for value in (entry_script_path, run_command):
        if not isinstance(value, str):
            continue
        normalized = value.lower().replace("_", "_")
        if any(term in normalized for term in BENCHMARK_ONLY_TERMS):
            errors.append(
                "custom-op entry script must not select a benchmark-only validation target"
            )
            return


def _reject_unsafe_run_command(run_command: str, errors: list[str]) -> None:
    if any(control in run_command for control in UNSAFE_RUN_COMMAND_CONTROLS):
        errors.append(
            # pylint: disable-next=line-too-long; silent
            "run_command must be a single non-interactive process command; create a wrapper script instead of using shell control syntax"
        )
        return
    try:
        tokens = shlex.split(run_command)
    except ValueError:
        errors.append("run_command must be shell-parseable as a single process command")
        return
    if not tokens:
        errors.append("run_command must be a non-empty string")
        return

    _, stripped = _extract_env_prefix(run_command)
    if stripped:
        try:
            tokens = shlex.split(stripped)
        except ValueError:
            pass

    if not tokens:
        errors.append("run_command must contain a real executable after env assignments")
        return

    executable = tokens[0].rsplit("/", 1)[-1]
    if executable in UNSAFE_RUN_COMMAND_EXECUTORS:
        errors.append(
            "run_command must not invoke a shell or shell builtin; create a wrapper script instead"
        )
        return
    if executable in ENV_EXECUTORS and _env_invokes_shell(tokens):
        errors.append(
            "run_command must not invoke a shell through env; create a wrapper script instead"
        )


def _env_invokes_shell(tokens: list[str]) -> bool:
    for token in tokens[1:]:
        if token.startswith("-") or "=" in token:
            continue
        executable = token.rsplit("/", 1)[-1]
        return executable in UNSAFE_RUN_COMMAND_EXECUTORS
    return False


def _reject_container_runtime_run_command(run_command: str, errors: list[str]) -> None:
    tokens_normalized = run_command.lower().replace("\\", "/")
    if "docker exec" in tokens_normalized or "podman exec" in tokens_normalized:
        errors.append(
            "run_command must not invoke docker/podman exec or pre-existing containers; "
            "the framework already executes this command inside a newly created container. "
            "Use a direct in-container command such as python3 /workspace/smoke_validate.py"
        )
        return
    try:
        tokens = shlex.split(run_command)
    except ValueError:
        return
    if not tokens:
        return

    _, stripped = _extract_env_prefix(run_command)
    if stripped:
        try:
            tokens = shlex.split(stripped)
        except ValueError:
            pass
    elif stripped == "":
        errors.append("run_command must contain a real executable after env assignments")
        return

    if not tokens:
        return

    executable = tokens[0].rsplit("/", 1)[-1]
    if executable in CONTAINER_RUNTIME_EXECUTORS:
        errors.append(
            "run_command must not invoke a container runtime (docker/podman); "
            "the framework already executes this command inside a newly created container. "
            "Use a direct in-container command such as python3 /workspace/smoke_validate.py"
        )
