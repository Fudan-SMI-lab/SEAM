"""Validation for the single-operator benchmark report."""

from core.validator_engine import ValidationDict


def validate(data: dict[str, object]) -> ValidationDict:
    errors: list[str] = []
    for field in ("operator_name", "workload_pass_rate", "operator_pass_rate"):
        if field not in data:
            errors.append(f"{field} is required")
    for field in ("workload_pass_rate", "operator_pass_rate"):
        value = data.get(field)
        if not isinstance(value, (int, float)) or not 0 <= value <= 1:
            errors.append(f"{field} must be a number in [0, 1]")
    return {"passed": not errors, "errors": errors, "warnings": []}
