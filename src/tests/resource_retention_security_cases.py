from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.continuation import ContinuationRequest, claim_terminal_parent
from core.continuation_environment import (
    ExistingContainerAttachment,
    FrameworkContainerDeleteEligible,
    Phase2EstablishmentEligible,
)
from core.continuation_environment_models import (
    _verified_framework_container_delete_eligibility,
)
from core.execution_backend import ContainerBackend
from core.resource_retention import (
    ContainerDeletionError,
    ContainerRetention,
    CurrentRunContainerDeleteAuthority,
    _authorized_container_cleanup,
    resolve_v3_container_retention,
)
from core.resource_retention_finalizer import (
    ContainerRetentionFinalizer,
    RetentionLifecycleRecorder,
    _authorized_retention_finalization,
)
from core.run_manifest import RunId
from tests.resource_retention_test_support import RecordingBackend, container_workflow
from tests.terminal_run_continuation_test_support import create_parent_run


def test_released_continuation_lock_prohibits_delete_side_effect(
    tmp_path: Path,
) -> None:
    # Given Task 13 eligibility resolved while the Task 11 owner lock is active.
    parent = create_parent_run(tmp_path)
    attachment = ExistingContainerAttachment(
        mode="existing_container",
        runtime="docker",
        container_id="immutable-id",
        container_name="seam-parent",
        owner_kind="framework",
        original_owner_run_id="parent-run-001",
        lineage_root_run_id="parent-run-001",
        ownership_token="owner-token",
        ownership_label="seam.owner=parent-run-001",
    )
    eligibility = Phase2EstablishmentEligible(
        attachment=attachment,
        deletion=_verified_framework_container_delete_eligibility(
            "parent-run-001",
            "parent-run-001",
            "owner-token",
            "seam.owner=parent-run-001",
        ),
    )
    with claim_terminal_parent(
        ContinuationRequest(
            summary_path=parent.summary_path,
            child_run_id=RunId("child-run"),
        )
    ):
        policy = resolve_v3_container_retention(
            container_workflow("existing_container"),
            ContainerRetention.DELETE,
            "child-run",
            eligibility,
        )
    backend = RecordingBackend()
    recorder = RetentionLifecycleRecorder()
    # When cleanup runs after the owner lock has been released.
    finalizer = ContainerRetentionFinalizer(
        policy, backend, parent.project_dir, recorder
    )
    with _authorized_retention_finalization(finalizer):
        with pytest.raises(ContainerDeletionError, match="active project owner lock"):
            finalizer.run()
    # Then stop/remove remains prohibited despite the stale typed eligibility.
    assert backend.delete_calls == []
    assert recorder.require_record().cleanup_status.value == "failed"
    assert policy.delete_authority is not None
    workflow = container_workflow("existing_container")
    assert workflow.execution_backend is not None
    real_backend = ContainerBackend._for_v3(
        workflow.execution_backend, policy.delete_authority
    )
    real_backend._container_id = "immutable-id"
    with patch("core.execution_backend.subprocess.run") as run:
        with _authorized_container_cleanup(policy.delete_authority):
            with pytest.raises(ContainerDeletionError, match="does not match"):
                _ = real_backend.delete_container(policy.delete_authority)
    run.assert_not_called()


def test_fabricated_task13_eligibility_cannot_mint_delete_authority(
    tmp_path: Path,
) -> None:
    # Given attacker-constructed ownership records and a legitimate active lock.
    parent = create_parent_run(tmp_path)
    attachment = ExistingContainerAttachment(
        mode="existing_container",
        runtime="docker",
        container_id="victim-id",
        container_name="victim",
        owner_kind="framework",
        original_owner_run_id="parent-run-001",
        lineage_root_run_id="parent-run-001",
        ownership_token="attacker-token",
        ownership_label="com.external.service=prod",
    )
    fabricated = Phase2EstablishmentEligible(
        attachment=attachment,
        deletion=FrameworkContainerDeleteEligible(
            "parent-run-001",
            "parent-run-001",
            "attacker-token",
            "com.external.service=prod",
        ),
    )
    # When the fabricated record is supplied during the legitimate claim.
    with claim_terminal_parent(
        ContinuationRequest(
            summary_path=parent.summary_path,
            child_run_id=RunId("child-run"),
        )
    ):
        policy = resolve_v3_container_retention(
            container_workflow("existing_container"),
            ContainerRetention.DELETE,
            "child-run",
            fabricated,
        )
    # Then unverified field equality cannot create destructive authority.
    assert policy.effective is ContainerRetention.RETAIN
    assert policy.delete_authority is None


