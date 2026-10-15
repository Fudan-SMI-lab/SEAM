"""Validation for operator runtime detection output."""

from core.validator_engine import ValidationDict


def validate(data: dict[str, object]) -> ValidationDict:
    errors: list[str] = []
    if data.get("phase_id") != "phase_1_operator_runtime_detect":
        errors.append("phase_id must be phase_1_operator_runtime_detect")
    if data.get("runtime_status") not in {"ready", "warning", "blocked"}:
        errors.append("runtime_status must be ready, warning, or blocked")
    for field in ("python_executable", "python_version", "target_soc"):
        value = data.get(field)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{field} must be a non-empty string")
    if not isinstance(data.get("npu_available"), bool):
        errors.append("npu_available must be a boolean")
    if not isinstance(data.get("device_count"), int) or data["device_count"] < 0:
        errors.append("device_count must be a non-negative integer")
    for field in ("blocking_issues", "warnings", "evidence"):
        if not isinstance(data.get(field), list):
            errors.append(f"{field} must be a list")
    return {"passed": not errors, "errors": errors, "warnings": []}
