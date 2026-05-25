"""Concise phase-boundary prompt injection for LLM phases.

Injects a short guidance block that scopes TODOs, tools, and sub-agents to the
current phase only, without forbidding their use entirely.  The outer
orchestrator handles phase transitions; the agent should focus on the current
phase's substeps.
"""

from __future__ import annotations


def _resolve_config(
    framework_config: dict[str, object] | None,
) -> dict[str, object]:
    """Normalise config to a flat-ish dict by unwrapping top-level ``framework``.

    Supports three shapes:

    1. Full config: ``{"framework": {"phase_boundary_guidance": {"enabled": false}}}``
    2. Unwrapped:   ``{"phase_boundary_guidance": {"enabled": false}}``
    3. Flat legacy: ``{"phase_boundary_guidance_enabled": false}``

    Returns a copy so callers never mutate the original.
    """
    if framework_config is None:
        return {}
    cfg = dict(framework_config)

    # Shape 1 — unwrap top-level ``framework`` whenever it is present.
    if "framework" in cfg and isinstance(cfg["framework"], dict):
        return dict(cfg["framework"])

    return cfg


def _bool_from_config(
    cfg: dict[str, object],
    *keys: str,
    default: bool = True,
) -> bool:
    """Read a boolean from *cfg*, trying nested dict keys then flat key.

    For ``phase_boundary_guidance.enabled`` the call would be::

        _bool_from_config(cfg, "phase_boundary_guidance", "enabled")

    It also falls back to the flat legacy key
    ``phase_boundary_guidance_enabled`` automatically.
    """
    # Try nested dict path: cfg["phase_boundary_guidance"]["enabled"]
    node: object = cfg
    for key in keys:
        if isinstance(node, dict):
            node = node.get(key)
        else:
            node = None
    if isinstance(node, bool):
        return node
    if isinstance(node, str):
        return node.strip().lower() not in ("false", "0", "no", "off")

    # Fallback to flat legacy key: phase_boundary_guidance_enabled
    flat_key = "_".join(keys) if keys else ""
    flat = cfg.get(flat_key)
    if isinstance(flat, bool):
        return flat
    if isinstance(flat, str):
        return flat.strip().lower() not in ("false", "0", "no", "off")

    return default


_BOUNDARY_BLOCK = (
    "\n\n## Phase Boundary\n"
    "You are working on the **current phase** only. You may use TODOs, "
    "tools, and sub-agents freely to complete the current phase's substeps. "
    "Do **not** create TODOs or take actions that belong to later phases. "
    "Return the phase output, then wait for the controller to advance.\n"
)


def inject_phase_boundary(
    prompt_text: str,
    *,
    framework_config: dict[str, object] | None = None,
) -> str:
    """Append concise phase-boundary guidance to *prompt_text* when enabled.

    The extra block:
    - scopes TODOs / tools / sub-agents to the current phase
    - forbids cross-phase actions
    - uses neutral wording (no framework name)
    - is controlled via ``phase_boundary_guidance.enabled`` in config
    """
    cfg = _resolve_config(framework_config)

    if not _bool_from_config(cfg, "phase_boundary_guidance", "enabled"):
        return prompt_text

    # Ensure at most one trailing newline before the block.
    if not prompt_text.endswith("\n"):
        prompt_text += "\n"
    return prompt_text + _BOUNDARY_BLOCK
