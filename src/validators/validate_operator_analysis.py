"""Validation for operator analysis output."""

from core.validator_engine import ValidationDict


def validate(data: dict[str, object]) -> ValidationDict:
    errors: list[str] = []
    for field in ("operator_name", "operator_task_dir", "reference_summary"):
        value = data.get(field)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{field} must be a non-empty string")
    workload_count = data.get("workload_count")
    if not isinstance(workload_count, int) or workload_count <= 0:
        errors.append("workload_count must be a positive integer")
    design = data.get("ascendc_design")
    if not isinstance(design, dict):
        errors.append("ascendc_design must be a dictionary")
    return {"passed": not errors, "errors": errors, "warnings": []}
