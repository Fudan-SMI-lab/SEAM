from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest

from core.continuation import (
    ContinuationRequest,
    claim_terminal_parent,
    current_project_owner_lock,
)
from core.continuation_environment import (
    BindMount,
    ContainerDeleteForbidden,
    ContinuationEnvironmentError,
    ContinuationEnvironmentErrorKind,
    ExistingContainerAttachment,
    FrameworkContainerDeleteEligible,
    RetainedEnvironmentEligible,
    verify_continuation_environment,
)
from core.run_manifest import RunId
from tests.continuation_environment_test_support import (
    container_observation,
    environment_record,
    fingerprint,
    manifest,
    request,
)
from tests.terminal_run_continuation_test_support import create_parent_run


def test_framework_container_attachment_preserves_owner_and_delete_authority(
    tmp_path: Path,
) -> None:
    parent = create_parent_run(tmp_path)
    project = parent.project_dir
    live_environment = fingerprint(
        namespace="container:immutable-id",
        container_id="immutable-id",
        interpreter="/usr/bin/python3",
    )
    with claim_terminal_parent(
        ContinuationRequest(
            summary_path=parent.summary_path,
            child_run_id=RunId("child-run"),
        )
    ):
        owner_lock = current_project_owner_lock()
        assert owner_lock is not None
        result = verify_continuation_environment(
            request(
                project,
                resource_manifest=manifest(
                    backend="container",
                    environment=environment_record(live_environment),
                ),
                observed_container=container_observation(project),
                observed_environment=live_environment,
                owner_lock=owner_lock,
            )
        )

    assert isinstance(result, RetainedEnvironmentEligible)
    assert isinstance(result.attachment, ExistingContainerAttachment)
    assert result.attachment.mode == "existing_container"
    assert result.attachment.owner_kind == "framework"
    assert result.attachment.original_owner_run_id == "parent-run-001"
    assert result.attachment.lineage_root_run_id == "parent-run-001"
    assert result.attachment.ownership_token == "owner-token"
    assert result.attachment.ownership_label == "seam.owner=parent-run-001"
    assert isinstance(result.deletion, FrameworkContainerDeleteEligible)


@pytest.mark.parametrize("owner_kind", ["user", "external"])
def test_user_and_external_containers_remain_undeletable(
    tmp_path: Path,
    owner_kind: Literal["user", "external"],
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    live_environment = fingerprint(
        namespace="container:immutable-id", container_id="immutable-id"
    )
    observed = replace(
        container_observation(project), ownership_token=None, ownership_label=None
    )

    result = verify_continuation_environment(
        request(
            project,
            resource_manifest=manifest(
                backend="container",
                environment=environment_record(live_environment),
                owner_kind=owner_kind,
            ),
            observed_container=observed,
            observed_environment=live_environment,
        )
    )

    assert isinstance(result, RetainedEnvironmentEligible)
    assert isinstance(result.deletion, ContainerDeleteForbidden)


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("runtime", "podman"),
        ("container_id", "other-id"),
        ("name", "other-name"),
        ("running", False),
        ("image_identity", "sha256:other"),
        ("workdir", "/other"),
        ("devices", ("/dev/other",)),
        ("bind_mounts", ()),
    ],
)
def test_container_identity_or_runtime_context_mismatch_is_rejected(
    tmp_path: Path,
    field: str,
    replacement: str | bool | tuple[str, ...] | tuple[BindMount, ...],
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    live_environment = fingerprint(
        namespace="container:immutable-id", container_id="immutable-id"
    )
    observed = replace(container_observation(project), **{field: replacement})

    with pytest.raises(ContinuationEnvironmentError) as raised:
        _ = verify_continuation_environment(
            request(
                project,
                resource_manifest=manifest(
                    backend="container",
                    environment=environment_record(live_environment),
                ),
                observed_container=observed,
                observed_environment=live_environment,
            )
        )

    assert raised.value.kind is ContinuationEnvironmentErrorKind.CONTAINER_MISMATCH


def test_framework_delete_authority_requires_matching_active_owner_lock(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    live_environment = fingerprint(
        namespace="container:immutable-id", container_id="immutable-id"
    )
    result = verify_continuation_environment(
        request(
            project,
            resource_manifest=manifest(
                backend="container",
                environment=environment_record(live_environment),
            ),
            observed_container=container_observation(project),
            observed_environment=live_environment,
            owner_lock=None,
        )
    )

    assert isinstance(result, RetainedEnvironmentEligible)
    assert isinstance(result.deletion, ContainerDeleteForbidden)


def test_released_project_owner_lock_cannot_authorize_deletion(tmp_path: Path) -> None:
    parent = create_parent_run(tmp_path)
    project = parent.project_dir
    live_environment = fingerprint(
        namespace="container:immutable-id", container_id="immutable-id"
    )
    with claim_terminal_parent(
        ContinuationRequest(
            summary_path=parent.summary_path,
            child_run_id=RunId("child-run"),
        )
    ):
        released_lock = current_project_owner_lock()
        assert released_lock is not None

    result = verify_continuation_environment(
        request(
            project,
            resource_manifest=manifest(
                backend="container",
                environment=environment_record(live_environment),
            ),
            observed_container=container_observation(project),
            observed_environment=live_environment,
            owner_lock=released_lock,
        )
    )

    assert isinstance(result, RetainedEnvironmentEligible)
    assert isinstance(result.deletion, ContainerDeleteForbidden)


def test_ambiguous_framework_ownership_is_rejected(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    live_environment = fingerprint(
        namespace="container:immutable-id", container_id="immutable-id"
    )
    recorded = manifest(
        backend="container", environment=environment_record(live_environment)
    )
    duplicate = next(
        fact for fact in recorded.facts if fact.name == "container.owner_kind"
    ).model_copy(update={"value": "user"})
    recorded = recorded.model_copy(update={"facts": recorded.facts + (duplicate,)})

    with pytest.raises(ContinuationEnvironmentError) as raised:
        _ = verify_continuation_environment(
            request(
                project,
                resource_manifest=recorded,
                observed_container=container_observation(project),
                observed_environment=live_environment,
            )
        )

    assert raised.value.kind is ContinuationEnvironmentErrorKind.OWNERSHIP_AMBIGUOUS
