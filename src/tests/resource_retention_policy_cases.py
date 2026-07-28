from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.continuation_environment import (
    ContainerDeleteForbidden,
    ExistingContainerAttachment,
    Phase2EstablishmentEligible,
)
from core.resource_manifest import ResourceManifestError, ResourceManifestErrorKind
from core.resource_retention import (
    ContainerDeletionError,
    ContainerRetention,
    CurrentRunContainerDeleteAuthority,
    resolve_v3_container_retention,
)
from core.resource_retention_finalizer import (
    ContainerRetentionFinalizer,
    RetentionLifecycleRecorder,
    _authorized_retention_finalization,
    retention_manifest_update,
)
from core.run_outcome import TerminalOutcome
from core.types import WorkflowDefinition
from harness.run import FinalizationHooks, RunArtifactUpdate
from harness.run.v3_retention import compose_v3_retention_hooks
from tests.resource_retention_test_support import RecordingBackend, container_workflow


def _evidence_available() -> bool:
    return True


def test_retain_policy_overrides_legacy_image_cleanup() -> None:
    # Given a materialized V3 image workflow with legacy cleanup enabled.
    workflow = container_workflow()
    # When the V3 retention policy is resolved before backend construction.
    policy = resolve_v3_container_retention(
        workflow, ContainerRetention.RETAIN, "run-safe-17"
    )
    # Then runtime auto-remove is disabled and framework ownership is retained.
    assert workflow.execution_backend is not None
    assert workflow.execution_backend.cleanup is False
    assert policy.effective is ContainerRetention.RETAIN
    assert isinstance(policy.delete_authority, CurrentRunContainerDeleteAuthority)
    assert policy.delete_authority.ownership_label == "seam.owner=run-safe-17"


def test_delete_policy_defers_owned_image_removal() -> None:
    # Given an explicitly requested delete policy for a V3 image workflow.
    workflow = container_workflow()
    # When policy is resolved.
    policy = resolve_v3_container_retention(
        workflow, ContainerRetention.DELETE, "run-safe-17"
    )
    # Then deletion remains explicit while Docker --rm stays disabled.
    assert workflow.execution_backend is not None
    assert workflow.execution_backend.cleanup is False
    assert policy.effective is ContainerRetention.DELETE
    assert policy.delete_authority is not None


def test_external_existing_container_is_structurally_retained() -> None:
    # Given a direct V3 workflow that attaches to an external container.
    workflow = container_workflow("existing_container")
    # When deletion is requested without Task 13 authority.
    policy = resolve_v3_container_retention(
        workflow, ContainerRetention.DELETE, "run-safe-17"
    )
    # Then no deletion capability exists and the effective policy is retain.
    assert policy.owner_kind == "external"
    assert policy.effective is ContainerRetention.RETAIN
    assert policy.delete_authority is None


def test_retain_finalizer_has_no_delete_side_effect() -> None:
    # Given a retained framework container and an output project.
    policy = resolve_v3_container_retention(
        container_workflow(), ContainerRetention.RETAIN, "run-safe-17"
    )
    backend = RecordingBackend()
    recorder = RetentionLifecycleRecorder()
    # When post-evidence cleanup runs.
    ContainerRetentionFinalizer(
        policy, backend, Path.cwd(), recorder, _evidence_available
    ).run()
    # Then the container remains available and no delete command is requested.
    record = recorder.require_record()
    assert backend.delete_calls == []
    assert record.cleanup_status.value == "retained"
    assert record.continuation_available is True
    assert record.entry_command == "docker exec -it immutable-id bash"


def test_owned_delete_finalizer_records_removed_state() -> None:
    # Given an explicitly deletable current-run image container.
    policy = resolve_v3_container_retention(
        container_workflow(), ContainerRetention.DELETE, "run-safe-17"
    )
    backend = RecordingBackend()
    recorder = RetentionLifecycleRecorder()
    # When post-evidence cleanup runs.
    finalizer = ContainerRetentionFinalizer(policy, backend, Path.cwd(), recorder)
    with _authorized_retention_finalization(finalizer):
        finalizer.run()
    # Then exactly one typed deletion occurs and continuation becomes unavailable.
    record = recorder.require_record()
    assert len(backend.delete_calls) == 1
    assert record.cleanup_status.value == "deleted"
    assert record.post_state == "absent"
    assert record.continuation_available is False


