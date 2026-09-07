from __future__ import annotations

from pathlib import Path

import pytest

from core.continuation import ContinuationRequest, claim_terminal_parent
from core.continuation_environment import (
    ExistingContainerAttachment,
    Phase2EstablishmentEligible,
)
from core.continuation_environment_models import (
    _verified_framework_container_delete_eligibility,
)
from core.resource_retention import (
    ContainerDeletionError,
    ContainerRetention,
    resolve_v3_container_retention,
)
from core.resource_retention_finalizer import (
    ContainerRetentionFinalizer,
    RetentionLifecycleRecorder,
    _authorized_retention_finalization,
)
from core.run_manifest import RunId
from tests.authority_boundary_attack_support import (
    mutate_continuation_eligibility,
    reassign_retention_policy_owner,
    reclassify_retention_policy,
)
from tests.resource_retention_test_support import RecordingBackend, container_workflow
from tests.terminal_run_continuation_test_support import create_parent_run


def test_mutated_continuation_eligibility_cannot_cross_delete_boundary(
    tmp_path: Path,
) -> None:
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
    backend = RecordingBackend()

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
        mutate_continuation_eligibility(policy)
        finalizer = ContainerRetentionFinalizer(
            policy,
            backend,
            parent.project_dir,
            RetentionLifecycleRecorder(),
        )
        with _authorized_retention_finalization(finalizer):
            with pytest.raises(ContainerDeletionError, match="not registered"):
                finalizer.run()

    assert backend.delete_calls == []


def test_imported_retention_mapping_cannot_reassign_policy_owner(
    tmp_path: Path,
) -> None:
    # Given a retain policy reclassified as delete through imported owner state.
    policy = resolve_v3_container_retention(
        container_workflow(), ContainerRetention.RETAIN, "run-owner"
    )
    foreign_policy = reclassify_retention_policy(policy)
    reassign_retention_policy_owner(foreign_policy)
    backend = RecordingBackend()
    finalizer = ContainerRetentionFinalizer(
        foreign_policy,
        backend,
        tmp_path,
        RetentionLifecycleRecorder(),
    )

    # When the forged policy reaches the real finalization boundary.
    with _authorized_retention_finalization(finalizer):
        with pytest.raises(ContainerDeletionError, match="policy"):
            finalizer.run()

    # Then imported state cannot grant destructive ownership.
    assert backend.delete_calls == []
