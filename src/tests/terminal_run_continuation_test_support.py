from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Final, Literal
from unittest.mock import Mock

import pytest
from pydantic import TypeAdapter

from core.artifact_store import ArtifactStore
from core.continuation import (
    ContinuationError,
    ContinuationErrorKind,
    ContinuationRequest,
    claim_terminal_parent,
)
from core.execution_env_context import BackendFactRequest, OpenCodeFactRequest
from core.resource_manifest import (
    ResourceManifestContext,
    ResourceManifestIdentity,
    ResourceManifestStore,
    build_backend_facts,
    build_initial_manifest,
    build_opencode_facts,
)
from core.run_manifest import (
    ResourceReference,
    RunId,
    RunManifest,
    RunManifestStore,
    RunStorageContext,
    Sha256Digest,
    SharedWorkspaceMarker,
)
from core.run_outcome import PhaseId, TerminalAnchor
from tests.terminal_run_continuation_receipt_support import (
    record_accepted_phase5_receipt,
)
from tests.terminal_run_continuation_parent_scenarios import (
    ParentCanonicalFixture,
    ParentRun,
    ParentRunScenario,
    SummaryPayload,
)
from tests.terminal_run_continuation_summary_support import build_summary_payload

PARENT_RUN_ID = RunId("parent-run-001")
CHILD_RUN_ID = RunId("child-run-001")
_JSON_ADAPTER: Final = TypeAdapter(SummaryPayload)


def create_parent_run(
    tmp_path: Path,
    *,
    status: str = "PASS",
    phase_status: str = "passed",
    anchor_phase: str = "phase_5_validation",
    scenario: ParentRunScenario | None = None,
) -> ParentRun:
    project_dir = tmp_path / "output-project"
    project_dir.mkdir()
    reports_root = tmp_path / "e2e-reports"
    workflow_path = tmp_path / "materialized-workflow.yaml"
    workflow_bytes = (
        scenario.workflow_bytes
        if scenario is not None
        else b"name: pinned-workflow\nversion: 1\n"
    )
    _ = workflow_path.write_bytes(workflow_bytes)
    workflow_digest = Sha256Digest(hashlib.sha256(workflow_bytes).hexdigest())
    context = RunStorageContext.bind(reports_root, project_dir)
    manifest = RunManifest(
        run_id=PARENT_RUN_ID,
        parent_run_id=None,
        lineage_root_run_id=PARENT_RUN_ID,
        revision=1,
        terminal_anchor=TerminalAnchor(phase_id=PhaseId(anchor_phase)),
        workflow_digest=workflow_digest,
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
    working = ArtifactStore(str(project_dir), str(PARENT_RUN_ID))
    canonical_outputs = (
        scenario.canonical_outputs
        if scenario is not None
        else (ParentCanonicalFixture("phase_5_validation", {"status": phase_status}),)
    )
    for canonical in canonical_outputs:
        _ = working.save_phase_output(
            canonical.phase_id, {"raw_phase": canonical.phase_id}
        )
        _ = working.mark_validated(canonical.phase_id, canonical.value)
    phase5_status = (
        next(
            (
                phase.status
                for phase in scenario.phases
                if phase.phase_id == "phase_5_validation"
            ),
            phase_status,
        )
        if scenario is not None
        else phase_status
    )
    if phase5_status == "passed":
        record_accepted_phase5_receipt(working, project_dir)
    run_store = RunManifestStore.create(context, manifest)
    _ = run_store.seal_working_evidence(working)
    report_dir = reports_root / str(PARENT_RUN_ID)
    identity = ResourceManifestIdentity(
        run_id=str(PARENT_RUN_ID),
        workflow_digest=str(workflow_digest),
        workspace_digest=str(context.workspace_digest),
    )
    resource_context = ResourceManifestContext.bind(report_dir, identity)
    launcher = resource_context.capture_launcher()
    facts = (
        launcher.facts
        + build_backend_facts(
            BackendFactRequest(
                requested_workflow=str(workflow_path),
                effective_workflow=str(workflow_path),
                requested_backend="local",
                effective_backend="local",
            )
        )
        + build_opencode_facts(
            OpenCodeFactRequest(
                endpoint="http://127.0.0.1:4096",
                version="1.18.5",
                owner_kind="framework",
                process_id="1234",
            )
        )
    )
    resource_store = ResourceManifestStore.create(
        resource_context,
        build_initial_manifest(identity, facts, (launcher.receipt,)),
    )
    lifecycle: Literal["passed", "failed"] = "passed" if status == "PASS" else "failed"
    _ = resource_store.seal(expected_revision=1, terminal_status=lifecycle)
    parent = ParentRun(
        summary_path=report_dir / "summary.json",
        report_dir=report_dir,
        reports_root=reports_root,
        project_dir=project_dir,
        workflow_path=workflow_path,
        run_manifest_path=report_dir / "run-manifest.v1.json",
        workflow_digest=workflow_digest,
    )
    write_summary(parent, build_summary_payload(parent, status, phase_status, scenario))
    return parent


def read_summary(parent: ParentRun) -> SummaryPayload:
    return read_json_payload(parent.summary_path)


def read_json_payload(path: Path) -> SummaryPayload:
    payload: SummaryPayload = _JSON_ADAPTER.validate_json(path.read_bytes())
    return payload


def write_summary(parent: ParentRun, payload: SummaryPayload) -> None:
    _ = parent.summary_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def tree_bytes(root: Path) -> tuple[tuple[str, bytes], ...]:
    return tuple(
        (path.relative_to(root).as_posix(), path.read_bytes())
        for path in sorted(root.rglob("*"))
        if path.is_file()
    )


def claim_rejection(
    parent: ParentRun,
    summary_path: Path | None = None,
    child_run_id: RunId = CHILD_RUN_ID,
) -> ContinuationErrorKind:
    parent_before = tree_bytes(parent.report_dir)
    project_before = tree_bytes(parent.project_dir)
    session_factory = Mock()
    backend_factory = Mock()
    child_artifact_factory = Mock()
    with pytest.raises(ContinuationError) as raised:
        with claim_terminal_parent(
            ContinuationRequest(
                summary_path=summary_path or parent.summary_path,
                child_run_id=child_run_id,
            )
        ):
            session_factory()
            backend_factory()
            child_artifact_factory()
    session_factory.assert_not_called()
    backend_factory.assert_not_called()
    child_artifact_factory.assert_not_called()
    assert tree_bytes(parent.report_dir) == parent_before
    assert tree_bytes(parent.project_dir) == project_before
    assert not (parent.reports_root / "locks").exists()
    return raised.value.kind
