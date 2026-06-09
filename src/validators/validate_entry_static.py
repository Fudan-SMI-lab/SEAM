"""Validation for Phase 3.5 static entry script compliance check."""

import ast
import re
from pathlib import Path
from typing import cast

from core.routes import SERVING_ENTRY_KINDS
from core.validator_engine import ValidationDict

CUSTOM_OP_BOOLEAN_FIELDS = (
    "custom_op_requirements_checked",
    "script_source_driven_inventory",
    "script_emits_fine_grained_units",
    "script_maps_public_api_to_units",
    "script_discovers_full_inventory",
    "script_records_native_operator_symbols",
    "script_runs_project_api_custom_ops",
    "script_rejects_report_only_success",
    "script_requires_project_local_artifacts",
    "script_requires_numeric_performance",
    "script_checks_no_fallback",
)

EXPANDED_VARIANT_BOOLEAN_FIELDS = (
    "expanded_variant_static_required",
    "script_discovers_expanded_variant_inventory",
    "script_checks_variant_axis_coverage",
    "script_requires_per_variant_performance",
)

SHORT_INTERNAL_TIMEOUT_SECONDS = 3600

def validate(data: dict[str, object]) -> ValidationDict:
    """Validate Phase 3.5 static analysis output.

    Input JSON shape:
        {"validation_passed": bool, "issues": list[str], "fix_plan": str}

    Returns validation error if validation_passed is false.
    """
    errors: list[str] = []
    issues_list: list[str] = []
    fix_plan_text = ""

    validation_passed = data.get("validation_passed")
    if not isinstance(validation_passed, bool):
        raw_val_errors = data.get("validation_errors")
        if isinstance(raw_val_errors, list):
            validation_passed = len(raw_val_errors) == 0
            # Seed issues from validation_errors so downstream semantic checks work
            if not validation_passed:
                data.setdefault("issues", raw_val_errors)
            if not data.get("fix_plan"):
                data["fix_plan"] = "Auto-synthesized from validation_errors: " + (
                    "no errors" if validation_passed else "; ".join(str(e) for e in raw_val_errors[:5])
                )
        elif isinstance(validation_passed, str):
            normalized = validation_passed.strip().lower()
            if normalized in {"true", "false"}:
                validation_passed = normalized == "true"
            elif normalized in {"yes", "pass", "ok", "success", "y", "1"}:
                validation_passed = True
            elif normalized in {"no", "fail", "error", "n", "0"}:
                validation_passed = False
            else:
                errors.append(f"validation_passed must be a boolean, got string {validation_passed!r}")
                return {"passed": False, "errors": errors, "warnings": []}
        elif isinstance(validation_passed, (int, float)):
            # LLM sometimes outputs 0/1 or 0.0/1.0 as booleans
            validation_passed = bool(validation_passed)
        elif validation_passed is None:
            # LLM emitted JSON null — treat as False but note the ambiguity
            validation_passed = False
            data.setdefault("issues", list(data.get("issues", []))).append(
                "validation_passed was null / missing — treated as False"
            )
        else:
            errors.append(
                f"validation_passed must be a boolean, got {type(validation_passed).__name__}: {validation_passed!r}"
            )
            return {"passed": False, "errors": errors, "warnings": []}

    raw_issues = data.get("issues", [])
    if not isinstance(raw_issues, list):
        errors.append("issues must be a list of strings")
    else:
        for item in cast(list[object], raw_issues):
            if not isinstance(item, str):
                errors.append(f"Each issue must be a string, got {type(item).__name__}")
                break
            issues_list.append(item)

    fix_plan = data.get("fix_plan")
    if not isinstance(fix_plan, str):
        errors.append("fix_plan must be a string")
    else:
        fix_plan_text = fix_plan.strip()

    # If structural validation passed, check the semantic result
    if not errors:
        non_empty_issues = [issue for issue in issues_list if issue.strip()]
        if len(non_empty_issues) != len(issues_list):
            errors.append("issues must not contain blank strings")

        if not fix_plan_text:
            errors.append("fix_plan must be a non-empty string")

        if validation_passed and issues_list:
            errors.append("validation_passed=true requires issues to be empty")

        if not validation_passed and not non_empty_issues:
            errors.append("validation_passed=false requires at least one issue")

        if data.get("custom_op_static_required") is False:
            errors.append("custom_op_static_required must be true when present")

        entry_script_kind = data.get("entry_script_kind")
        allowed_entry_kinds = {"custom_op_full_validation", *SERVING_ENTRY_KINDS}
        if entry_script_kind is not None and entry_script_kind not in allowed_entry_kinds:
            errors.append("entry_script_kind must be a supported validation kind when present")

        if _custom_static_required(data):
            missing_fields = [field for field in CUSTOM_OP_BOOLEAN_FIELDS if field not in data]
            if missing_fields:
                errors.append("custom-op static validation missing booleans: " + ", ".join(missing_fields))
            for field in CUSTOM_OP_BOOLEAN_FIELDS:
                if field in data and data.get(field) is not True:
                    errors.append(f"{field} must be true for custom-op static validation")

        if _expanded_variant_static_required(data):
            missing_variant_fields = [field for field in EXPANDED_VARIANT_BOOLEAN_FIELDS if field not in data]
            if missing_variant_fields:
                errors.append("expanded-variant static validation missing booleans: " + ", ".join(missing_variant_fields))
            for field in EXPANDED_VARIANT_BOOLEAN_FIELDS:
                if field in data and data.get(field) is not True:
                    errors.append(f"{field} must be true for expanded-variant static validation")

        if _custom_static_required(data):
            errors.extend(_entry_script_timeout_errors(data))

        # Validate custom_op_surface when present (custom-op surface generation)
        custom_op_surface = data.get("custom_op_surface")
        if custom_op_surface is not None:
            _validate_custom_op_surface(custom_op_surface, errors)

    if not errors:
        if not validation_passed:
            # Map issues to validation errors
            return {
                "passed": False,
                "errors": issues_list,
                "warnings": [],
            }

    return {"passed": not errors, "errors": errors, "warnings": []}


