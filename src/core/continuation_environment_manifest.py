from __future__ import annotations

from .continuation_environment_models import (
    ContinuationEnvironmentError,
    ContinuationEnvironmentErrorKind,
)
from .resource_manifest_models import FactStatus, ProvenanceFact


def error(
    kind: ContinuationEnvironmentErrorKind,
    field: str,
    detail: str,
) -> ContinuationEnvironmentError:
    return ContinuationEnvironmentError(kind, field, detail)


def known_fact(
    facts: tuple[ProvenanceFact, ...],
    name: str,
    *,
    required: bool = True,
) -> str | None:
    values = frozenset(
        fact.value
        for fact in facts
        if fact.name == name
        and fact.status is FactStatus.KNOWN
        and fact.value is not None
    )
    if len(values) > 1:
        ownership = name.startswith(
            (
                "container.owner",
                "container.original",
                "container.lineage",
                "container.framework",
            )
        )
        kind = (
            ContinuationEnvironmentErrorKind.OWNERSHIP_AMBIGUOUS
            if ownership
            else ContinuationEnvironmentErrorKind.ENVIRONMENT_MISMATCH
        )
        raise error(kind, name, "recorded fact has multiple values")
    value = next(iter(values), None)
    if required and value is None:
        raise error(
            ContinuationEnvironmentErrorKind.ENVIRONMENT_MISSING,
            name,
            "recorded fact is unavailable",
        )
    return value


def required_fact(facts: tuple[ProvenanceFact, ...], name: str) -> str:
    value = known_fact(facts, name)
    if value is None:
        raise AssertionError("required manifest fact unexpectedly absent")
    return value
