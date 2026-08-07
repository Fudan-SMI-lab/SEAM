from __future__ import annotations

import typing

from .resource_manifest_models import (
    EnvironmentRecord,
    FactProvenance,
    FactStatus,
    ProbeReceipt,
    ProvenanceFact,
    ResourceManifestError,
    ResourceManifestErrorKind,
)


def _error(
    kind: ResourceManifestErrorKind,
    detail: str,
) -> ResourceManifestError:
    return ResourceManifestError(kind, detail)


def environment_namespace(environment: EnvironmentRecord) -> str:
    namespaces = frozenset(
        fact.value
        for fact in environment.facts
        if fact.name == "environment.namespace" and fact.status is FactStatus.KNOWN
    )
    namespace = next(iter(namespaces), None)
    if namespace is None or len(namespaces) != 1:
        raise _error(
            ResourceManifestErrorKind.MALFORMED,
            f"environment namespace is ambiguous: {environment.environment_id}",
        )
    if any(fact.namespace != namespace for fact in environment.facts):
        raise _error(
            ResourceManifestErrorKind.RUN_CONTEXT_MISMATCH,
            f"environment facts cross namespaces: {environment.environment_id}",
        )
    container_ids = frozenset(
        fact.value
        for fact in environment.facts
        if fact.name == "environment.container_id"
        and fact.status is FactStatus.KNOWN
        and fact.value is not None
    )
    expected_container = (
        namespace.partition(":")[2] if namespace.startswith("container:") else None
    )
    expected_ids: typing.FrozenSet[str] = (
        frozenset({expected_container}) if expected_container else frozenset()
    )
    if container_ids != expected_ids:
        raise _error(
            ResourceManifestErrorKind.RUN_CONTEXT_MISMATCH,
            f"environment container identity is inconsistent: {environment.environment_id}",
        )
    return namespace


def require_initial_observation_sources(
    facts: typing.Tuple[ProvenanceFact, ...],
) -> None:
    invalid = tuple(
        fact.name
        for fact in facts
        if fact.provenance is FactProvenance.FRAMEWORK_OBSERVED
        and fact.authority_tag is None
    )
    if invalid:
        raise _error(
            ResourceManifestErrorKind.PROVENANCE_ESCALATION,
            f"top-level observed facts lack authority tags: {', '.join(invalid)}",
        )


def require_update_observation_sources(
    facts: typing.Tuple[ProvenanceFact, ...],
) -> None:
    if any(
        fact.provenance is FactProvenance.FRAMEWORK_OBSERVED
        and fact.authority_tag is None
        for fact in facts
    ):
        raise _error(
            ResourceManifestErrorKind.PROVENANCE_ESCALATION,
            "top-level observed facts require a trusted capture boundary",
        )


def require_observed_receipts(
    environment: EnvironmentRecord,
    receipts: typing.Tuple[ProbeReceipt, ...],
) -> None:
    namespace = environment_namespace(environment)
    for fact in environment.facts:
        observed = fact.provenance is FactProvenance.FRAMEWORK_OBSERVED
        verified = any(
            receipt.status is fact.status
            and receipt.environment_id == environment.environment_id
            and receipt.namespace == namespace
            and fact in receipt.verified_facts
            for receipt in receipts
        )
        if observed and not verified:
            raise _error(
                ResourceManifestErrorKind.PROVENANCE_ESCALATION,
                f"observed fact lacks matching probe receipt: {fact.name}",
            )


def require_receipt_contexts(
    top_level_facts: typing.Tuple[ProvenanceFact, ...],
    environments: typing.Tuple[EnvironmentRecord, ...],
    receipts: typing.Tuple[ProbeReceipt, ...],
) -> None:
    namespaces = {
        environment.environment_id: environment_namespace(environment)
        for environment in environments
    }
    facts = {
        environment.environment_id: frozenset(environment.facts)
        for environment in environments
    }
    for receipt in receipts:
        if receipt.environment_id.startswith("resource-"):
            if not frozenset(receipt.verified_facts).issubset(top_level_facts):
                raise _error(
                    ResourceManifestErrorKind.PROVENANCE_ESCALATION,
                    f"top-level receipt verifies detached facts: {receipt.probe_id}",
                )
            continue
        if namespaces.get(receipt.environment_id) != receipt.namespace:
            raise _error(
                ResourceManifestErrorKind.RUN_CONTEXT_MISMATCH,
                f"probe receipt targets another environment: {receipt.probe_id}",
            )
        if not frozenset(receipt.verified_facts).issubset(
            facts[receipt.environment_id]
        ):
            raise _error(
                ResourceManifestErrorKind.PROVENANCE_ESCALATION,
                f"probe receipt verifies detached facts: {receipt.probe_id}",
            )
