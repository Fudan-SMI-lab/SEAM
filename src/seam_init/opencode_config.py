"""Discover and transactionally configure OpenCode providers/models.

Facade re-exporting discovery and selection types plus the orchestrator that
drives: discover → summarize → authorize → prove observed → interactive
selection → API-key flow → schema validation → model-list validation →
ConfigTransaction commit. Never writes before live-path proof, schema
validation, and model-list validation all pass.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol, final, runtime_checkable

from seam_init.config_transaction import ConfigTransaction, TransactionResult
from seam_init.models import ProviderSelection, SafeDetail
from seam_init.opencode_adapters import (
    OpencodeCommand,
    OpencodeSchemaValidator,
    SubprocessRuntimePort,
)
from seam_init.opencode_discovery import (
    ConfigTargetPolicy,
    DiscoveredConfig,
    JsonDict,
    RuntimePort,
    discover_config_candidates,
    discover_project_config,
    is_path_observed,
    prove_path_observed,
    select_target_candidate,
    summarize_structure,
)
from seam_init.opencode_selection import (
    CustomProviderSpec,
    PromptPort,
    apply_selection,
    build_custom_provider_override,
    collect_api_key,
    provider_has_api_key,
    select_provider_model,
)
from core.secret_redaction import redact_sensitive_text

__all__ = [
    "ConfigFact", "ConfigTargetPolicy", "CustomProviderSpec", "DebugConfigPort",
    "DiscoveredConfig", "JsonDict", "OpencodeConfigError",
    "OpencodeCommand", "OpencodeConfigRequest", "OpencodeConfigResult",
    "OpencodeSchemaValidator", "PromptPort",
    "RuntimePort", "SchemaValidator", "apply_selection",
    "build_custom_provider_override", "collect_api_key",
    "configure_opencode", "discover_config_candidates",
    "discover_project_config", "prove_project_path_observed",
    "select_provider_model", "SubprocessRuntimePort", "summarize_structure",
]

_PROJECT_CONFIG_REL: Final[str] = ".opencode/opencode.jsonc"
_NORMALIZATION_CONSENT: Final[str] = (
    "Writing this config will normalize JSONC: comments and trailing-comma "
    "formatting will be lost. The exact original is retained in an owner-only "
    "backup. Authorize the provider/model merge?"
)


def _safe(raw: str) -> SafeDetail:
    return SafeDetail(redact_sensitive_text(raw))


@final
class OpencodeConfigError(Exception):
    """Typed discovery/parse error; carries only secret-free SafeDetail."""

    safe_detail: SafeDetail

    def __init__(self, *, safe_detail: SafeDetail) -> None:
        super().__init__(str(safe_detail))
        self.safe_detail = safe_detail


@runtime_checkable
class SchemaValidator(Protocol):
    """Injectable boundary for live v1 schema validation."""

    def validate(self, config_bytes: bytes) -> bool: ...


@runtime_checkable
class DebugConfigPort(Protocol):
    """Backward-compatible alias for RuntimePort.debug_config consumers."""

    def read(self) -> JsonDict | None: ...


@final
@dataclass(frozen=True, slots=True)
class ConfigFact:
    """One secret-free observation about a configuration outcome."""

    kind: str
    detail: SafeDetail


@final
@dataclass(frozen=True, slots=True)
class OpencodeConfigRequest:
    """All orchestrator inputs as one typed value."""

    project_root: Path
    prompt: PromptPort
    runtime: RuntimePort
    schema_validator: SchemaValidator
    selection: ProviderSelection | None = None
    api_key: str | None = None
    custom: CustomProviderSpec | None = None
    target_policy: ConfigTargetPolicy = ConfigTargetPolicy.PROJECT_DOT_OPENCODE


@final
@dataclass(frozen=True, slots=True)
class OpencodeConfigResult:
    """Frozen outcome: committed flag, pending-auth flag, secret-free facts."""

    committed: bool
    pending_auth: bool
    facts: tuple[ConfigFact, ...]
    transaction: TransactionResult | None
    safe_detail: SafeDetail
    selection: ProviderSelection | None = None


def prove_project_path_observed(
    debug_value: JsonDict | None,
    project_root: Path,
    config_rel_path: str,
) -> bool:
    """Backward-compatible facade for :func:`prove_path_observed`."""
    return prove_path_observed(debug_value, project_root, config_rel_path)


def _abort(
    facts: list[ConfigFact], detail: str,
) -> OpencodeConfigResult:
    return OpencodeConfigResult(
        committed=False,
        pending_auth=False,
        facts=tuple(facts),
        transaction=None,
        safe_detail=_safe(detail),
    )


def configure_opencode(request: OpencodeConfigRequest) -> OpencodeConfigResult:
    """Orchestrate discover, summarize, authorize, prove, select, validate, commit."""
    root = request.project_root
    facts: list[ConfigFact] = []

    # 1. Discover candidates.
    candidates = discover_config_candidates(root)
    discovered = select_target_candidate(candidates, root, request.target_policy)
    if discovered is None:
        kind = "NO_PROJECT_CONFIG" if not candidates else "TARGET_NOT_FOUND"
        facts.append(ConfigFact(kind, _safe("selected project config not found")))
        return _abort(facts, "selected project config not found")

    # 2. Zero-secret summary.
    summary = summarize_structure(discovered.value)
    print(f"OpenCode config summary: {summary}", flush=True)

    # 3. Merge authorization.
    if not request.prompt.confirm(_NORMALIZATION_CONSENT, default=False):
        facts.append(ConfigFact("MERGE_DECLINED", summary))
        return _abort(facts, "merge declined by user")
    facts.append(ConfigFact("MERGE_AUTHORIZED", summary))

    # 4. Interactive selection (if not pre-built).
    selection = request.selection
    custom = request.custom
    if selection is None:
        models = request.runtime.debug_models() or ()
        pair = select_provider_model(request.prompt, discovered.value, models)
        if pair is None:
            facts.append(ConfigFact("SELECTION_CANCELLED", _safe("selection cancelled")))
            return _abort(facts, "provider/model selection cancelled")
        selection, custom = pair

    # 5. API-key flow when neither request nor selected provider has auth.
    api_key = request.api_key
    existing_auth = provider_has_api_key(discovered.value, selection.provider_id)
    if api_key is None and not existing_auth:
        api_key = collect_api_key(request.prompt)

    # 6. Build merged config.
    merged = apply_selection(discovered.value, selection, api_key, custom)
    pending_auth = not (bool(api_key) or existing_auth)
    if pending_auth:
        facts.append(ConfigFact("AUTH_SKIPPED", _safe("api key skipped; pending auth")))
    else:
        facts.append(ConfigFact("AUTH_PROVIDED", _safe("api key supplied to provider options")))
    merged_bytes = json.dumps(merged, indent=2, ensure_ascii=False).encode("utf-8")

    # 7. Prove path observed BEFORE any transaction.
    debug_cfg = request.runtime.debug_config()
    if not is_path_observed(debug_cfg, discovered.path):
        facts.append(ConfigFact("PATH_UNOBSERVED", _safe("project path not observed")))
        return _abort(facts, "project config not observed; write aborted")
    facts.append(ConfigFact("PATH_OBSERVED", _safe("project config path observed")))

    # 8. Schema validation BEFORE any transaction.
    if not request.schema_validator.validate(merged_bytes):
        facts.append(ConfigFact("SCHEMA_INVALID", _safe("schema validation failed")))
        return _abort(facts, "schema validation failed; original bytes intact")
    facts.append(ConfigFact("SCHEMA_VALID", _safe("schema validation passed")))

    # 9. Model-list validation BEFORE any transaction.
    model_ref = f"{selection.provider_id}/{selection.model_id}"
    debug_models = request.runtime.debug_models(merged_bytes)
    if debug_models is None:
        facts.append(ConfigFact("MODEL_DATA_UNAVAILABLE", _safe("model data unavailable")))
        return _abort(facts, "opencode model validation unavailable")
    if model_ref not in debug_models:
        facts.append(ConfigFact("MODEL_NOT_FOUND", _safe(f"model {model_ref} not in models list")))
        return _abort(facts, f"model {model_ref} not found in OpenCode provider catalog")
    facts.append(ConfigFact("MODEL_VALIDATED", _safe(f"model {model_ref} validated")))

    # 10. ConfigTransaction commit.
    tx = ConfigTransaction(root)
    tx_id = tx.begin((discovered.path,))
    result = tx.commit(tx_id, {discovered.path: merged_bytes})
    if result.committed:
        facts.append(ConfigFact("COMMITTED", _safe(f"committed; tx={result.transaction_id}")))
        return OpencodeConfigResult(
            committed=True, pending_auth=pending_auth, facts=tuple(facts),
            transaction=result, safe_detail=_safe("provider/model merge committed"),
            selection=selection,
        )
    return OpencodeConfigResult(
        committed=False, pending_auth=pending_auth, facts=tuple(facts),
        transaction=result, safe_detail=_safe("commit rolled back"),
    )
