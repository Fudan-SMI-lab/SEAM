"""Validation for summarized KernelBench operator evaluation output."""

from core.validator_engine import ValidationDict


def validate(data: dict[str, object]) -> ValidationDict:
    errors: list[str] = []
    for field in ("total_workloads", "passed_workloads"):
        value = data.get(field)
        if not isinstance(value, int) or value < 0:
            errors.append(f"{field} must be a non-negative integer")
    total = data.get("total_workloads")
    passed = data.get("passed_workloads")
    if isinstance(total, int) and isinstance(passed, int) and passed > total:
        errors.append("passed_workloads cannot exceed total_workloads")
    for field in ("workload_pass_rate", "operator_pass_rate"):
        value = data.get(field)
        if not isinstance(value, (int, float)) or not 0 <= value <= 1:
            errors.append(f"{field} must be a number in [0, 1]")
    if data.get("required_matched_ratio") != 0.99:
        errors.append("required_matched_ratio must be 0.99")
    return {"passed": not errors, "errors": errors, "warnings": []}
