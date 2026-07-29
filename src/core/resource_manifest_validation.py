from __future__ import annotations

import typing

from pydantic import ValidationError

from .resource_manifest_models import (
    EnvironmentRecord,
    FactProvenance,
    ProbeReceipt,
    ResourceManifest,
    ResourceManifestError,
    ResourceManifestErrorKind,
    ResourceManifestUpdate,
)
from .resource_manifest_provenance import (
    environment_namespace,
    require_initial_observation_sources,
    require_observed_receipts,
    require_receipt_contexts,
    require_update_observation_sources,
)
from .resource_manifest_semantics import (
    require_backend_resource_context,
    require_semantic_fact_uniqueness,
)

_REQUIRED_INITIAL_FACTS = frozenset(
    {
        "launcher.python_executable",
        "launcher.python_realpath",
        "launcher.python_implementation",
        "launcher.python_version",
        "launcher.platform",
        "launcher.architecture",
        "launcher.cwd",
        "workflow.requested",
        "workflow.effective",
        "backend.requested",
        "backend.effective",
        "backend.local_identity",
        "container.attachment_mode",
        "container.owner_kind",
        "container.original_owner_run_id",
        "container.lineage_root_run_id",
        "container.framework_ownership_token_sha256",
        "container.framework_ownership_label",
        "container.runtime",
        "container.id",
        "container.image",
        "container.probe_status",
        "opencode.endpoint",
        "opencode.version",
        "opencode.owner_kind",
        "opencode.pid",
        "ownership.resource_owner_kind",
        "retention.requested",
        "retention.effective",
        "lifecycle.status",
    }
)
_REQUIRED_ENVIRONMENT_FACTS = frozenset(
    {
        "environment.type",
        "environment.namespace",
        "environment.container_id",
        "interpreter.realpath",
        "interpreter.sys_executable",
        "interpreter.sys_prefix",
        "interpreter.sys_base_prefix",
        "python.implementation",
        "python.version",
        "platform.system",
        "platform.architecture",
        "packages.inventory_sha256",
    }
)


def _error(
    kind: ResourceManifestErrorKind,
    detail: str,
) -> ResourceManifestError:
    return ResourceManifestError(kind, detail)


def validate_manifest_structure(manifest: ResourceManifest) -> None:
    names = frozenset(fact.name for fact in manifest.facts)
    if not _REQUIRED_INITIAL_FACTS.issubset(names):
        raise _error(
            ResourceManifestErrorKind.MALFORMED,
            "resource manifest is missing required runtime facts",
        )
    if len(manifest.facts) != len(set(manifest.facts)):
        raise _error(
            ResourceManifestErrorKind.DUPLICATE_FACT, "duplicate resource fact"
        )
    require_semantic_fact_uniqueness(manifest.facts)
    require_initial_observation_sources(manifest.facts)
    environment_ids = tuple(item.environment_id for item in manifest.environments)
    if len(environment_ids) != len(set(environment_ids)):
        raise _error(ResourceManifestErrorKind.DUPLICATE_FACT, "duplicate environment")
    for environment in manifest.environments:
        environment_names = frozenset(fact.name for fact in environment.facts)
        if not _REQUIRED_ENVIRONMENT_FACTS.issubset(environment_names):
            raise _error(
                ResourceManifestErrorKind.MALFORMED,
                f"environment is incomplete: {environment.environment_id}",
            )
        _ = environment_namespace(environment)
        require_observed_receipts(environment, manifest.probe_receipts)
    environment_namespaces = {
        item.environment_id: environment_namespace(item)
        for item in manifest.environments
    }
    require_receipt_contexts(
        manifest.facts, manifest.environments, manifest.probe_receipts
    )
    require_backend_resource_context(manifest)
    for reference in manifest.phase5_environment_references:
        target = reference.environment_reference
        valid = (
            target.name == "phase5.environment_id"
            and target.provenance is FactProvenance.DERIVED
            and target.value in environment_ids
            and target.namespace == environment_namespaces.get(target.value)
        )
        if not valid:
            raise _error(
                ResourceManifestErrorKind.RUN_CONTEXT_MISMATCH,
                f"Phase 5 reference is invalid: {reference.attempt_id}",
            )
    lifecycle = tuple(
        fact.value for fact in manifest.facts if fact.name == "lifecycle.status"
    )
    terminal = lifecycle and lifecycle[-1] in {
        "passed",
        "passed_with_reviews",
        "failed",
        "cancelled",
        "error",
    }
    if manifest.sealed != bool(terminal):
        raise _error(
            ResourceManifestErrorKind.SEALED,
            "seal state differs from terminal lifecycle fact",
        )


