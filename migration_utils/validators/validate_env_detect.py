"""Validation for Phase 0 environment detection output."""

from core.validator_engine import ValidationDict


def validate(data: dict[str, object]) -> ValidationDict:
    errors: list[str] = []

    platform = data.get("platform")
    if platform not in ("npu", "cuda"):
        errors.append("platform must be one of: npu, cuda")

    if not isinstance(data.get("npu_detected"), bool):
        errors.append("npu_detected must be a boolean")

    python_version = data.get("python_version")
    if not isinstance(python_version, str) or not python_version.strip():
        errors.append("python_version must be a non-empty string")

    # CANN toolchain fields (added for operator_fixer awareness)
    cann_version = data.get("cann_version")
    if not isinstance(cann_version, str) or not cann_version.strip():
        errors.append("cann_version must be a non-empty string")

    ascendc_available = data.get("ascendc_available")
    if not isinstance(ascendc_available, bool):
        errors.append("ascendc_available must be a boolean")

    driver_version = data.get("driver_version")
    if not isinstance(driver_version, str) or not driver_version.strip():
        errors.append("driver_version must be a non-empty string")

    return {"passed": not errors, "errors": errors, "warnings": []}
