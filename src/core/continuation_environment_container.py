from __future__ import annotations

from pathlib import Path

from .continuation_environment_manifest import error, known_fact, required_fact
from .continuation_lock import (
    ActiveProjectOwnerLock,
    project_owner_lock_is_active,
)
from .continuation_environment_models import (
    BindMount,
    ContainerDeleteForbidden,
    ContinuationEnvironmentErrorKind,
    ContinuationEnvironmentRequest,
    ExistingContainerAttachment,
    FrameworkContainerDeleteEligible,
)


def _mount_matches(
    mounts: tuple[BindMount, ...],
    project: Path,
    destination: str,
) -> bool:
    expected_source = project.resolve()
    expected_destination = destination.rstrip("/")
    return any(
        mount.source.resolve() == expected_source
        and mount.destination.rstrip("/") == expected_destination
        for mount in mounts
    )


def _lock_matches(
    lock: ActiveProjectOwnerLock | None,
    request: ContinuationEnvironmentRequest,
    lineage_root: str,
) -> bool:
    return (
        lock is not None
        and project_owner_lock_is_active(lock)
        and (
            lock.parent_run_id == request.resource_manifest.run_id
            and lock.child_run_id == request.child_run_id
            and lock.lineage_root_run_id == lineage_root
            and lock.output_project.resolve() == request.output_project.resolve()
        )
    )


def container_eligibility(
    request: ContinuationEnvironmentRequest,
) -> tuple[
    ExistingContainerAttachment,
    FrameworkContainerDeleteEligible | ContainerDeleteForbidden,
]:
    requirements = request.container_requirements
    observed = request.observed_container
    if requirements is None or observed is None:
        raise error(
            ContinuationEnvironmentErrorKind.CONTAINER_MISSING,
            "container",
            "retained container requirements or observation is unavailable",
        )
    facts = request.resource_manifest.facts
    runtime = required_fact(facts, "container.runtime")
    container_id = required_fact(facts, "container.id")
    image = required_fact(facts, "container.image")
    owner = required_fact(facts, "container.owner_kind")
    context_matches = (
        observed.runtime == runtime
        and observed.container_id == container_id
        and observed.name == requirements.name
        and observed.running
        and image in (observed.image_identity, observed.image_reference)
        and observed.workdir.rstrip("/") == requirements.workdir.rstrip("/")
        and frozenset(observed.devices) == frozenset(requirements.devices)
        and _mount_matches(
            observed.bind_mounts,
            request.output_project,
            requirements.project_mount_destination,
        )
    )
    if not context_matches:
        raise error(
            ContinuationEnvironmentErrorKind.CONTAINER_MISMATCH,
            "container",
            "live container identity or runtime context differs from the record",
        )
    if owner not in ("framework", "user", "external"):
        raise error(
            ContinuationEnvironmentErrorKind.OWNERSHIP_AMBIGUOUS,
            "container.owner_kind",
            "container owner is not unambiguous",
        )
    original_owner = known_fact(
        facts, "container.original_owner_run_id", required=False
    )
    lineage_root = known_fact(facts, "container.lineage_root_run_id", required=False)
    token = known_fact(facts, "container.framework_ownership_token", required=False)
    label = known_fact(facts, "container.framework_ownership_label", required=False)
    attachment = ExistingContainerAttachment(
        mode="existing_container",
        runtime=runtime,
        container_id=container_id,
        container_name=observed.name,
        owner_kind=owner,
        original_owner_run_id=original_owner,
        lineage_root_run_id=lineage_root,
        ownership_token=token,
        ownership_label=label,
    )
    if owner != "framework":
        return attachment, ContainerDeleteForbidden("container is user or external")
    ownership_complete = all(
        value is not None for value in (original_owner, lineage_root, token, label)
    )
    ownership_matches = (
        ownership_complete
        and observed.ownership_token == token
        and observed.ownership_label == label
        and lineage_root == request.child_lineage_root_run_id
    )
    if not ownership_matches:
        raise error(
            ContinuationEnvironmentErrorKind.OWNERSHIP_AMBIGUOUS,
            "container.framework_ownership",
            "framework ownership token, label, or lineage does not match",
        )
    if not _lock_matches(request.owner_lock, request, lineage_root or ""):
        return attachment, ContainerDeleteForbidden(
            "active project owner lock required"
        )
    if original_owner is None or lineage_root is None or token is None or label is None:
        raise AssertionError("complete framework ownership unexpectedly absent")
    return attachment, FrameworkContainerDeleteEligible(
        original_owner_run_id=original_owner,
        lineage_root_run_id=lineage_root,
        ownership_token=token,
        ownership_label=label,
    )
