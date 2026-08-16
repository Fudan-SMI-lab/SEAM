"""Structural config merge boundary for parsed JSONC values.

Deterministic, non-mutating merge of two config objects: nested objects merge
recursively, scalars replace, the ``plugin`` array deduplicates preserving
order and exact string/tuple forms, and every other array replaces wholesale.
Inputs are never mutated; outputs are deep clones.
"""
from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from enum import Enum, auto

from core.jsonc_parser import JsonValue

__all__ = ["ArrayMergePolicy", "merge_config"]


class ArrayMergePolicy(Enum):
    """Field-specific array merge policies."""

    REPLACE = auto()
    PLUGIN_DEDUPE = auto()


def _clone(value: JsonValue) -> JsonValue:
    return copy.deepcopy(value)


def _canonical(value: JsonValue) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _plugin_key(entry: JsonValue) -> tuple[str, str]:
    """Canonical, exact-form-preserving dedupe key for a plugin entry."""
    if isinstance(entry, str):
        return ("str", entry)
    if isinstance(entry, list):
        return ("list", _canonical(entry))
    if isinstance(entry, dict):
        ident = entry.get("id")
        if isinstance(ident, str):
            return ("id", ident)
        return ("dict", _canonical(entry))
    return ("other", _canonical(entry))


def _plugin_dedupe(
    base_entries: list[JsonValue], override_entries: list[JsonValue]
) -> list[JsonValue]:
    seen: set[tuple[str, str]] = set()
    out: list[JsonValue] = []
    for entry in [*base_entries, *override_entries]:
        key = _plugin_key(entry)
        if key in seen:
            continue
        seen.add(key)
        out.append(entry)
    return out


def merge_config(
    base: Mapping[str, JsonValue], overrides: Mapping[str, JsonValue]
) -> dict[str, JsonValue]:
    """Deterministic structural merge; inputs never mutated.

    Objects merge recursively; scalars replace; the ``plugin`` array
    deduplicates preserving order and exact string/tuple forms; every other
    array replaces wholesale.
    """
    result: dict[str, JsonValue] = {key: _clone(val) for key, val in base.items()}
    for key, override_val in overrides.items():
        base_val = result.get(key)
        if (
            key in result
            and isinstance(base_val, dict)
            and isinstance(override_val, dict)
        ):
            result[key] = merge_config(base_val, override_val)
        elif key == "plugin" and isinstance(override_val, list):
            base_list = base_val if isinstance(base_val, list) else []
            result[key] = _plugin_dedupe(base_list, override_val)
        else:
            result[key] = _clone(override_val)
    return result
