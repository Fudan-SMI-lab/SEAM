from __future__ import annotations

from collections.abc import Iterable

from core.resource_manifest import (
    EnvironmentRecord,
    FactProvenance,
    FactStatus,
    ProvenanceFact,
    ResourceManifest,
)
from core.v3_runtime_report_models import RuntimeEnvironmentReport, RuntimeFact

_NONPUBLIC_FACTS = frozenset(
    {
        "container.framework_ownership_label",
        "container.framework_ownership_token",
        "retention.entry_command",
    }
)


def runtime_fact(fact: ProvenanceFact) -> RuntimeFact:
    return RuntimeFact(
        name=fact.name,
        value=fact.value,
        provenance=fact.provenance,
        namespace=fact.namespace,
        status=fact.status,
        detail=fact.detail,
    )


def runtime_section(
    facts: Iterable[ProvenanceFact],
    prefixes: tuple[str, ...],
) -> tuple[RuntimeFact, ...]:
    return tuple(
        runtime_fact(fact)
        for fact in facts
        if fact.name.startswith(prefixes) and fact.name not in _NONPUBLIC_FACTS
    )


def known_fact(
    facts: Iterable[ProvenanceFact],
    name: str,
) -> ProvenanceFact | None:
    selected = tuple(
        fact for fact in facts if fact.name == name and fact.status is FactStatus.KNOWN
    )
    return selected[-1] if selected else None


def authenticated_fact(
    facts: Iterable[ProvenanceFact],
    name: str,
    provenance: FactProvenance,
) -> ProvenanceFact | None:
    fact = known_fact(facts, name)
    if fact is None or fact.provenance is not provenance or fact.authority_tag is None:
        return None
    return fact


def active_environment_id(
    manifest: ResourceManifest,
    accepted_attempt_id: str | None,
) -> str | None:
    if accepted_attempt_id is not None:
        references = tuple(
            reference
            for reference in manifest.phase5_environment_references
            if reference.attempt_id == accepted_attempt_id
        )
        if len(references) == 1:
            return references[0].environment_reference.value
        return None
    if len(manifest.environments) == 1:
        return manifest.environments[0].environment_id
    return None


def environment_report(environment: EnvironmentRecord) -> RuntimeEnvironmentReport:
    return RuntimeEnvironmentReport(
        environment_id=environment.environment_id,
        facts=tuple(runtime_fact(fact) for fact in environment.facts),
    )