def test_cloned_task13_proof_cannot_authorize_modified_ownership(
    tmp_path: Path,
) -> None:
    # Given a legitimate proof copied onto attacker-selected ownership facts.
    parent = create_parent_run(tmp_path)
    verified = _verified_framework_container_delete_eligibility(
        "parent-run-001",
        "parent-run-001",
        "owner-token",
        "seam.owner=parent-run-001",
    )
    cloned = replace(
        verified,
        ownership_token="attacker-token",
        ownership_label="seam.owner=victim",
    )
    attachment = ExistingContainerAttachment(
        mode="existing_container",
        runtime="docker",
        container_id="victim-id",
        container_name="victim",
        owner_kind="framework",
        original_owner_run_id="parent-run-001",
        lineage_root_run_id="parent-run-001",
        ownership_token="attacker-token",
        ownership_label="seam.owner=victim",
    )
    eligibility = Phase2EstablishmentEligible(
        attachment=attachment,
        deletion=cloned,
    )

    # When copied proof is presented under a legitimate active project lock.
    with claim_terminal_parent(
        ContinuationRequest(
            summary_path=parent.summary_path,
            child_run_id=RunId("child-run"),
        )
    ):
        policy = resolve_v3_container_retention(
            container_workflow("existing_container"),
            ContainerRetention.DELETE,
            "child-run",
            eligibility,
        )

    # Then proof remains bound to its originally verified ownership facts.
    assert policy.effective is ContainerRetention.RETAIN
    assert policy.delete_authority is None


def test_retention_finalizer_cannot_self_authorize_deletion(tmp_path: Path) -> None:
    # Given requested deletion outside the harness finalization stage.
    policy = resolve_v3_container_retention(
        container_workflow(), ContainerRetention.DELETE, "run-safe-17"
    )
    backend = RecordingBackend()
    recorder = RetentionLifecycleRecorder()

    # When the public finalizer is invoked directly.
    with pytest.raises(ContainerDeletionError, match="finalization stage"):
        ContainerRetentionFinalizer(policy, backend, tmp_path, recorder).run()

    # Then it cannot grant itself deletion authority.
    assert backend.delete_calls == []


def test_ownership_token_under_wrong_label_key_prohibits_delete(
    tmp_path: Path,
) -> None:
    # Given an owned backend whose live token moved to an unrelated label key.
    workflow = container_workflow()
    policy = resolve_v3_container_retention(
        workflow, ContainerRetention.DELETE, "run-safe-17"
    )
    assert workflow.execution_backend is not None
    assert isinstance(policy.delete_authority, CurrentRunContainerDeleteAuthority)
    backend = ContainerBackend._for_v3(
        workflow.execution_backend, policy.delete_authority
    )
    backend._container_id = "immutable-id"
    recorder = RetentionLifecycleRecorder()
    # When finalization observes the malformed ownership-label schema.
    with patch("core.execution_backend.subprocess.run") as run:
        run.side_effect = [
            MagicMock(
                returncode=0,
                stdout=(
                    'running|immutable-id|{"seam.owner":"run-safe-17",'
                    f'"attacker.alias":"{policy.delete_authority.ownership_token}"}}\n'
                ),
                stderr="",
            ),
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=0, stdout="", stderr=""),
        ]
        finalizer = ContainerRetentionFinalizer(policy, backend, tmp_path, recorder)
        with _authorized_retention_finalization(finalizer):
            with pytest.raises(ContainerDeletionError, match="ownership changed"):
                finalizer.run()
    # Then only read-only inspection occurs; stop and remove are unreachable.
    assert [call.args[0][1] for call in run.call_args_list] == ["inspect"]


def test_owned_image_creation_has_labels_without_runtime_auto_remove() -> None:
    # Given a retained V3 image policy with a same-process ownership capability.
    workflow = container_workflow()
    policy = resolve_v3_container_retention(
        workflow, ContainerRetention.RETAIN, "run-safe-17"
    )
    assert workflow.execution_backend is not None
    assert isinstance(policy.delete_authority, CurrentRunContainerDeleteAuthority)
    # When the real container adapter creates the framework-owned container.
    with patch("core.execution_backend.subprocess.run") as run:
        run.return_value = MagicMock(returncode=0, stdout="immutable-id\n", stderr="")
        backend = ContainerBackend._for_v3(
            workflow.execution_backend, policy.delete_authority
        )
        _ = backend._ensure_container()
    # Then immutable ownership labels are present and Docker --rm is absent.
    command = run.call_args.args[0]
    assert "--rm" not in command
    assert "seam.owner=run-safe-17" in command
    assert any(str(item).startswith("seam.owner-token=") for item in command)
