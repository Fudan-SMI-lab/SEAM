from __future__ import annotations

from .execution_env_context import Phase5ReferenceRequest
from .resource_manifest_models import (
    FactProvenance,
    Phase5EnvironmentReference,
    ProvenanceFact,
)


def build_phase5_reference(
    request: Phase5ReferenceRequest,
) -> Phase5EnvironmentReference:
    return Phase5EnvironmentReference(
        attempt_id=request.attempt_id,
        environment_reference=ProvenanceFact(
            name="phase5.environment_id",
            value=request.environment_id,
            provenance=FactProvenance.DERIVED,
            namespace=request.namespace,
        ),
    )
