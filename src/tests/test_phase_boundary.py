"""Tests for phase-boundary prompt injection."""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# pylint: disable-next=wrong-import-position; silent
from core.config_loader import load_framework_config
# pylint: disable-next=wrong-import-position; silent
from core.phase_boundary import _bool_from_config, _resolve_config, inject_phase_boundary

# ── Config resolution ──────────────────────────────────────────────────


def test_resolve_full_framework_config() -> None:
    cfg = _resolve_config(
        {
            "framework": {
                "phase_boundary_guidance": {"enabled": False},
                "omo_mode_directives": {"enabled": False, "scope": "current_phase"},
            }
        }
    )
    assert "phase_boundary_guidance" in cfg
    assert cfg["phase_boundary_guidance"] == {"enabled": False}
    assert "framework" not in cfg


def test_resolve_full_framework_config_with_extra_top_level_keys() -> None:
    """Framework key is unwrapped even when other top-level keys co-exist."""
    cfg = _resolve_config(
        {
            "framework": {
                "phase_boundary_guidance": {"enabled": False},
            },
            "other": {"key": "value"},
        }
    )
    assert "phase_boundary_guidance" in cfg
    assert "framework" not in cfg
    assert "other" not in cfg  # framework key consumed; rest dropped


def test_resolve_unwrapped_config() -> None:
    cfg = _resolve_config(
        {
            "phase_boundary_guidance": {"enabled": False},
            "omo_mode_directives": {"enabled": False},
        }
    )
    assert "phase_boundary_guidance" in cfg


def test_resolve_flat_legacy_config() -> None:
    cfg = _resolve_config(
        {
            "phase_boundary_guidance_enabled": False,
            "omo_mode_directives_enabled": False,
        }
    )
    assert "phase_boundary_guidance_enabled" in cfg


def test_resolve_none() -> None:
    # pylint: disable-next=use-implicit-booleaness-not-comparison; silent
    assert _resolve_config(None) == {}


# ── Bool extraction ────────────────────────────────────────────────────


def test_bool_from_nested_dict() -> None:
    assert (
        _bool_from_config(
            {"phase_boundary_guidance": {"enabled": False}}, "phase_boundary_guidance", "enabled"
        )
        is False
    )
    assert (
        _bool_from_config(
            {"phase_boundary_guidance": {"enabled": True}}, "phase_boundary_guidance", "enabled"
        )
        is True
    )


def test_bool_from_flat_key_fallback() -> None:
    assert (
        _bool_from_config(
            {"phase_boundary_guidance_enabled": False}, "phase_boundary_guidance", "enabled"
        )
        is False
    )


def test_bool_string_values() -> None:
    assert (
        _bool_from_config(
            {"phase_boundary_guidance_enabled": "false"}, "phase_boundary_guidance", "enabled"
        )
        is False
    )
    assert (
        _bool_from_config(
            {"phase_boundary_guidance_enabled": "no"}, "phase_boundary_guidance", "enabled"
        )
        is False
    )
    assert (
        _bool_from_config(
            {"phase_boundary_guidance_enabled": "0"}, "phase_boundary_guidance", "enabled"
        )
        is False
    )


def test_bool_defaults_true() -> None:
    assert _bool_from_config({}, "phase_boundary_guidance", "enabled") is True


# ── Boundary injection: content ────────────────────────────────────────


def test_inject_boundary_appends_short_guidance() -> None:
    prompt = "## Phase 2 - Environment Setup\n\nDo the thing."
    result = inject_phase_boundary(prompt)
    assert "## Phase Boundary" in result
    assert "current phase" in result.lower()
    assert "later phases" in result.lower()
    assert "wait for the controller" in result.lower()
    assert "TODOs" in result
    assert "sub-agents" in result
    # Boundary should be concise: ~3 lines of body text.
    marker = result.index("## Phase Boundary")
    block = result[marker:]
    body_lines = [l for l in block.split("\n") if l.strip() and not l.strip().startswith("#")]
    assert len(body_lines) <= 4  # ~3 body lines + possible trailing newline


def test_inject_boundary_no_output_path_notice() -> None:
    """Output path notice ('Mutable output project copies...') is removed."""
    result = inject_phase_boundary("prompt")
    assert "Mutable output project copies" not in result
    assert "outside the controller tree" not in result


# ── Boundary injection: config shapes ──────────────────────────────────


def test_disabled_via_full_framework_config() -> None:
    """Disabled via the top-level framework shape from load_framework_config()."""
    prompt = "Phase content here."
    result = inject_phase_boundary(
        prompt,
        framework_config={
            "framework": {
                "phase_boundary_guidance": {"enabled": False},
                "omo_mode_directives": {"enabled": True, "scope": "current_phase"},
            }
        },
    )
    assert prompt == result


def test_disabled_via_full_config_with_extra_keys() -> None:
    """Disabled even when top-level config has framework + other keys."""
    prompt = "Phase content."
    result = inject_phase_boundary(
        prompt,
        framework_config={
            "framework": {
                "phase_boundary_guidance": {"enabled": False},
            },
            "other": {"key": "value"},
        },
    )
    assert prompt == result


def test_disabled_via_unwrapped_nested_config() -> None:
    """Disabled via already-unwrapped nested config."""
    result = inject_phase_boundary(
        "prompt",
        framework_config={"phase_boundary_guidance": {"enabled": False}},
    )
    assert "prompt" == result


def test_disabled_via_flat_legacy_key() -> None:
    """Disabled via flat legacy boolean key (backward compatible)."""
    result = inject_phase_boundary(
        "prompt",
        framework_config={"phase_boundary_guidance_enabled": False},
    )
    assert "prompt" == result


def test_enabled_by_default() -> None:
    result = inject_phase_boundary("prompt")
    assert "## Phase Boundary" in result


def test_enabled_with_load_framework_config_defaults() -> None:
    """Uses live defaults from framework_defaults.yaml — should be enabled."""
    cfg = load_framework_config()
    result = inject_phase_boundary("prompt", framework_config=cfg)
    assert "## Phase Boundary" in result


def test_omo_directives_config_does_not_affect_presence() -> None:
    """omo_mode_directives config no longer controls output notice; boundary
    presence is only controlled by phase_boundary_guidance."""
    result = inject_phase_boundary(
        "prompt",
        framework_config={
            "phase_boundary_guidance": {"enabled": True},
            "omo_mode_directives": {"enabled": False},
        },
    )
    assert "## Phase Boundary" in result


# ── Boundary injection: content assertions ─────────────────────────────


def test_allows_current_phase_substeps() -> None:
    result = inject_phase_boundary("prompt")
    assert "You may use TODOs, tools, and sub-agents freely" in result


def test_forbids_later_phase_actions() -> None:
    result = inject_phase_boundary("prompt")
    assert "Do **not** create TODOs or take actions that belong to later phases" in result


def test_tells_agent_to_wait_for_controller() -> None:
    result = inject_phase_boundary("prompt")
    assert "wait for the controller" in result.lower()


def test_avoids_framework_name() -> None:
    result = inject_phase_boundary("prompt")
    assert "OpenCode" not in result
    assert "SEAM" not in result


def test_trailing_newline_handled() -> None:
    result = inject_phase_boundary("prompt\n")
    assert result.startswith("prompt\n")
    assert "## Phase Boundary" in result
