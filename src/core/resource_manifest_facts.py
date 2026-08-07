from __future__ import annotations

import typing
from typing import NamedTuple

from .resource_manifest_models import (
    FactProvenance,
    FactStatus,
    ProvenanceFact,
)


class Evidence(NamedTuple):
    value: typing.Optional[str]
    provenance: FactProvenance
    namespace: str
    detail: typing.Optional[str] = None


def build_fact(name: str, evidence: Evidence) -> ProvenanceFact:
    status = FactStatus.KNOWN if evidence.value is not None else FactStatus.UNKNOWN
    return ProvenanceFact(
        name=name,
        value=evidence.value,
        provenance=evidence.provenance,
        namespace=evidence.namespace,
        status=status,
        detail=evidence.detail,
    )
