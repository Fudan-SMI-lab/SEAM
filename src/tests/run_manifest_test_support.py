from __future__ import annotations

from pathlib import Path

from core.artifact_store import ArtifactStore
from core.run_manifest import (
    CanonicalReference,
    ResourceReference,
    RunId,
    RunManifest,
    RunManifestStore,
    RunStorageContext,
    Sha256Digest,
    SharedWorkspaceMarker,
)
from core.run_outcome import PhaseId, TerminalAnchor

WORKFLOW_DIGEST = Sha256Digest("a" * 64)
OTHER_DIGEST = Sha256Digest("b" * 64)
PARENT_RUN_ID = RunId("parent-run-001")


def storage_context(
    tmp_path: Path,
    workspace_name: str = "workspace",
    reports_name: str = "e2e-reports",
) -> RunStorageContext:
    workspace = tmp_path / workspace_name
    workspace.mkdir(parents=True, exist_ok=True)
    return RunStorageContext.bind(tmp_path / reports_name, workspace)


def root_manifest(
    context: RunStorageContext,
    run_id: RunId = PARENT_RUN_ID,
) -> RunManifest:
    return RunManifest(
        run_id=run_id,
        parent_run_id=None,
        lineage_root_run_id=run_id,
        revision=1,
        terminal_anchor=TerminalAnchor(phase_id=PhaseId("phase_5_validation")),
        workflow_digest=WORKFLOW_DIGEST,
        inherited_canonical=(),
        shared_workspace=SharedWorkspaceMarker(
            workspace_digest=context.workspace_digest
        ),
        resource_references=(
            ResourceReference(kind="environment", reference_id="resource-parent"),
        ),
        parent_evidence_digests=(),
        evidence_sealed=False,
        sealed_evidence=(),
    )


def child_manifest(parent: RunManifest) -> RunManifest:
    return RunManifest(
        run_id=RunId("child-run-001"),
        parent_run_id=parent.run_id,
        lineage_root_run_id=parent.lineage_root_run_id,
        revision=1,
        terminal_anchor=TerminalAnchor(phase_id=PhaseId("phase_5_validation")),
        workflow_digest=parent.workflow_digest,
        inherited_canonical=(
            CanonicalReference(
                phase_id=PhaseId("phase_3_entry_script"),
                artifact_name="phase_3_canonical.json",
                digest=Sha256Digest("d" * 64),
            ),
        ),
        shared_workspace=parent.shared_workspace,
        resource_references=(
            ResourceReference(kind="environment", reference_id="resource-child"),
        ),
        parent_evidence_digests=parent.sealed_evidence,
        evidence_sealed=False,
        sealed_evidence=(),
    )


def sealed_parent(
    tmp_path: Path,
) -> tuple[RunStorageContext, RunManifestStore, RunManifest]:
    context = storage_context(tmp_path)
    working = ArtifactStore(str(context.workspace_root), "parent-run-001")
    _ = working.mark_validated("phase_3_entry_script", {"status": "success"})
    writer = RunManifestStore.create(context, root_manifest(context))
    sealed = writer.seal_working_evidence(working)
    return (
        context,
        RunManifestStore.open_readonly(context, sealed.run_id, sealed.workflow_digest),
        sealed,
    )
