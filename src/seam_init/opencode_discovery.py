"""Discovery, runtime proof, and structural summary for OpenCode configs.

Discovers project (``.opencode/opencode.jsonc``), project-root (``opencode.json``),
and global (``~/.config/opencode/opencode.json``) config candidates. Parses each
via :func:`core.jsonc.parse_config_object` (Task 2). Provides an injectable
:class:`RuntimePort` boundary that independently exposes ``opencode debug config``
and live provider-catalog model facts. Proves the chosen project path is live before
any transaction begins. Emits a zero-secret structural summary. Fresh targets are
proven post-write via :func:`prove_projection_loaded` against a secret-free
:class:`ConfigProjection` of the merged candidate.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum, unique
from pathlib import Path
from typing import Final, Protocol, final, runtime_checkable

from core.compat import TypeAlias, assert_never
from core.jsonc import JsonValue, parse_config_object  # noqa: F401
from core.secret_redaction import redact_json_value, redact_sensitive_text
from seam_init.models import SafeDetail
from seam_init.opencode_selection import ConfigProjection

__all__ = [
    "ConfigTarget", "ConfigTargetPolicy", "DiscoveredConfig",
    "ExistingConfigTarget", "FreshConfigTarget", "JsonDict", "RuntimePort",
    "discover_config_candidates", "discover_project_config",
    "is_path_observed", "prove_path_observed", "prove_projection_loaded",
    "select_config_target", "select_target_candidate",
    "summarize_structure", "target_path",
]

_PROJECT_CONFIG_REL: Final[str] = ".opencode/opencode.jsonc"
_GLOBAL_CONFIG_REL: Final[str] = ".config/opencode/opencode.json"

JsonDict: TypeAlias = "dict[str, JsonValue]"


def _safe(raw: str) -> SafeDetail:
    return SafeDetail(redact_sensitive_text(raw))


@runtime_checkable
class RuntimePort(Protocol):
    """Injectable boundary for merged config and live provider-model facts.

    The two methods are independent: ``debug_config`` returns the merged runtime
    config dict (used for path-observation proof), ``debug_models`` returns the
    available ``provider/model`` strings (used for model-list validation).
    Either may return ``None`` when opencode is absent or the command fails.
    """

    def debug_config(self) -> JsonDict | None: ...

    def debug_models(self, config_bytes: bytes | None = None) -> tuple[str, ...] | None: ...


@unique
class ConfigTargetPolicy(str, Enum):
    """Writable project config targets; global candidates remain read-only."""

    PROJECT_DOT_OPENCODE = "project-dot-opencode"
    PROJECT_ROOT = "project-root"


@final
@dataclass(frozen=True, slots=True)
class DiscoveredConfig:
    """A discovered config file with its parsed value and source label."""

    path: Path
    value: JsonDict
    source: str


@final
@dataclass(frozen=True, slots=True)
class ExistingConfigTarget:
    """Writable target backed by an existing discovered config file."""

    discovered: DiscoveredConfig


@final
@dataclass(frozen=True, slots=True)
class FreshConfigTarget:
    """Writable project target whose file does not exist yet.

    Created only by ``ConfigTransaction`` (absent-target sentinel).
    """

    path: Path


ConfigTarget: TypeAlias = ExistingConfigTarget | FreshConfigTarget

_PROJECT_SOURCES: Final = frozenset({"project", "project-root"})


def _try_parse(path: Path, source: str) -> DiscoveredConfig | None:
    """Parse a JSONC config file; None if absent, raise ValueError if malformed."""
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    parsed = parse_config_object(text)
    value = parsed.value
    if not isinstance(value, dict):
        return None
    return DiscoveredConfig(path=path, value=value, source=source)


def discover_project_config(project_root: Path) -> DiscoveredConfig | None:
    """Discover and parse ``.opencode/opencode.jsonc``; None when absent."""
    return _try_parse(project_root / _PROJECT_CONFIG_REL, "project")


def discover_config_candidates(
    project_root: Path,
) -> tuple[DiscoveredConfig, ...]:
    """Discover project, project-root, and global OpenCode config candidates.

    Order: project (``.opencode/opencode.jsonc``), project-root (``opencode.json``),
    global (``~/.config/opencode/opencode.json``). Returns only candidates that
    exist and parse successfully as JSONC objects.
    """
    candidates: list[DiscoveredConfig] = []
    project_path = project_root / _PROJECT_CONFIG_REL
    candidate = _try_parse(project_path, "project")
    if candidate is not None:
        candidates.append(candidate)
    root_path = project_root / "opencode.json"
    if root_path.resolve() != project_path.resolve():
        candidate = _try_parse(root_path, "project-root")
        if candidate is not None:
            candidates.append(candidate)
    global_path = Path.home() / _GLOBAL_CONFIG_REL
    candidate = _try_parse(global_path, "global")
    if candidate is not None:
        candidates.append(candidate)
    return tuple(candidates)


def target_path(project_root: Path, policy: ConfigTargetPolicy) -> Path:
    """Resolve the writable path selected by a typed project policy."""
    match policy:
        case ConfigTargetPolicy.PROJECT_DOT_OPENCODE:
            return project_root / _PROJECT_CONFIG_REL
        case ConfigTargetPolicy.PROJECT_ROOT:
            return project_root / "opencode.json"
        case unreachable:
            assert_never(unreachable)


def select_target_candidate(
    candidates: tuple[DiscoveredConfig, ...],
    project_root: Path,
    policy: ConfigTargetPolicy,
) -> DiscoveredConfig | None:
    """Return the discovered candidate matching the typed writable target."""
    expected = target_path(project_root, policy).resolve()
    for candidate in candidates:
        if candidate.path.resolve() == expected:
            return candidate
    return None


def select_config_target(
    candidates: tuple[DiscoveredConfig, ...],
    project_root: Path,
    policy: ConfigTargetPolicy,
) -> ConfigTarget | None:
    """Resolve the typed writable target: existing candidate, fresh path, or None.

    Fresh only when no project-level config exists (global stays read-only);
    None when a project config exists at a different path (no competing path).
    """
    existing = select_target_candidate(candidates, project_root, policy)
    if existing is not None:
        return ExistingConfigTarget(discovered=existing)
    if any(candidate.source in _PROJECT_SOURCES for candidate in candidates):
        return None
    return FreshConfigTarget(path=target_path(project_root, policy))


def is_path_observed(debug_value: JsonDict | None, path: Path) -> bool:
    """True when an exact resolved candidate path appears in debug output."""
    if debug_value is None:
        return False
    target = path.resolve()
    haystack = json.dumps(debug_value)
    native = str(target)
    escaped = native.replace("\\", "\\\\")
    return native in haystack or target.as_posix() in haystack or escaped in haystack


def prove_path_observed(
    debug_value: JsonDict | None,
    project_root: Path,
    config_rel_path: str,
) -> bool:
    """True when the resolved project config path appears in debug output.

    Checks the native path, POSIX form, and JSON-escaped form (Windows
    backslash doubling) to handle cross-platform path representation.
    Returns False when ``debug_value`` is None (opencode absent).
    """
    return is_path_observed(debug_value, project_root / config_rel_path)


def prove_projection_loaded(
    debug_value: JsonDict | None, projection: ConfigProjection,
) -> bool:
    """True when the runtime merged config carries the candidate projection.

    Post-write semantic proof for fresh targets: the top-level model must
    match and, when the candidate declares the selected provider entry, the
    runtime entry must carry the same npm adapter, baseURL endpoint, model
    keys, and (presence-only) apiKey. Secret values are never compared.
    Returns False when ``debug_value`` is None (opencode absent).
    """
    if debug_value is None or debug_value.get("model") != projection.model_ref:
        return False
    expected = projection.provider
    if expected is None:
        return True
    providers = debug_value.get("provider")
    entry = providers.get(expected.provider_id) if isinstance(providers, dict) else None
    if not isinstance(entry, dict):
        return False
    if expected.npm is not None and entry.get("npm") != expected.npm:
        return False
    raw_options = entry.get("options")
    options = raw_options if isinstance(raw_options, dict) else {}
    if expected.base_url is not None and options.get("baseURL") != expected.base_url:
        return False
    if expected.has_api_key:
        key = options.get("apiKey")
        if not (isinstance(key, str) and key.strip()):
            return False
    if not expected.model_ids:
        return True
    raw_models = entry.get("models")
    return isinstance(raw_models, dict) and all(
        model_id in raw_models for model_id in expected.model_ids)


def summarize_structure(value: JsonDict) -> SafeDetail:
    """Zero-secret structural summary: schema, providers, models, plugins.

    Secret values are redacted via :func:`redact_json_value` before any field
    is read. The returned summary never contains apiKey or token content.
    """
    redacted_raw = redact_json_value(value)
    if not isinstance(redacted_raw, dict):
        return _safe("(empty config)")
    schema_part = (
        "$schema:present" if "$schema" in redacted_raw else "$schema:absent"
    )
    providers_raw = redacted_raw.get("provider")
    if isinstance(providers_raw, dict):
        parts: list[str] = []
        for name, entry in providers_raw.items():
            models = entry.get("models") if isinstance(entry, dict) else None
            count = len(models) if isinstance(models, dict) else 0
            parts.append(f"{name}({count} models)")
        providers_part = "providers:" + ",".join(parts)
    else:
        providers_part = "providers:(none)"
    plugins_raw = redacted_raw.get("plugin")
    plugin_count = len(plugins_raw) if isinstance(plugins_raw, list) else 0
    raw = f"{schema_part}; {providers_part}; plugins:{plugin_count}"
    return _safe(raw)
