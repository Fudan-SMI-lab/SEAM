from __future__ import annotations

from core.platform_policy import (
    BUILTIN_PRESETS,
    EnvValidationConfig,
    PlatformPolicy,
)
from core.validator_engine import ValidationDict

# ── Legacy-to-preset key mapping ──────────────────────────────────────
_LEGACY_KEY_TO_PRESET: dict[str, str] = {
    "npu": "npu_ascend",
    "ppu": "ppu_cuda_compatible",
}


def validate(
    data: dict[str, object],
    platform_policy: PlatformPolicy | None = None,
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


def _try_get_env_validation(policy: PlatformPolicy | None) -> EnvValidationConfig | None:
    """Extract ``env_validation`` from a ``PlatformPolicy``."""
    if policy is None:
        return None
    return policy.env_validation


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
# Delegates to platform_policy presets where available, computes minimal
# detection-field-only configs for platforms not covered by presets.
# ---------------------------------------------------------------------------


def _get_legacy_env_validation(platform_key: str) -> EnvValidationConfig | None:
    """Return legacy EnvValidationConfig for *platform_key*, or None.

    Resolves via ``BUILTIN_PRESETS`` using the legacy-to-preset mapping;
    avoids direct imports of platform-specific config objects.
    """
    preset_id = _LEGACY_KEY_TO_PRESET.get(platform_key)
    if preset_id is not None:
        policy = BUILTIN_PRESETS.get(preset_id)
        if policy is not None:
            return policy.env_validation
    if platform_key in {"cuda", "musa", "rocm", "mlu"}:
        return EnvValidationConfig(detection_field=f"{platform_key}_detected")
    return None


def _validate_legacy(
    data: dict[str, object],
    platform_key: str,
    errors: list[str],
) -> None:
    """Fallback validation when no PlatformPolicy is available.

    Uses a built-in mapping that mirrors the env_validation presets from
    platform_policy.py.  Unknown platform keys get a generic check.
    """
    cfg = _get_legacy_env_validation(platform_key)
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
