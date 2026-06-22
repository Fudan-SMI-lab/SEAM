"""Validation for Phase 0 environment detection output.

Supports NPU (Ascend), CUDA, and PPU platforms. PPU outputs use
CUDA-compatible fields with NPU-compatibility fallbacks.
"""

from core.validator_engine import ValidationDict


def validate(data: dict[str, object]) -> ValidationDict:
    errors: list[str] = []

    platform = data.get("platform")
    if not isinstance(platform, str) or not platform.strip():
        errors.append("platform must be a non-empty string")
        platform_key = ""
    else:
        platform_key = platform.strip().lower().replace("-", "_")

    if platform_key == "cpu":
        errors.append("platform must not be cpu")

    if platform_key == "ppu":
        ppu_detected = data.get("ppu_detected")
        if not isinstance(ppu_detected, bool):
            errors.append("ppu_detected must be a boolean for platform=ppu")
        cuda_api_available = data.get("cuda_api_available")
        if not isinstance(cuda_api_available, bool):
            errors.append("cuda_api_available must be a boolean for platform=ppu")
    elif platform_key in ("npu", "cuda"):
        if not isinstance(data.get("npu_detected"), bool):
            errors.append("npu_detected must be a boolean")
    elif platform_key:
        platform_detected = data.get(f"{platform_key}_detected")
        accelerator_detected = data.get("accelerator_detected")
        if not isinstance(platform_detected, bool) and not isinstance(
            accelerator_detected, bool
        ):
            errors.append(
                f"{platform_key}_detected or accelerator_detected "
                f"must be a boolean for platform={platform_key}"
            )

    python_version = data.get("python_version")
    if not isinstance(python_version, str) or not python_version.strip():
        errors.append("python_version must be a non-empty string")

    # CANN toolchain fields: required for NPU, optional/flexible for cuda/ppu
    cann_version = data.get("cann_version")
    if platform_key == "npu":
        if not isinstance(cann_version, str) or not cann_version.strip():
            errors.append("cann_version must be a non-empty string")
    elif platform_key == "ppu":
        if cann_version is not None and not isinstance(cann_version, str):
            errors.append("cann_version must be a string when present")

    ascendc_available = data.get("ascendc_available")
    if platform_key == "npu":
        if not isinstance(ascendc_available, bool):
            errors.append("ascendc_available must be a boolean")
    elif platform_key == "ppu":
        if ascendc_available is not None and not isinstance(ascendc_available, bool):
            errors.append("ascendc_available must be a boolean when present")

    driver_version = data.get("driver_version")
    if platform_key == "npu":
        if not isinstance(driver_version, str) or not driver_version.strip():
            errors.append("driver_version must be a non-empty string")
    elif platform_key == "ppu":
        if driver_version is not None and not isinstance(driver_version, str):
            errors.append("driver_version must be a string when present")

    return {"passed": not errors, "errors": errors, "warnings": []}
