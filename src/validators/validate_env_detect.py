from __future__ import annotations

from core.platform_policy import EnvValidationConfig
from core.validator_engine import ValidationDict


def validate(
    data: dict[str, object],
    platform_policy: object | None = None,
) -> ValidationDict:
    """Validate environment-detection output.

    When ``platform_policy`` (a ``PlatformPolicy``) is provided, validation
    uses its ``env_validation`` config instead of legacy hardcoded branches.
    This unifies NPU/PPU/Musa/etc. into a single policy-driven path.

    Without a policy the original hand-coded rules apply unchanged.
    """
    errors: list[str] = []

    platform = data.get("platform")
    if not isinstance(platform, str) or not platform.strip():
        errors.append("platform must be a non-empty string")
        platform_key = ""
    else:
        platform_key = platform.strip().lower().replace("-", "_")

    if platform_key == "cpu":
        errors.append("platform must not be cpu")

    # Try policy-driven validation when a platform policy is available.
    env_cfg = _try_get_env_validation(platform_policy)
    if env_cfg is not None and env_cfg.detection_field:
        _validate_with_policy(data, platform_key, env_cfg, errors)
    else:
        _validate_legacy(data, platform_key, errors)

    python_version = data.get("python_version")
    if not isinstance(python_version, str) or not python_version.strip():
        errors.append("python_version must be a non-empty string")

    return {"passed": not errors, "errors": errors, "warnings": []}


# ---------------------------------------------------------------------------
# Policy-driven path
# ---------------------------------------------------------------------------


def _try_get_env_validation(policy: object | None) -> EnvValidationConfig | None:
    """Extract ``env_validation`` from a ``PlatformPolicy``-like object."""
    if policy is None:
        return None
    try:
        return getattr(policy, "env_validation")  # type: ignore[no-any-return]
    except AttributeError:
        return None


def _validate_with_policy(
    data: dict[str, object],
    platform_key: str,
    cfg: EnvValidationConfig,
    errors: list[str],
) -> None:
    detection = data.get(cfg.detection_field)
    if not isinstance(detection, bool):
        errors.append(
            f"{cfg.detection_field} must be a boolean for platform={platform_key}"
        )

    for field in cfg.required_string_fields:
        val = data.get(field)
        if not isinstance(val, str) or not val.strip():
            errors.append(
                f"{field} must be a non-empty string for platform={platform_key}"
            )

    for field in cfg.required_bool_fields:
        if not isinstance(data.get(field), bool):
            errors.append(
                f"{field} must be a boolean for platform={platform_key}"
            )

    for field in cfg.optional_string_fields:
        val = data.get(field)
        if val is not None and not isinstance(val, str):
            errors.append(
                f"{field} must be a string when present for platform={platform_key}"
            )

    for field in cfg.optional_bool_fields:
        val = data.get(field)
        if val is not None and not isinstance(val, bool):
            errors.append(
                f"{field} must be a boolean when present for platform={platform_key}"
            )


# ---------------------------------------------------------------------------
# Built-in fallback configs when no platform_policy is provided.
# Mirrors the env_validation presets in platform_policy.py.
# ---------------------------------------------------------------------------

_LEGACY_ENV_CFG: dict[str, EnvValidationConfig] = {
    "npu": EnvValidationConfig(
        detection_field="npu_detected",
        required_string_fields=("cann_version", "driver_version"),
        required_bool_fields=("ascendc_available",),
    ),
    "ppu": EnvValidationConfig(
        detection_field="ppu_detected",
        required_bool_fields=("cuda_api_available",),
    ),
    "cuda": EnvValidationConfig(
        detection_field="cuda_detected",
    ),
}


def _validate_legacy(
    data: dict[str, object],
    platform_key: str,
    errors: list[str],
) -> None:
    """Fallback validation when no PlatformPolicy is available.

    Uses a built-in mapping that mirrors the env_validation presets from
    platform_policy.py.  Unknown platform keys get a generic check.
    """
    cfg = _LEGACY_ENV_CFG.get(platform_key)
    if cfg is not None:
        _validate_with_policy(data, platform_key, cfg, errors)
        return

    # Generic fallback for unknown / unset platform keys.
    if not platform_key:
        return
    platform_detected = data.get(f"{platform_key}_detected")
    accelerator_detected = data.get("accelerator_detected")
    if not isinstance(platform_detected, bool) and not isinstance(accelerator_detected, bool):
        errors.append(
            f"{platform_key}_detected or accelerator_detected must be a boolean for platform={platform_key}"
        )
