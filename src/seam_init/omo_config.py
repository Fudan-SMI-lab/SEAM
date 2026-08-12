"""Configure the live OMO model mapping across legacy/current generations.

Transactional paths: fresh create, legacy migration (dry-run + consent), and
existing normalize (variant->reasoning). All share one model-finalisation step
honoring ``selected_model``/``selected_reasoning`` when provided. Capability
is version-gated (``pluginVersion`` major 5); doctor ``configPath``/
``configValid`` are NOT used.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum, unique
from pathlib import Path
from typing import Final, Protocol, final, runtime_checkable

from core.jsonc import (
    JsonValue,
    JsoncParseError,
    error_kind_label,
    parse_config_object,
)
from core.secret_redaction import redact_json_value, redact_sensitive_text
from seam_init.config_transaction import ConfigTransaction, TransactionResult
from seam_init.models import SafeDetail
from seam_init.omo_profile import (
    DEFAULT_REASONING,
    apply_model_to_all,
    build_canonical_with_preserved,
    fill_missing_models,
)
from seam_init.omo_sources import (
    HomePathPolicy,
    LegacySource,
    collect_models,
    discover_legacy_sources,
    normalize_config,
    seam_validate,
    select_provider_model_reasoning,
    summarize_config,
    validate_model_catalog,
)
from seam_init.opencode_discovery import JsonDict, RuntimePort
from seam_init.opencode_selection import PromptPort

__all__ = [
    "CURRENT_SCHEMA_URL", "CURRENT_TARGET_REL", "DefaultHome", "DryRunResult",
    "DryRunStatus", "GLOBAL_LEGACY_REL", "HomePathPolicy", "LEGACY_SIDECAR_REL",
    "LEGACY_SOURCE_RELS", "OmoCapabilityPort", "OmoConfigFact", "OmoConfigRequest",
    "OmoConfigResult", "SNAPSHOT_SUFFIXES", "SchemaCapability",
    "configure_omo", "discover_legacy_sources_all",
]

CURRENT_TARGET_REL: Final[str] = ".omo/omo.jsonc"
CURRENT_SCHEMA_URL: Final[str] = (
    "https://raw.githubusercontent.com/code-yeongyu/oh-my-openagent"
    "/dev/assets/omo.schema.json"
)
LEGACY_SOURCE_RELS: Final[tuple[str, ...]] = (
    ".opencode/oh-my-openagent.json", ".opencode/oh-my-openagent.jsonc",
    ".opencode/oh-my-opencode.json", ".opencode/oh-my-opencode.jsonc",
)
GLOBAL_LEGACY_REL: Final[str] = ".omo/config.jsonc"
LEGACY_SIDECAR_REL: Final[str] = ".opencode/oh-my-openagent.json.migrations.json"
SNAPSHOT_SUFFIXES: Final[tuple[str, ...]] = ("_gpt", "_glm", "_deepseek", "_k3", "_temp")
_FRESH_CONSENT: Final[str] = (
    "No existing OMO config found. Create a fresh .omo/omo.jsonc with "
    "canonical agent/category mappings? The file is written transactionally.")
_MIGRATION_CONSENT: Final[str] = (
    "Migrate legacy OMO config to the current .omo/omo.jsonc format? "
    "The legacy source, snapshots, and migration sidecar are preserved.")
_NORMALIZE_CONSENT: Final[str] = (
    "Normalizing will convert variant->reasoning and update the schema URL. "
    "The exact original is retained in an owner-only backup. Continue?")


def _safe(raw: str) -> SafeDetail:
    return SafeDetail(redact_sensitive_text(raw))


@unique
class DryRunStatus(str, Enum):
    UNSUPPORTED = "unsupported"
    SUCCESS = "success"
    FAILURE = "failure"


@final
@dataclass(frozen=True, slots=True)
class DryRunResult:
    status: DryRunStatus
    output: JsonDict | None


@final
@dataclass(frozen=True, slots=True)
class SchemaCapability:
    schema_url: str
    reasoning_values: tuple[str, ...]
    version: str
    schema_document: JsonDict


@runtime_checkable
class OmoCapabilityPort(Protocol):
    def resolve_capability(self) -> SchemaCapability | None: ...
    def migrate_dry_run(self) -> DryRunResult: ...


@final
class DefaultHome:
    def home(self) -> Path:
        return Path.home()


@final
@dataclass(frozen=True, slots=True)
class OmoConfigFact:
    kind: str
    detail: SafeDetail


@final
@dataclass(frozen=True, slots=True)
class OmoConfigRequest:
    project_root: Path
    prompt: PromptPort
    capability_port: OmoCapabilityPort
    runtime: RuntimePort
    home: HomePathPolicy = field(default_factory=DefaultHome)
    selected_model: str | None = None
    selected_reasoning: str | None = None
    precomputed_models: tuple[str, ...] | None = None


@final
@dataclass(frozen=True, slots=True)
class OmoConfigResult:
    committed: bool
    migrated: bool
    fresh: bool
    facts: tuple[OmoConfigFact, ...]
    transaction: TransactionResult | None
    safe_detail: SafeDetail
    plugin_version: str = ""


def discover_legacy_sources_all(
    project_root: Path, home: HomePathPolicy = DefaultHome(),
) -> tuple[LegacySource, ...]:
    return discover_legacy_sources(
        project_root, LEGACY_SOURCE_RELS, GLOBAL_LEGACY_REL, home)


def _fact(kind: str, detail: str) -> OmoConfigFact:
    return OmoConfigFact(kind, _safe(detail))


def _abort(facts: list[OmoConfigFact], detail: str) -> OmoConfigResult:
    return OmoConfigResult(
        committed=False, migrated=False, fresh=False, facts=tuple(facts),
        transaction=None, safe_detail=_safe(detail))


def _serialize(config: dict[str, JsonValue]) -> bytes:
    return json.dumps(config, indent=2, ensure_ascii=False).encode("utf-8") + b"\n"


def configure_omo(request: OmoConfigRequest) -> OmoConfigResult:
    """Resolve capability, build candidate, validate, and commit."""
    root = request.project_root
    facts: list[OmoConfigFact] = []
    tx = ConfigTransaction(root)
    _ = tx.recover_interrupted()

    cap = request.capability_port.resolve_capability()
    if cap is None:
        return _abort([_fact("CAPABILITY_UNAVAILABLE", "capability unavailable")],
                       "OMO capability unavailable or unsupported")
    if request.precomputed_models is not None:
        model_list = request.precomputed_models
    else:
        model_list = request.runtime.debug_models()
    if model_list is None:
        return _abort([_fact("MODEL_DATA_UNAVAILABLE", "model list unavailable")],
                       "opencode model list unavailable")
    catalog_err = validate_model_catalog(model_list)
    if catalog_err is not None:
        return _abort([_fact("CATALOG_INVALID", catalog_err)],
                       f"runtime model catalog invalid: {catalog_err}")
    facts.append(_fact("FACTS_DETECTED", f"version={cap.version}; models={len(model_list)}"))

    target_path = root / CURRENT_TARGET_REL
    migrated = False
    fresh = False
    if target_path.is_file():
        existing = _read_existing(target_path)
        if isinstance(existing, OmoConfigFact):
            return _abort([existing], str(existing.detail))
        base = existing
        print(f"Current OMO config summary: {summarize_config(base)}", flush=True)
        if not request.prompt.confirm(_NORMALIZE_CONSENT, default=False):
            return _abort([_fact("NORMALIZE_DECLINED", "normalize declined")],
                           "normalization declined by user")
        facts.append(_fact("NORMALIZE_AUTHORIZED", "normalize authorized"))
        current = normalize_config(base, cap.schema_url)
    elif (legacy := discover_legacy_sources_all(root, request.home)):
        current = _migrate_path(request, cap, legacy, facts)
        if current is None:
            return _abort(facts, "migration aborted")
        migrated = True
    else:
        if not request.prompt.confirm(_FRESH_CONSENT, default=True):
            return _abort([_fact("FRESH_DECLINED", "fresh config declined")],
                           "fresh config declined by user")
        facts.append(_fact("FRESH_AUTHORIZED", "fresh config authorized"))
        fresh = True
        current = {}

    current = _finalize_mappings(request, cap, model_list, current, facts)
    if current is None:
        return _abort(facts, "model selection cancelled")

    config_bytes = _serialize(current)
    issues = seam_validate(
        config_bytes, cap.schema_document, cap.reasoning_values, model_list)
    if issues:
        facts.append(_fact("VALIDATION_FAILED", "; ".join(issues[:3])))
        return _abort(facts, f"candidate validation failed: {issues[0]}")
    facts.append(_fact("VALIDATION_PASSED", f"{len(model_list)} runtime models"))

    target_rel = Path(CURRENT_TARGET_REL)
    tx_id = tx.begin((target_rel,))

    def _commit_validate(updates: Mapping[Path, bytes]) -> bool:
        return all(
            not seam_validate(d, cap.schema_document, cap.reasoning_values, model_list)
            for d in updates.values())
    result = tx.commit(tx_id, {target_rel: config_bytes}, validate=_commit_validate)
    if result.committed:
        facts.append(_fact("COMMITTED", f"tx={result.transaction_id}"))
        return OmoConfigResult(
            committed=True, migrated=migrated, fresh=fresh, facts=tuple(facts),
            transaction=result, safe_detail=_safe("OMO config committed"),
            plugin_version=cap.version)
    facts.append(_fact("COMMIT_ROLLED_BACK", "commit rolled back"))
    return OmoConfigResult(
        committed=False, migrated=migrated, fresh=False, facts=tuple(facts),
        transaction=result, plugin_version=cap.version,
        safe_detail=_safe("commit rolled back; prior state restored"))


def _read_existing(path: Path) -> dict[str, JsonValue] | OmoConfigFact:
    """Read the existing target; an unreadable/malformed file fails closed."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return _fact("EXISTING_UNREADABLE",
                     f"existing OMO config unreadable ({type(exc).__name__})")
    except ValueError:
        return _fact("EXISTING_MALFORMED",
                     "existing OMO config malformed (undecodable text)")
    try:
        parsed = parse_config_object(text)
    except ValueError as exc:
        label = (error_kind_label(exc.error.kind)
                 if isinstance(exc, JsoncParseError) else "invalid JSON")
        return _fact("EXISTING_MALFORMED",
                     f"existing OMO config malformed ({label})")
    if not isinstance(parsed.value, dict):
        return _fact("EXISTING_MALFORMED",
                     "existing OMO config malformed (non-object root)")
    return parsed.value


