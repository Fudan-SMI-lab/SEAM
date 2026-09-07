"""Legacy discovery, config normalization, schema-driven validation, and
interactive selection helpers for :mod:`seam_init.omo_config`.

Candidate validation combines two layers:
1. **Schema authority** — the vendored draft-07 ``omo.schema.json`` document,
   validated via :func:`seam_init.omo_schema.validate_against_schema`.
2. **SEAM model-membership rules** — every model in agents/categories must be
   a nonempty exact ``provider/model`` member of the runtime model list, and
   every reasoning value must be in the canonical set extracted from the
   schema.  These are stricter than the upstream schema (which accepts any
   string for model and reasoning).
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Final, Protocol, final, runtime_checkable

from core.jsonc import JsonValue, parse_config_object
from core.secret_redaction import redact_json_value, redact_sensitive_text
from seam_init.models import SafeDetail
from seam_init.omo_profile import MAP_SECTIONS
from seam_init.omo_schema import validate_against_schema
from seam_init.opencode_discovery import JsonDict
from seam_init.opencode_selection import PromptPort

__all__ = [
    "HomePathPolicy", "LegacySource", "collect_models", "collect_reasoning",
    "discover_legacy_sources", "normalize_config", "seam_validate",
    "select_provider_model_reasoning", "summarize_config", "validate_model_catalog",
]

_PROFILES_KEY: Final[str] = "profiles"
_BRACKET_HARNESS: Final[tuple[str, ...]] = ("[opencode]", "[senpi]", "[codex]")


@runtime_checkable
class HomePathPolicy(Protocol):
    def home(self) -> Path: ...


@final
class LegacySource:
    __slots__ = ("path", "value", "source")

    def __init__(self, *, path: Path, value: JsonDict, source: str) -> None:
        self.path = path
        self.value = value
        self.source = source


def discover_legacy_sources(
    project_root: Path,
    legacy_rels: tuple[str, ...],
    global_rel: str,
    home: HomePathPolicy,
) -> tuple[LegacySource, ...]:
    """Discover and parse legacy OMO config sources (read-only)."""
    sources: list[LegacySource] = []
    targets: list[tuple[Path, str]] = [
        (project_root / rel, rel) for rel in legacy_rels]
    targets.append((home.home() / global_rel, "global"))
    for path, source in targets:
        if not path.is_file():
            continue
        try:
            parsed = parse_config_object(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(parsed.value, dict):
            sources.append(LegacySource(path=path, value=parsed.value, source=source))
    return tuple(sources)


def normalize_config(
    value: dict[str, JsonValue], schema_url: str,
) -> dict[str, JsonValue]:
    """Set current schema, convert variant->reasoning, ensure profiles.

    Non-mutating.  Preserves unknown supported top-level data and bracket
    harness sections (``[opencode]``/``[senpi]``/``[codex]``) verbatim.
    """
    result: dict[str, JsonValue] = {}
    for key, val in value.items():
        if key in MAP_SECTIONS and isinstance(val, dict):
            result[key] = _normalize_section(val)
        else:
            result[key] = copy.deepcopy(val)
    result["$schema"] = schema_url
    if _PROFILES_KEY not in result:
        result[_PROFILES_KEY] = {}
    return result


def _normalize_section(section: dict[str, JsonValue]) -> dict[str, JsonValue]:
    normalized: dict[str, JsonValue] = {}
    for name, entry in section.items():
        if isinstance(entry, dict) and "variant" in entry and "reasoning" not in entry:
            new_entry = copy.deepcopy(
                {k: v for k, v in entry.items() if k != "variant"})
            new_entry["reasoning"] = entry["variant"]
            normalized[name] = new_entry
        else:
            normalized[name] = copy.deepcopy(entry)
    return normalized


def collect_models(value: dict[str, JsonValue]) -> tuple[str, ...]:
    """Extract ALL model strings from agents/categories (including malformed).

    Returns every ``model`` value that is a string, regardless of whether it
    contains ``/``.  This ensures malformed siblings are validated, not ignored.
    """
    found: list[str] = []
    for section in MAP_SECTIONS:
        sec = value.get(section)
        if not isinstance(sec, dict):
            continue
        for entry in sec.values():
            if isinstance(entry, dict):
                model = entry.get("model")
                if isinstance(model, str) and model.strip():
                    found.append(model)
    return tuple(found)


def collect_reasoning(value: dict[str, JsonValue]) -> tuple[str, ...]:
    """Extract ALL reasoning strings from agents/categories."""
    found: list[str] = []
    for section in MAP_SECTIONS:
        sec = value.get(section)
        if not isinstance(sec, dict):
            continue
        for entry in sec.values():
            if isinstance(entry, dict):
                reasoning = entry.get("reasoning")
                if isinstance(reasoning, str) and reasoning.strip():
                    found.append(reasoning)
    return tuple(found)


def seam_validate(
    candidate_bytes: bytes,
    schema: JsonDict,
    reasoning_values: tuple[str, ...],
    runtime_models: tuple[str, ...],
) -> list[str]:
    """Schema-authority + SEAM model-membership validation.

    Returns a list of issue strings (empty = valid).  Layer 1 validates the
    candidate against the vendored draft-07 schema document.  Layer 2 enforces
    SEAM's stricter rules: every model is a nonempty ``provider/model`` member
    of the runtime list, and every reasoning value is canonical.
    """
    try:
        parsed = parse_config_object(candidate_bytes.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        return [f"parse error: {exc}"]
    instance = parsed.value
    issues = list(validate_against_schema(schema, instance))
    if isinstance(instance, dict):
        issues.extend(_check_models(instance, runtime_models))
        issues.extend(_check_reasoning(instance, reasoning_values))
    return issues


def _check_models(
    instance: dict[str, JsonValue], runtime_models: tuple[str, ...],
) -> list[str]:
    issues: list[str] = []
    model_set = set(runtime_models)
    for section in MAP_SECTIONS:
        sec = instance.get(section)
        if not isinstance(sec, dict):
            continue
        for name, entry in sec.items():
            if not isinstance(entry, dict):
                continue
            model = entry.get("model")
            if not isinstance(model, str) or not model.strip():
                continue
            if "/" not in model:
                issues.append(f"{section}.{name}.model: missing provider/model slash")
            elif model not in model_set:
                issues.append(f"{section}.{name}.model: {model!r} not in runtime list")
    return issues


def _check_reasoning(
    instance: dict[str, JsonValue], reasoning_values: tuple[str, ...],
) -> list[str]:
    issues: list[str] = []
    canonical = set(reasoning_values)
    for section in MAP_SECTIONS:
        sec = instance.get(section)
        if not isinstance(sec, dict):
            continue
        for name, entry in sec.items():
            if not isinstance(entry, dict):
                continue
            reasoning = entry.get("reasoning")
            if isinstance(reasoning, str) and reasoning.strip() and reasoning not in canonical:
                issues.append(
                    f"{section}.{name}.reasoning: {reasoning!r} not canonical")
    return issues


def select_provider_model_reasoning(
    prompt: PromptPort,
    runtime_models: tuple[str, ...],
    reasoning_supported: tuple[str, ...],
) -> tuple[str, str] | None:
    """Interactive selection when multiple runtime models exist.

    Returns ``(model, reasoning)`` or ``None`` if the user cancels.  When
    multiple providers exist, prompts for provider first; then prompts for
    model and reasoning.  Call only when ``len(runtime_models) > 1``.
    """
    providers = sorted({m.split("/", 1)[0] for m in runtime_models if "/" in m})
    if len(providers) > 1:
        provider = prompt.ask(
            f"Multiple providers available: {', '.join(providers)}. Select provider:",
        ).strip()
        if provider not in providers:
            return None
        candidates = sorted(m for m in runtime_models if m.startswith(f"{provider}/"))
    else:
        candidates = sorted(m for m in runtime_models if "/" in m)
    model = prompt.ask(
        f"Select model: {', '.join(candidates)}",
    ).strip()
    if model not in candidates:
        return None
    reasoning = prompt.ask(
        f"Select reasoning level: {', '.join(reasoning_supported)}",
    ).strip()
    if reasoning not in reasoning_supported:
        return None
    return model, reasoning


def summarize_config(value: dict[str, JsonValue]) -> SafeDetail:
    """Zero-secret structural summary for consent display."""
    redacted = redact_json_value(value)
    if not isinstance(redacted, dict):
        return SafeDetail("(empty)")
    parts: list[str] = ["$schema:present" if "$schema" in redacted else "$schema:absent"]
    for section in MAP_SECTIONS:
        sec = redacted.get(section)
        count = len(sec) if isinstance(sec, dict) else 0
        parts.append(f"{section}:{count}")
    for harness in _BRACKET_HARNESS:
        if harness in redacted:
            parts.append(f"{harness}:present")
    return SafeDetail(redact_sensitive_text("; ".join(parts)))


def validate_model_catalog(models: tuple[str, ...]) -> str | None:
    """Validate a runtime model catalog; return error detail or None if usable.

    Rejects empty tuples, entries without a ``/`` separator (malformed
    ``provider/model``), and blank entries.  Called before any prompting
    or filesystem mutation so unusable catalogs fail closed.
    """
    if not models:
        return "runtime model catalog is empty"
    for m in models:
        if not m.strip():
            return f"malformed model entry: {m!r}"
        if "/" not in m:
            return f"model entry lacks provider/model slash: {m!r}"
    return None
