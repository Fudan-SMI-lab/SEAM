"""Framework Config Loader — parses YAML config with env var interpolation."""

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import cast
import yaml
from core.paths import resolve_relative_path

_PACKAGE_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_CONFIG = _PACKAGE_ROOT / "config" / "framework_defaults.yaml"


@dataclass(frozen=True)
class ContextManagementConfig:
    """Typed ``context_management`` config (bug #16 §5.2 contract).

    Field defaults mirror ``config/framework_defaults.yaml`` so a missing
    ``context_management`` section resolves to exactly the same values as
    an explicitly-configured one (missing -> defaults contract).
    """

    enabled: bool = True
    context_tokens: int | str = "auto"  # "auto" = estimate from provider metadata
    reserve_output_tokens: int = 8192
    compact_threshold_ratio: float = 0.72
    rotate_threshold_ratio: float = 0.88
    summary_budget_tokens: int = 12000
    keep_recent_turns: int = 2
    max_compactions_per_session: int = 2
    max_recoveries_per_command: int = 1


def _bool_field(raw: dict, key: str, default: bool) -> bool:
    value = raw.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(
            f"context_management.{key}: expected bool, got {value!r}"
        )
    return value


def _positive_int_field(raw: dict, key: str, default: int) -> int:
    value = raw.get(key, default)
    # bool is an int subclass in Python — reject it so `reserve_output_tokens: true`
    # cannot silently pass validation as 1.
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(
            f"context_management.{key}: expected positive int (> 0), got {value!r}"
        )
    return value


def _nonneg_int_field(raw: dict, key: str, default: int) -> int:
    value = raw.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(
            f"context_management.{key}: expected non-negative int (>= 0), got {value!r}"
        )
    return value


def _ratio_field(raw: dict, key: str, default: float) -> float:
    value = raw.get(key, default)
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not (0 < value < 1)
    ):
        raise ValueError(
            f"context_management.{key}: expected number in (0, 1), got {value!r}"
        )
    return float(value)


def load_context_management_config(raw: dict) -> ContextManagementConfig:
    """Validate a raw ``context_management`` mapping into a typed config.

    Missing keys fall back to the authoritative defaults from
    ``framework_defaults.yaml``; invalid values raise ``ValueError`` naming
    the offending field and the expected range, so a misconfigured section
    fails at startup instead of silently degrading.

    Args:
        raw: Raw ``context_management`` mapping (the section read from
            ``framework_defaults.yaml``, or ``{}`` when absent).

    Returns:
        Validated :class:`ContextManagementConfig`.

    Raises:
        ValueError: If any field violates the contract.
    """
    if not isinstance(raw, dict):
        raise ValueError(
            "context_management: expected a mapping, got "
            f"{type(raw).__name__}"
        )

    enabled = _bool_field(raw, "enabled", True)

    context_tokens = raw.get("context_tokens", "auto")
    if context_tokens != "auto" and (
        isinstance(context_tokens, bool)
        or not isinstance(context_tokens, int)
        or context_tokens <= 0
    ):
        raise ValueError(
            "context_management.context_tokens: expected 'auto' or a "
            f"positive int (> 0), got {context_tokens!r}"
        )

    reserve_output_tokens = _positive_int_field(
        raw, "reserve_output_tokens", 8192
    )
    compact_threshold_ratio = _ratio_field(
        raw, "compact_threshold_ratio", 0.72
    )
    rotate_threshold_ratio = _ratio_field(
        raw, "rotate_threshold_ratio", 0.88
    )
    if not compact_threshold_ratio < rotate_threshold_ratio:
        raise ValueError(
            "context_management: compact_threshold_ratio must be < "
            "rotate_threshold_ratio, got "
            f"compact={compact_threshold_ratio}, "
            f"rotate={rotate_threshold_ratio}"
        )

    summary_budget_tokens = _positive_int_field(
        raw, "summary_budget_tokens", 12000
    )
    keep_recent_turns = _nonneg_int_field(raw, "keep_recent_turns", 2)
    max_compactions_per_session = _positive_int_field(
        raw, "max_compactions_per_session", 2
    )
    max_recoveries_per_command = _positive_int_field(
        raw, "max_recoveries_per_command", 1
    )

    if context_tokens != "auto":
        if reserve_output_tokens + summary_budget_tokens >= context_tokens:
            raise ValueError(
                "context_management: reserve_output_tokens + "
                "summary_budget_tokens must be < context_tokens, got "
                f"{reserve_output_tokens} + {summary_budget_tokens} = "
                f"{reserve_output_tokens + summary_budget_tokens} >= "
                f"{context_tokens}"
            )

    return ContextManagementConfig(
        enabled=enabled,
        context_tokens=context_tokens,
        reserve_output_tokens=reserve_output_tokens,
        compact_threshold_ratio=compact_threshold_ratio,
        rotate_threshold_ratio=rotate_threshold_ratio,
        summary_budget_tokens=summary_budget_tokens,
        keep_recent_turns=keep_recent_turns,
        max_compactions_per_session=max_compactions_per_session,
        max_recoveries_per_command=max_recoveries_per_command,
    )


def load_framework_config(config_path: str | None = None) -> dict[str, object]:
    """Load framework configuration from YAML with env var interpolation.

    Args:
        config_path: Path to the YAML file. If None, uses the default
                     ``config/framework_defaults.yaml`` relative to the
                     package root.

    Returns:
        Merged config dict with all ``{VAR_NAME}`` placeholders replaced
        by ``os.environ.get('VAR_NAME', '')``.

    Raises:
        FileNotFoundError: If the config file does not exist.
    """
    yaml_path = Path(config_path) if config_path else _DEFAULT_CONFIG

    if not yaml_path.is_absolute() and config_path is not None:
        yaml_path = resolve_relative_path(yaml_path)

    if not yaml_path.exists():
        raise FileNotFoundError(f"Config file not found: {yaml_path}")

    with open(yaml_path, "r", encoding="utf-8") as f:
        data: dict[str, object] = yaml.safe_load(f) or {}

    data = cast("dict[str, object]", _interpolate_env(data))

    # context_management is the one strictly-validated section: an invalid
    # section must fail at startup rather than silently degrade.
    if "context_management" in data:
        data["context_management"] = load_context_management_config(
            cast("dict[object, object]", data["context_management"])
        )

    return data


def _interpolate_env(obj: object) -> object:
    """Recursively replace ``{VAR_NAME}`` in all string values."""
    if isinstance(obj, str):
        return re.sub(r"\{(\w+)\}", lambda m: os.environ.get(m.group(1), ""), obj)
    if isinstance(obj, dict):
        return {
            k: _interpolate_env(v) for k, v in cast("dict[object, object]", obj).items()
        }
    if isinstance(obj, list):
        return [_interpolate_env(item) for item in cast("list[object]", obj)]
    return obj