def _merge_environment(
    current: EnvironmentRecord,
    update: EnvironmentRecord,
) -> EnvironmentRecord:
    if current.environment_id != update.environment_id:
        raise _error(
            ResourceManifestErrorKind.RUN_CONTEXT_MISMATCH,
            "environment update targets another identity",
        )
    if environment_namespace(current) != environment_namespace(update):
        raise _error(
            ResourceManifestErrorKind.RUN_CONTEXT_MISMATCH,
            "environment update crosses namespaces",
        )
    combined = current.facts + tuple(
        fact for fact in update.facts if fact not in current.facts
    )
    try:
        return EnvironmentRecord(
            environment_id=current.environment_id,
            facts=combined,
        )
    except ValidationError as exc:
        raise _error(ResourceManifestErrorKind.DUPLICATE_FACT, str(exc)) from exc


def _merge_environments(
    current: typing.Tuple[EnvironmentRecord, ...],
    updates: typing.Tuple[EnvironmentRecord, ...],
    receipts: typing.Tuple[ProbeReceipt, ...],
) -> typing.Tuple[EnvironmentRecord, ...]:
    merged = list(current)
    positions = {item.environment_id: index for index, item in enumerate(merged)}
    for update in updates:
        require_observed_receipts(update, receipts)
        position = positions.get(update.environment_id)
        if position is None:
            positions[update.environment_id] = len(merged)
            merged.append(update)
        else:
            merged[position] = _merge_environment(merged[position], update)
    return tuple(merged)


def merge_update(
    current: ResourceManifest,
    update: ResourceManifestUpdate,
    terminal_seal: bool = False,
) -> ResourceManifest:
    if update.expected_revision != current.revision:
        raise _error(
            ResourceManifestErrorKind.STALE_WRITE,
            f"expected revision {update.expected_revision}, current is {current.revision}",
        )
    require_update_observation_sources(update.facts)
    combined_facts = current.facts + update.facts
    if len(combined_facts) != len(set(combined_facts)):
        raise _error(
            ResourceManifestErrorKind.DUPLICATE_FACT, "duplicate resource fact"
        )
    references = (
        current.phase5_environment_references + update.phase5_environment_references
    )
    attempts = tuple(item.attempt_id for item in references)
    if len(attempts) != len(set(attempts)):
        raise _error(
            ResourceManifestErrorKind.DUPLICATE_FACT, "duplicate Phase 5 attempt"
        )
    receipts = current.probe_receipts + update.probe_receipts
    probe_ids = tuple(item.probe_id for item in receipts)
    if len(probe_ids) != len(set(probe_ids)):
        raise _error(
            ResourceManifestErrorKind.DUPLICATE_FACT, "duplicate probe receipt"
        )
    environments = _merge_environments(
        current.environments, update.environments, update.probe_receipts
    )
    require_receipt_contexts(combined_facts, environments, receipts)
    try:
        manifest = ResourceManifest.model_validate(
            {
                **current.model_dump(by_alias=True, mode="json"),
                "revision": current.revision + 1,
                "facts": combined_facts,
                "environments": environments,
                "phase5_environment_references": references,
                "probe_receipts": receipts,
            }
        )
        if not terminal_seal:
            validate_manifest_structure(manifest)
        return manifest
    except ValidationError as exc:
        raise _error(ResourceManifestErrorKind.MALFORMED, str(exc)) from exc
