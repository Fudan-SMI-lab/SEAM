# pyright: reportUnusedCallResult=false

"""Verification tests for load_framework_config (config_loader)."""
import os
import sys
import tempfile
from pathlib import Path
from typing import cast

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.config_loader import load_framework_config


def test_load_framework_config_defaults():
    """Loads with no path, returns correct defaults."""
    config = load_framework_config()
    assert "framework" in config
    framework = cast(dict[str, object], config["framework"])
    assert "session_timeout_repair" not in framework
    assert "entry_script_timeout" not in framework
    assert framework["max_iterations"] == 10
    assert cast(dict[str, object], framework["review"])["enabled"] is False
    assisted = cast(dict[str, object], framework["assisted_verification"])
    assert assisted["verifier_lifecycle"] == "persistent"
    assert assisted["verifier_agent"] == "Sisyphus-Junior"
    assert cast(dict[str, object], framework["server"])["auto_start"] is True
    assert cast(dict[str, object], framework["artifacts"])["key_prefix"] == "phase"
    print("PASS: load_framework_config returns correct defaults")


def test_load_framework_config_explicit_path():
    """Loads from explicit path."""
    default_path = PROJECT_ROOT / "config" / "framework_defaults.yaml"
    config = load_framework_config(str(default_path))
    assert "framework" in config
    framework = cast(dict[str, object], config["framework"])
    assert framework["stagnation_threshold"] == 3
    print("PASS: load_framework_config loads from explicit path")


def test_load_framework_config_notfound():
    """Raises FileNotFoundError for missing file."""
    with pytest.raises(FileNotFoundError, match="Config file not found"):
        load_framework_config("/nonexistent/path/to/config.yaml")
    print("PASS: load_framework_config raises FileNotFoundError")


def test_load_framework_config_env_interpolation(monkeypatch):
    """Set env var, verify replacement in loaded config."""
    monkeypatch.setenv("CUSTOM_TIMEOUT", "9999")

    with tempfile.TemporaryDirectory() as tmpdir:
        cfg = Path(tmpdir) / "test_config.yaml"
        cfg.write_text(
            "framework:\n"
            '  session_timeout_repair: "{CUSTOM_TIMEOUT}"\n'
            '  name: "test-{CUSTOM_TIMEOUT}"\n',
            encoding="utf-8",
        )

        config = load_framework_config(str(cfg))
        framework = cast(dict[str, object], config["framework"])
        assert framework["session_timeout_repair"] == "9999"
        assert framework["name"] == "test-9999"
    print("PASS: load_framework_config interpolates env vars")
