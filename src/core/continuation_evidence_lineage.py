from __future__ import annotations

from .continuation_evidence_models import (
    ChildEvidenceRequest,
    ContinuationEvidenceError,
    ContinuationEvidenceErrorKind,
)
from .continuation_models import ResolvedTerminalParent
from .run_manifest import CanonicalReference, RunId, RunManifest


def _verified_inherited(
    parent: ResolvedTerminalParent,
    request: ChildEvidenceRequest,
) -> tuple[CanonicalReference, ...]:
    seen_paths: set[str] = set()
    for reference in request.inherited_canonical:
        phase_id = str(reference.phase_id)
        suffix = phase_id[6:] if phase_id.startswith("phase_") else phase_id
        expected_name = f"phase_{suffix}_canonical.json"
        expected_path = f"validated/{expected_name}"
        matches = tuple(
            item
            for item in parent.run_manifest.sealed_evidence
            if item.relative_path == expected_path
        )
        valid = (
            reference.artifact_name == expected_name
            and len(matches) == 1
            and matches[0].digest == reference.digest
            and expected_path not in seen_paths
        )
        if not valid:
            raise ContinuationEvidenceError(
                ContinuationEvidenceErrorKind.INHERITED_REFERENCE_INVALID,
                f"inherited canonical reference is not parent evidence: {phase_id}",
            )
        seen_paths.add(expected_path)
    return request.inherited_canonical


def build_child_manifest(
    parent: ResolvedTerminalParent,
    request: ChildEvidenceRequest,
) -> RunManifest:
    return RunManifest(
        run_id=RunId(request.continuation.child_run_id),
        parent_run_id=parent.run_id,
        lineage_root_run_id=parent.run_manifest.lineage_root_run_id,
        revision=1,
        terminal_anchor=parent.terminal_anchor,
        workflow_digest=parent.workflow_digest,
        inherited_canonical=_verified_inherited(parent, request),
        shared_workspace=parent.run_manifest.shared_workspace,
        resource_references=parent.run_manifest.resource_references,
        parent_evidence_digests=parent.run_manifest.sealed_evidence,
        evidence_sealed=False,
        sealed_evidence=(),
    )
