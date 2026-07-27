from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, TypeAlias
from unittest.mock import Mock

import pytest
from pydantic import JsonValue, TypeAdapter

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

PARENT_RUN_ID = RunId("parent-run-001")
CHILD_RUN_ID = RunId("child-run-001")
SummaryPayload: TypeAlias = dict[str, JsonValue]
_JSON_ADAPTER: Final = TypeAdapter(SummaryPayload)


@dataclass(frozen=True, slots=True)
class ParentRun:
    summary_path: Path
    report_dir: Path
    reports_root: Path
    project_dir: Path
    workflow_path: Path
    run_manifest_path: Path
    workflow_digest: Sha256Digest


def _summary_payload(
    parent: ParentRun, status: str, phase_status: str
) -> SummaryPayload:
    return {
        "run_id": str(PARENT_RUN_ID),
        "base_url": "http://127.0.0.1:4096",
        "workflow_path": str(parent.workflow_path),
        "output_dir": str(parent.report_dir),
        "temp_dir": str(parent.project_dir),
        "keep_temp_dir": True,
        "requested_max_phase5_iter": 5,
        "effective_max_phase5_iter": 5,
        "phases": [
            {
                "phase_number": 5,
                "phase_id": "phase_5_validation",
                "label": "phase_5_validation",
                "status": phase_status,
                "duration_seconds": 1.25,
                "error": "validation failed" if phase_status == "failed" else None,
            }
        ],
        "session_count": 2,
        "command_count": 3,
        "overall_status": status,
        "total_duration_seconds": 4.5,
        "artifact_dir": None,
        "telemetry_paths": {},
        "before_snapshot_path": None,
        "after_snapshot_path": None,
        "entry_script": "python app.py",
        "errors": ["validation failed"] if status == "FAIL" else [],
        "review_timeout_observability": {
            "schema_version": "1.0",
            "review_count": 0,
            "reviews": [],
            "timeout_count": 0,
            "timeouts": [],
            "exhaustion_count": 0,
            "dropped_event_count": 0,
            "review_duration_seconds": 0.0,
            "timeout_elapsed_seconds": 0.0,
        },
    }


def create_parent_run(
    tmp_path: Path,
    *,
    status: str = "PASS",
    phase_status: str = "passed",
    anchor_phase: str = "phase_5_validation",
) -> ParentRun:
    project_dir = tmp_path / "output-project"
    project_dir.mkdir()
    reports_root = tmp_path / "e2e-reports"
    workflow_path = tmp_path / "materialized-workflow.yaml"
    workflow_bytes = b"name: pinned-workflow\nversion: 1\n"
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
    _ = working.mark_validated("phase_5_validation", {"status": phase_status})
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
    write_summary(parent, _summary_payload(parent, status, phase_status))
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