def _migrate_path(
    request: OmoConfigRequest, cap: SchemaCapability,
    legacy: tuple[LegacySource, ...], facts: list[OmoConfigFact],
) -> dict[str, JsonValue] | None:
    dry_run = request.capability_port.migrate_dry_run()
    if dry_run.status is DryRunStatus.FAILURE:
        facts.append(_fact("DRY_RUN_FAILED", "dry-run failed"))
        return None
    if dry_run.status is DryRunStatus.SUCCESS and dry_run.output is not None:
        print(f"OMO migration dry-run: {json.dumps(redact_json_value(dry_run.output))}",
              flush=True)
        facts.append(_fact("DRY_RUN_PRESENTED", "dry-run presented"))
    if not request.prompt.confirm(_MIGRATION_CONSENT, default=False):
        facts.append(_fact("MIGRATION_DECLINED", "migration declined"))
        return None
    facts.append(_fact("MIGRATION_AUTHORIZED", "migration authorized"))
    return normalize_config(dict(legacy[0].value), cap.schema_url)


def _finalize_mappings(
    request: OmoConfigRequest, cap: SchemaCapability,
    model_list: tuple[str, ...], current: dict[str, JsonValue],
    facts: list[OmoConfigFact],
) -> dict[str, JsonValue] | None:
    """Unified model resolution for fresh/existing/legacy: select, generate, apply."""
    if request.selected_model is not None and request.selected_model in model_list:
        model = request.selected_model
        reasoning = request.selected_reasoning or DEFAULT_REASONING
    elif len(model_list) == 1:
        model, reasoning = model_list[0], DEFAULT_REASONING
    else:
        selected = select_provider_model_reasoning(
            request.prompt, model_list, cap.reasoning_values)
        if selected is None:
            facts.append(_fact("SELECTION_CANCELLED", "model selection cancelled"))
            return None
        model, reasoning = selected
    facts.append(_fact("MODEL_RESOLVED", f"{model}/{reasoning}"))
    if not collect_models(current):
        facts.append(_fact("CANONICAL_GENERATED", "from empty mappings"))
        return build_canonical_with_preserved(cap.schema_url, model, reasoning, current)
    if len(model_list) > 1:
        return apply_model_to_all(current, model, reasoning)
    return fill_missing_models(current, model, reasoning)
