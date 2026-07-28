from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core import resource_retention_lifecycle
from core.execution_backend import ContainerBackend
from core.resource_manifest import ResourceManifestError, ResourceManifestStore
from core.resource_retention import (
    ContainerCleanupStatus,
    ContainerDeletionError,
    ContainerRetention,
    resolve_v3_container_retention,
)
from core.resource_retention_finalizer import (
    ContainerRetentionFinalizer,
    RetentionLifecycleRecorder,
    _authorized_retention_finalization,
)
from core.resource_retention_manifest import (
    RetentionManifestFinalizer,
    RetentionManifestRequest,
    create_retention_manifest,
)
from core.resource_retention_lifecycle import (
    RetentionLifecycleRecord,
)
from core.types import ExecutionBackendConfig, WorkflowDefinition


def _retained_store(
    tmp_path: Path,
    *,
    container_id: str = "immutable-id",
    workspace: Path | None = None,
    workflow_path: Path | None = None,
) -> tuple[
    ResourceManifestStore,
    ContainerBackend,
    ContainerRetentionFinalizer,
    RetentionLifecycleRecorder,
]:
    report_dir = tmp_path / "reports" / "run-safe-17"
    report_dir.mkdir(parents=True)
    workspace = workspace or tmp_path / "output-project"
    workspace.mkdir(parents=True, exist_ok=True)
    workflow_path = workflow_path or tmp_path / "workflow.yaml"
    workflow_path.parent.mkdir(parents=True, exist_ok=True)
    workflow_path.write_text("name: retention-test\n", encoding="utf-8")
    config = ExecutionBackendConfig.from_dict(
        {"mode": "container", "source": "image", "image": "cpu:test"}
    )
    workflow = WorkflowDefinition(
        name="retention-test",
        version="1.0",
        phases=[],
        terminals=["complete"],
        execution_backend=config,
    )
    policy = resolve_v3_container_retention(
        workflow, ContainerRetention.RETAIN, "run-safe-17"
    )
    assert policy.delete_authority is not None
    backend = ContainerBackend._for_v3(config, policy.delete_authority)
    backend._container_id = container_id
    recorder = RetentionLifecycleRecorder()
    store = create_retention_manifest(
        RetentionManifestRequest(
            report_dir=report_dir,
            run_id="run-safe-17",
            requested_workflow=workflow_path,
            effective_workflow=workflow_path,
            workspace=workspace,
            requested_backend="container",
            endpoint="http://127.0.0.1:4096",
            server_process_id=None,
            policy=policy,
            backend=backend,
        )
    )
    return (
        store,
        backend,
        ContainerRetentionFinalizer(policy, backend, workspace, recorder),
        recorder,
    )


def test_authenticated_manifest_persists_complete_retained_lifecycle(
    tmp_path: Path,
) -> None:
    # Given a materialized image workflow and framework-owned retained container.
    store, backend, finalizer, recorder = _retained_store(tmp_path)
    manifest_finalizer = RetentionManifestFinalizer(store, recorder, backend)

    # When cleanup observes the container and the frozen PASS lifecycle seals.
    with patch("core.execution_backend.subprocess.run") as run:
        run.return_value = MagicMock(
            returncode=0,
            stdout="running|immutable-id\n",
            stderr="",
        )
        with _authorized_retention_finalization(finalizer):
            finalizer.run()
    artifact = manifest_finalizer.persist_and_seal("passed")

    # Then every retention fact is truthful in the authenticated sealed artifact.
    manifest = store.read()
    facts = {fact.name: fact.value for fact in manifest.facts}
    lifecycle = tuple(
        fact for fact in manifest.facts if fact.name.startswith("retention.")
    )
    assert artifact == store.path
    assert manifest.sealed is True
    assert facts["retention.requested"] == "retain"
    assert facts["retention.effective"] == "retain"
    assert facts["retention.owner_kind"] == "framework"
    assert facts["retention.entry_command"] == "docker exec -it immutable-id bash"
    assert facts["retention.pre_state"] == "running"
    assert facts["retention.post_state"] == "running"
    assert facts["retention.cleanup_result"] == "retained"
    assert facts["retention.continuation_available"] == "false"
    assert facts["lifecycle.status"] == "passed"
    assert all(
        fact.authority_tag is not None
        for fact in lifecycle
        if fact.name not in {"retention.requested", "retention.effective"}
    )


