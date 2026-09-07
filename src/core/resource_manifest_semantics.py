from __future__ import annotations

import typing

from .resource_manifest_models import (
    FactStatus,
    ProvenanceFact,
    ResourceManifest,
    ResourceManifestError,
    ResourceManifestErrorKind,
)
from .resource_manifest_provenance import environment_namespace

_HISTORICAL_FACTS = frozenset({"lifecycle.status"})


def _error(
    kind: ResourceManifestErrorKind,
    detail: str,
) -> ResourceManifestError:
    return ResourceManifestError(kind, detail)


def require_semantic_fact_uniqueness(
    facts: typing.Tuple[ProvenanceFact, ...],
) -> None:
    keys = tuple(
        (fact.name, fact.provenance, fact.namespace)
        for fact in facts
        if fact.name not in _HISTORICAL_FACTS
    )
    if len(keys) != len(set(keys)):
        raise _error(
            ResourceManifestErrorKind.DUPLICATE_FACT,
            "singleton facts must have one value per source and namespace",
        )


def _single_value(
    facts: typing.Tuple[ProvenanceFact, ...],
    name: str,
) -> typing.Optional[str]:
    values = frozenset(
        fact.value
        for fact in facts
        if fact.name == name
        and fact.status is FactStatus.KNOWN
        and fact.value is not None
    )
    if len(values) > 1:
        raise _error(
            ResourceManifestErrorKind.RUN_CONTEXT_MISMATCH,
            f"resource fact is ambiguous: {name}",
        )
    return next(iter(values), None)


def require_backend_resource_context(manifest: ResourceManifest) -> None:
    backend = _single_value(manifest.facts, "backend.effective")
    container_id = _single_value(manifest.facts, "container.id")
    if backend == "local":
        expected_namespace = "host"
    elif backend == "container" and container_id is not None:
        expected_namespace = f"container:{container_id}"
    else:
        raise _error(
            ResourceManifestErrorKind.RUN_CONTEXT_MISMATCH,
            "effective backend lacks a consistent resource identity",
        )
    for environment in manifest.environments:
        if environment_namespace(environment) != expected_namespace:
            raise _error(
                ResourceManifestErrorKind.RUN_CONTEXT_MISMATCH,
                f"environment differs from backend resource: {environment.environment_id}",
            )
    attachment = _single_value(manifest.facts, "container.attachment_mode")
    original_owner = _single_value(manifest.facts, "container.original_owner_run_id")
    if attachment == "image_created" and original_owner != manifest.run_id:
        raise _error(
            ResourceManifestErrorKind.RUN_CONTEXT_MISMATCH,
            "image-created container owner must be the current run",
        )
