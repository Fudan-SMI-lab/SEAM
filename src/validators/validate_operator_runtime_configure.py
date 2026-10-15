"""Validation for operator runtime configuration output."""

from core.validator_engine import ValidationDict


def validate(data: dict[str, object]) -> ValidationDict:
    errors: list[str] = []
    if data.get("phase_id") != "phase_1_5_operator_runtime_configure":
        errors.append("phase_id must be phase_1_5_operator_runtime_configure")
    if data.get("configuration_status") not in {"ready", "changed", "blocked"}:
        errors.append("configuration_status must be ready, changed, or blocked")
    for field in ("changes", "verified_checks", "remaining_blocking_issues"):
        if not isinstance(data.get(field), list):
            errors.append(f"{field} must be a list")
    if not isinstance(data.get("summary"), str) or not data["summary"].strip():
        errors.append("summary must be a non-empty string")
    return {"passed": not errors, "errors": errors, "warnings": []}
