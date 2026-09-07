"""Canonical OMO agent/category names and fresh-config generation.

The 11 agents and 8 categories are verified against the upstream
``model-core`` source at commit ``ee81ab7`` (``agent-model-requirements.ts``,
``category-model-requirements.ts``).  A fresh project with no legacy and no
target can create an authorised ``.omo/omo.jsonc`` by mapping every canonical
agent and category to the selected runtime model and reasoning level.

SEAM writes only nonempty exact ``provider/model`` members for every mapping,
which is stricter than the upstream schema (the schema accepts any string;
the slash format is only a doctor *warning*).  This stricter rule is a SEAM
contract, not an upstream requirement.
"""
from __future__ import annotations

import copy
from typing import Final

from core.jsonc import JsonValue

__all__ = [
    "CANONICAL_AGENTS", "CANONICAL_CATEGORIES", "MAP_SECTIONS",
    "apply_model_to_all", "build_fresh_config", "build_canonical_with_preserved",
    "fill_missing_models", "DEFAULT_REASONING",
]

CANONICAL_AGENTS: Final[tuple[str, ...]] = (
    "sisyphus", "hephaestus", "oracle", "librarian", "explore",
    "multimodal-looker", "prometheus", "metis", "momus", "atlas",
    "sisyphus-junior",
)
CANONICAL_CATEGORIES: Final[tuple[str, ...]] = (
    "visual-engineering", "ultrabrain", "deep", "artistry",
    "quick", "unspecified-low", "unspecified-high", "writing",
)
MAP_SECTIONS: Final[tuple[str, ...]] = ("agents", "categories")
DEFAULT_REASONING: Final[str] = "medium"
_PROFILES_KEY: Final[str] = "profiles"


def build_fresh_config(
    schema_url: str, model: str, reasoning: str,
) -> dict[str, JsonValue]:
    """Generate a schema-valid fresh config mapping all canonical names.

    The result includes ``$schema``, ``profiles`` (required by the schema),
    and nonempty ``agents``/``categories`` sections.  Every entry carries the
    exact ``provider/model`` string and a canonical reasoning value.
    """
    if "/" not in model or not model.strip():
        raise ValueError(f"fresh model must be provider/model: {model!r}")
    agents: dict[str, JsonValue] = {
        name: {"model": model, "reasoning": reasoning} for name in CANONICAL_AGENTS
    }
    categories: dict[str, JsonValue] = {
        name: {"model": model, "reasoning": reasoning} for name in CANONICAL_CATEGORIES
    }
    return {
        "$schema": schema_url,
        _PROFILES_KEY: {},
        "agents": agents,
        "categories": categories,
    }


def fill_missing_models(
    config: dict[str, JsonValue], model: str, reasoning: str,
) -> dict[str, JsonValue]:
    """Fill entries lacking a well-formed provider/model with defaults.

    Non-mutating: returns a deep-copied config.  An entry is "missing" if its
    ``model`` is absent, not a string, or lacks a ``/`` separator.  The
    reasoning is set only when absent or non-string.
    """
    if "/" not in model or not model.strip():
        raise ValueError(f"fill model must be provider/model: {model!r}")
    result = copy.deepcopy(config)
    if _PROFILES_KEY not in result:
        result[_PROFILES_KEY] = {}
    for section in MAP_SECTIONS:
        sec = result.get(section)
        if not isinstance(sec, dict):
            continue
        for entry in sec.values():
            if not isinstance(entry, dict):
                continue
            existing = entry.get("model")
            if not (isinstance(existing, str) and "/" in existing and existing.strip()):
                entry["model"] = model
            reasoning_val = entry.get("reasoning")
            if not isinstance(reasoning_val, str) or not reasoning_val.strip():
                entry["reasoning"] = reasoning
    return result


def apply_model_to_all(
    config: dict[str, JsonValue], model: str, reasoning: str,
) -> dict[str, JsonValue]:
    """Replace model/reasoning in EVERY agents/categories entry.

    Non-mutating.  Used after multi-provider interactive selection to ensure
    all OMO mappings point to the user-chosen provider/model consistently.
    """
    if "/" not in model or not model.strip():
        raise ValueError(f"apply model must be provider/model: {model!r}")
    result = copy.deepcopy(config)
    if _PROFILES_KEY not in result:
        result[_PROFILES_KEY] = {}
    for section in MAP_SECTIONS:
        sec = result.get(section)
        if not isinstance(sec, dict):
            continue
        for entry in sec.values():
            if isinstance(entry, dict):
                entry["model"] = model
                entry["reasoning"] = reasoning
    return result


def build_canonical_with_preserved(
    schema_url: str, model: str, reasoning: str,
    source: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    """Build canonical mappings while preserving unrelated supported fields.

    Used when an authorized existing/legacy config has NO agent/category
    mappings.  Generates the full canonical agent/category set with the
    selected model, then copies every non-mapping top-level key from
    *source* (e.g. ``telemetry``, ``[senpi]``, ``[codex]``, ``_migrations``).
    """
    fresh = build_fresh_config(schema_url, model, reasoning)
    mapping_keys = {"$schema", _PROFILES_KEY, *MAP_SECTIONS}
    for key, val in source.items():
        if key not in mapping_keys:
            fresh[key] = copy.deepcopy(val)
    return fresh
