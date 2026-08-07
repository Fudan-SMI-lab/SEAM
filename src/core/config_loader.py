"""Framework Config Loader — parses YAML config with env var interpolation."""

import os
import re
from pathlib import Path
from typing import cast
import yaml
from core.paths import resolve_relative_path

_PACKAGE_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_CONFIG = _PACKAGE_ROOT / "config" / "framework_defaults.yaml"


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

    return cast("dict[str, object]", _interpolate_env(data))


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