def test_authenticated_ownership_fact_tampering_fails_closed(tmp_path: Path) -> None:
    # Given a Task 17 manifest whose ownership facts have authenticated receipts.
    store, _backend, _finalizer, _recorder = _retained_store(tmp_path)
    payload = json.loads(store.path.read_text(encoding="utf-8"))
    for fact in payload["facts"]:
        if fact["name"] == "container.framework_ownership_token":
            fact["value"] = "attacker-controlled-token"
    store.path.write_text(json.dumps(payload), encoding="utf-8")

    # When the authoritative store reopens the modified manifest.
    with pytest.raises(
        ResourceManifestError,
        match="provenance_escalation|authority_mismatch",
    ):
        _ = store.read()

    # Then forged ownership cannot become deletion authority.
    assert store.path.is_file()


def test_retention_manifest_rejects_backend_identity_replacement(
    tmp_path: Path,
) -> None:
    # Given a manifest captured for one immutable backend identity.
    store, backend, finalizer, recorder = _retained_store(tmp_path)
    manifest_finalizer = RetentionManifestFinalizer(store, recorder, backend)
    with patch("core.execution_backend.subprocess.run") as run:
        run.return_value = MagicMock(
            returncode=0,
            stdout="running|immutable-id\n",
            stderr="",
        )
        with _authorized_retention_finalization(finalizer):
            finalizer.run()
    backend._container_id = "replacement-id"

    # When terminal persistence observes a different backend identity.
    with pytest.raises(ResourceManifestError, match="identity.*changed"):
        _ = manifest_finalizer.persist_and_seal("passed")

    # Then stale authenticated identity is never sealed as final evidence.
    assert store.read().sealed is False


def test_public_fake_backend_cannot_mint_lifecycle_authority(tmp_path: Path) -> None:
    # Given a public retention finalizer backed by caller-controlled observations.
    store, backend, finalizer, recorder = _retained_store(tmp_path)
    with patch("core.execution_backend.subprocess.run") as run:
        run.return_value = MagicMock(
            returncode=0,
            stdout="running|immutable-id\n",
            stderr="",
        )

        # When the caller invokes it outside the authorized finalization stage.
        with pytest.raises(ContainerDeletionError, match="finalization stage"):
            finalizer.run()

    # Then the public manifest finalizer has no measured record to authenticate.
    with pytest.raises(ContainerDeletionError, match="measurement capability"):
        _ = RetentionManifestFinalizer(store, recorder, backend).persist_and_seal(
            "passed"
        )
    assert store.read().sealed is False


def test_forged_record_source_cannot_mint_lifecycle_authority(tmp_path: Path) -> None:
    # Given a structural source that returns caller-selected lifecycle facts.
    store, backend, _finalizer, _recorder = _retained_store(tmp_path)

    class ForgedRecordSource:
        def require_record(self, _backend):
            return RetentionLifecycleRecord(
                requested=ContainerRetention.RETAIN,
                effective=ContainerRetention.RETAIN,
                owner_kind="framework",
                entry_command="docker exec -it immutable-id bash",
                pre_state="running",
                post_state="running",
                cleanup_status=ContainerCleanupStatus.RETAINED,
                continuation_available=True,
            )

    # When the forged source reaches the lifecycle persistence boundary.
    writer = resource_retention_lifecycle.__dict__["write_measured_retention_lifecycle"]
    with pytest.raises(ContainerDeletionError, match="measurement capability"):
        _ = writer(
            store,
            ForgedRecordSource(),
            backend,
        )

    # Then no caller-selected lifecycle record is written or sealed.
    assert store.read().sealed is False


def test_retention_stage_grant_is_not_a_public_hook_api() -> None:
    # Given the runtime retention hook module.
    from harness.run import v3_retention

    # When ordinary callers inspect its public composition surface.
    public_names = tuple(name for name in dir(v3_retention) if not name.startswith("_"))

    # Then no API can grant the private authorized measurement stage.
    assert "compose_v3_retention_hooks" not in public_names
    assert "V3AuthorizedResourceCleanup" not in public_names
