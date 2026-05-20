"""Validation for Phase 0 environment detection output.

Supports NPU (Ascend), CUDA, and PPU platforms. PPU outputs use
CUDA-compatible fields with NPU-compatibility fallbacks.
"""

from core.validator_engine import ValidationDict


def validate(data: dict[str, object]) -> ValidationDict:
    errors: list[str] = []

    platform = data.get("platform")
    if platform not in ("npu", "cuda", "ppu"):
        errors.append("platform must be one of: npu, cuda, ppu")

    # PPU uses cuda_api_available + ppu_detected instead of npu_detected
    if platform == "ppu":
        ppu_detected = data.get("ppu_detected")
        if not isinstance(ppu_detected, bool):
            errors.append("ppu_detected must be a boolean for platform=ppu")
        cuda_api_available = data.get("cuda_api_available")
        if not isinstance(cuda_api_available, bool):
            errors.append("cuda_api_available must be a boolean for platform=ppu")
    else:
        # NPU/CUDA: require npu_detected
        if not isinstance(data.get("npu_detected"), bool):
            errors.append("npu_detected must be a boolean")

    python_version = data.get("python_version")
    if not isinstance(python_version, str) or not python_version.strip():
        errors.append("python_version must be a non-empty string")

    # CANN toolchain fields: required for NPU, optional/flexible for cuda/ppu
    cann_version = data.get("cann_version")
    if platform == "npu":
        if not isinstance(cann_version, str) or not cann_version.strip():
            errors.append("cann_version must be a non-empty string")
    elif platform == "ppu":
        # PPU may supply cann_version for compat or omit it
        if cann_version is not None and not isinstance(cann_version, str):
            errors.append("cann_version must be a string when present")
    # cuda: cann_version already handled as optional (legacy compat)

    ascendc_available = data.get("ascendc_available")
    if platform == "npu":
        if not isinstance(ascendc_available, bool):
            errors.append("ascendc_available must be a boolean")
    elif platform == "ppu":
        if ascendc_available is not None and not isinstance(ascendc_available, bool):
            errors.append("ascendc_available must be a boolean when present")

    driver_version = data.get("driver_version")
    if platform == "npu":
        if not isinstance(driver_version, str) or not driver_version.strip():
            errors.append("driver_version must be a non-empty string")
    elif platform == "ppu":
        if driver_version is not None and not isinstance(driver_version, str):
            errors.append("driver_version must be a string when present")

    return {"passed": not errors, "errors": errors, "warnings": []}
