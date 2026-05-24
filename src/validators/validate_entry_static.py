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
    "script_requires_strict_opp_producer_evidence",
    "script_rejects_non_opp_producer_success",
    "script_runs_project_api_custom_ops",
    "script_requires_per_row_route_evidence",
    "script_correlates_route_evidence_to_manifest_rows",
    "script_rejects_direct_or_builtin_only_routes",
    "script_rejects_report_only_success",
    "script_requires_project_local_artifacts",
    "script_requires_project_root_artifact_existence",
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
        errors.append("validation_passed must be a boolean")
        # Cannot evaluate issues if we don't know pass/fail status
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
