from __future__ import annotations

import typing

from .resource_manifest_models import (
    ProbeReceipt,
    ProvenanceFact,
    ResourceManifest,
    ResourceManifestError,
    ResourceManifestErrorKind,
    ResourceManifestIdentity,
)
from .resource_manifest_validation import (
    _REQUIRED_INITIAL_FACTS,
    validate_manifest_structure,
)


def build_initial_manifest(
    identity: ResourceManifestIdentity,
    facts: typing.Tuple[ProvenanceFact, ...],
    probe_receipts: typing.Tuple[ProbeReceipt, ...] = (),
) -> ResourceManifest:
    names = frozenset(fact.name for fact in facts)
    missing = tuple(sorted(_REQUIRED_INITIAL_FACTS - names))
    if missing:
        raise ResourceManifestError(
            ResourceManifestErrorKind.MALFORMED,
            f"initial resource facts are missing: {', '.join(missing)}",
        )
    manifest = ResourceManifest(
        run_id=identity.run_id,
        workflow_digest=identity.workflow_digest,
        workspace_digest=identity.workspace_digest,
        revision=1,
        sealed=False,
        facts=facts,
        probe_receipts=probe_receipts,
    )
    validate_manifest_structure(manifest)
    return manifest