def _custom_static_required(data: dict[str, object]) -> bool:
    if data.get("custom_op_static_required") is True:
        return True
    if data.get("entry_script_kind") == "custom_op_full_validation":
        return True
    return any(field in data for field in CUSTOM_OP_BOOLEAN_FIELDS)


def _expanded_variant_static_required(data: dict[str, object]) -> bool:
    if data.get("expanded_variant_static_required") is True:
        return True
    return any(field in data for field in EXPANDED_VARIANT_BOOLEAN_FIELDS if field != "expanded_variant_static_required")


def _entry_script_timeout_errors(data: dict[str, object]) -> list[str]:
    raw_path = data.get("entry_script_path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        return []

    script_path = Path(raw_path)
    if not script_path.is_file():
        return []
    try:
        source = script_path.read_text(encoding="utf-8")
    except OSError:
        return []

    errors: list[str] = []
    errors.extend(_ast_short_timeout_errors(source, script_path))
    if errors:
        return errors
    return _regex_short_timeout_errors(source, script_path)


def _ast_short_timeout_errors(source: str, script_path: Path) -> list[str]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    errors: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not _is_subprocess_call(node):
            continue
        timeout = _literal_timeout_value(node)
        if timeout is None or timeout >= SHORT_INTERNAL_TIMEOUT_SECONDS:
            continue
        if not _call_targets_project_validation(node):
            continue
        errors.append(
            f"{script_path}:{node.lineno}: custom-op validation script uses short internal subprocess timeout={timeout:g}; " +
            "real project/API validation must not be bounded by a short generated-script timeout"
        )
    return errors


def _regex_short_timeout_errors(source: str, script_path: Path) -> list[str]:
    errors: list[str] = []
    for match in re.finditer(r"timeout\s*=\s*(\d+(?:\.\d+)?)", source):
        timeout = float(match.group(1))
        if timeout >= SHORT_INTERNAL_TIMEOUT_SECONDS:
            continue
        window = source[max(0, match.start() - 500): match.end() + 500].lower()
        if not _text_targets_project_validation(window):
            continue
        line_no = source.count("\n", 0, match.start()) + 1
        errors.append(
            f"{script_path}:{line_no}: custom-op validation script uses short internal subprocess timeout={timeout:g}; " +
            "real project/API validation must not be bounded by a short generated-script timeout"
        )
    return errors


def _is_subprocess_call(node: ast.Call) -> bool:
    func = node.func
    if isinstance(func, ast.Attribute) and func.attr in {"run", "Popen", "communicate"}:
        value = func.value
        if isinstance(value, ast.Name) and value.id in {"subprocess", "process"}:
            return True
    return False


def _literal_timeout_value(node: ast.Call) -> float | None:
    for keyword in node.keywords:
        if keyword.arg != "timeout":
            continue
        value = keyword.value
        if isinstance(value, ast.Constant) and isinstance(value.value, (int, float)) and not isinstance(value.value, bool):
            return float(value.value)
    return None


def _call_targets_project_validation(node: ast.Call) -> bool:
    return _text_targets_project_validation(ast.unparse(node).lower())


def _text_targets_project_validation(text: str) -> bool:
    project_tokens = (
        "test_e2e",
        "e2e",
        "project api",
        "public api",
        "integration",
        "validation",
    )
    return any(token in text for token in project_tokens)


def _validate_custom_op_surface(surface: object, errors: list[str]) -> None:
    """Validate the custom_op_surface output from Phase 3.5's custom-op surface generation."""
    if not isinstance(surface, dict):
        errors.append("custom_op_surface must be a dict")
        return

    def _check_bool(key: str) -> bool:
        val = surface[key]
        if not isinstance(val, bool):
            errors.append(f"custom_op_surface.{key} must be bool, got {type(val).__name__}")
            return False
        return val

    def _check_str_list(key: str) -> None:
        val = surface.get(key)
        if val is None:
            errors.append(f"custom_op_surface.{key} is required but missing")
            return
        if not isinstance(val, list):
            errors.append(f"custom_op_surface.{key} must be a list, got {type(val).__name__}")
            return
        for i, item in enumerate(val):
            if not isinstance(item, str):
                errors.append(f"custom_op_surface.{key}[{i}] must be str, got {type(item).__name__}")

    def _check_obj_list(key: str, required_keys: tuple[str, ...]) -> None:
        val = surface.get(key)
        if val is None:
            errors.append(f"custom_op_surface.{key} is required but missing")
            return
        if not isinstance(val, list):
            errors.append(f"custom_op_surface.{key} must be a list, got {type(val).__name__}")
            return
        for i, item in enumerate(val):
            if not isinstance(item, dict):
                errors.append(f"custom_op_surface.{key}[{i}] must be a dict, got {type(item).__name__}")
                continue
            for rk in required_keys:
                if rk not in item:
                    errors.append(f"custom_op_surface.{key}[{i}] missing required key '{rk}'")

    def _check_str_dict(key: str) -> None:
        val = surface.get(key)
        if val is None:
            return
        if not isinstance(val, dict):
            errors.append(f"custom_op_surface.{key} must be dict, got {type(val).__name__}")

    has_detected = _check_bool("custom_op_detected")
    _check_bool("discovery_complete")

    # Required string-list fields
    for field in (
        "discovery_sources_checked",
        "searched_source_roots",
        "searched_source_paths",
        "operator_families",
        "fine_grained_operator_units",
        "discovered_operator_names",
        "native_operator_symbols",
        "kernel_launch_sites",
        "source_evidence",
        "negative_evidence",
        "dynamic_loading_checks",
        "build_load_checks",
        "unresolved_source_groups",
        "out_of_scope_source_groups",
    ):
        _check_str_list(field)

    # Required object-list fields
    _check_obj_list("fine_grained_operator_unit_evidence", ("unit_identity", "source_evidence"))
    _check_obj_list("expanded_operator_variants", ("unit_identity", "base_unit_identity", "axis_values", "source_evidence"))

    # Optional object fields
    _check_str_dict("variant_axes")

    # variant_axes_detected consistency
    variant_detected = surface.get("variant_axes_detected")
    if isinstance(variant_detected, bool) and variant_detected:
        if not surface.get("variant_axes"):
            errors.append("custom_op_surface.variant_axes must be present when variant_axes_detected is true")
        if not surface.get("expanded_operator_variants"):
            errors.append("custom_op_surface.expanded_operator_variants must be present when variant_axes_detected is true")

    # expanded_operator_instances_count should match expanded_operator_variants
    # length, but an LLM-reported count that drifts from the actual produced
    # list does not invalidate the surface.  Accept the variants list as the
    # authoritative source and warn on mismatch.
    instance_count = surface.get("expanded_operator_instances_count")
    expanded_variants = surface.get("expanded_operator_variants")
    if isinstance(instance_count, int) and isinstance(expanded_variants, list):
        if instance_count != len(expanded_variants):
            import logging
            _logger = logging.getLogger(__name__)
            _logger.warning(
                "custom_op_surface.expanded_operator_instances_count (%d) != len(expanded_operator_variants) (%d) — using variants list length",
                instance_count, len(expanded_variants),
            )

    # Cross-field consistency: fine_grained_operator_units should not be empty when custom_op_detected
    if has_detected:
        units = surface.get("fine_grained_operator_units")
        if isinstance(units, list) and len(units) == 0:
            errors.append("custom_op_surface.fine_grained_operator_units must not be empty when custom_op_detected is true")
        families = surface.get("operator_families")
        if isinstance(families, list) and len(families) == 0:
            errors.append("custom_op_surface.operator_families must not be empty when custom_op_detected is true")