def test_cleanup_failure_is_persistable_without_losing_policy() -> None:
    # Given owned cleanup that fails after the container stops.
    policy = resolve_v3_container_retention(
        container_workflow(), ContainerRetention.DELETE, "run-safe-17"
    )
    failure = ContainerDeletionError(
        "immutable-id", "running", "stopped", "runtime remove failed"
    )
    recorder = RetentionLifecycleRecorder()
    # When explicit deletion fails.
    finalizer = ContainerRetentionFinalizer(
        policy, RecordingBackend(failure), Path.cwd(), recorder
    )
    with _authorized_retention_finalization(finalizer):
        with pytest.raises(ContainerDeletionError):
            finalizer.run()
    # Then the frozen policy and truthful partial state are available to the manifest.
    record = recorder.require_record()
    facts = {
        fact.name: fact.value
        for fact in retention_manifest_update(record, expected_revision=3).facts
    }
    assert record.requested is ContainerRetention.DELETE
    assert facts["retention.cleanup_result"] == "failed"
    assert facts["retention.post_state"] == "stopped"
    assert facts["retention.continuation_available"] == "false"


def test_task13_forbidden_deletion_remains_retain() -> None:
    # Given an attached user container with Task 13 deletion forbidden.
    attachment = ExistingContainerAttachment(
        mode="existing_container",
        runtime="docker",
        container_id="immutable-id",
        container_name="user-container",
        owner_kind="user",
        original_owner_run_id=None,
        lineage_root_run_id=None,
        ownership_token=None,
        ownership_label=None,
    )
    eligibility = Phase2EstablishmentEligible(
        attachment=attachment,
        deletion=ContainerDeleteForbidden("container is user or external"),
    )
    # When deletion is requested.
    policy = resolve_v3_container_retention(
        container_workflow("existing_container"),
        ContainerRetention.DELETE,
        "child-run",
        eligibility,
    )
    # Then the request cannot produce a deletion capability.
    assert policy.effective is ContainerRetention.RETAIN
    assert policy.delete_authority is None


@pytest.mark.parametrize("auto_remove_flag", ("--rm", "--rm=true"))
def test_retain_policy_strips_runtime_auto_remove_flag(
    auto_remove_flag: str,
) -> None:
    # Given a V3 image workflow that smuggles auto-remove through runtime flags.
    workflow = container_workflow()
    assert workflow.execution_backend is not None
    workflow.execution_backend.runtime_flags = [auto_remove_flag, "--init"]
    # When retain policy resolves before backend construction.
    policy = resolve_v3_container_retention(
        workflow, ContainerRetention.RETAIN, "run-safe-17"
    )
    # Then no Docker auto-remove spelling can reach the retained backend.
    assert policy.effective is ContainerRetention.RETAIN
    assert workflow.execution_backend.runtime_flags == ["--init"]


def test_manifest_setup_failure_preserves_authorized_cleanup(tmp_path: Path) -> None:
    # Given requested deletion and a manifest initialization failure after preflight.
    policy = resolve_v3_container_retention(
        container_workflow(), ContainerRetention.DELETE, "run-safe-17"
    )
    backend = RecordingBackend()
    cleanup = ContainerRetentionFinalizer(
        policy, backend, tmp_path, RetentionLifecycleRecorder()
    )
    resources = MagicMock(return_value=RunArtifactUpdate())
    failure = ResourceManifestError(
        ResourceManifestErrorKind.WRITE_INTERRUPTED,
        "manifest initialization failed",
    )
    hooks = compose_v3_retention_hooks(
        FinalizationHooks.empty(), resources, cleanup, None, failure
    )
    # When finalization runs cleanup and then required manifest publication.
    _ = hooks.authorized_cleanup(TerminalOutcome.PASSED)
    with pytest.raises(ResourceManifestError, match="initialization failed"):
        _ = hooks.post_cleanup_manifest(TerminalOutcome.PASSED)
    # Then the preflight-created owned container was still deleted exactly once.
    assert len(backend.delete_calls) == 1


@pytest.mark.parametrize(
    ("evidence_available", "expected"),
    ((False, False), (True, True)),
)
def test_local_continuation_availability_requires_finalization_evidence(
    tmp_path: Path,
    evidence_available: bool,
    expected: bool,
) -> None:
    # Given a retained local output project with or without complete evidence.
    policy = resolve_v3_container_retention(
        WorkflowDefinition(
            name="local-retention",
            version="1.0",
            phases=[],
            terminals=["complete"],
        ),
        ContainerRetention.RETAIN,
        "run-safe-17",
    )
    recorder = RetentionLifecycleRecorder()

    def finalized_evidence() -> bool:
        return evidence_available

    # When the lifecycle records a run without a container.
    ContainerRetentionFinalizer(
        policy, None, tmp_path, recorder, finalized_evidence
    ).run()
    # Then availability requires both the project and finalized evidence.
    assert recorder.require_record().continuation_available is expected
