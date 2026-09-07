from __future__ import annotations

from pathlib import Path
from weakref import ref

from core import (
    phase5_authority_registry,
    resource_retention_authority,
    run_manifest,
    run_manifest_access,
)
from core.phase5_attempt_authority import Phase5AuthorityError
from core.phase5_authority_registry import Phase5AuthorityRegistry
from core.resource_retention import (
    ContainerRetention,
    ContinuationContainerDeleteAuthority,
    V3ContainerRetentionPolicy,
)
from core.resource_retention_authority import DeleteAuthorityError
from core.run_manifest import RunManifest, RunManifestStore


def reclassify_manifest_reader(
    reader: RunManifestStore,
    updated: RunManifest,
) -> RunManifest:
    try:
        run_manifest_access.register_store(reader, True)
    except (AttributeError, run_manifest_access.RunManifestHandleError) as error:
        _ = error
    try:
        permission = run_manifest._ManifestPermission(reader, True)
        setattr(reader, "_permission", permission)
    except (AttributeError, run_manifest_access.RunManifestHandleError) as error:
        _ = error
        try:
            setattr(reader, "_permission", True)
        except (AttributeError, run_manifest_access.RunManifestHandleError) as error:
            _ = error
    return reader.write(updated)


def reclassify_retention_policy(
    policy: V3ContainerRetentionPolicy,
) -> V3ContainerRetentionPolicy:
    return policy._replace(effective=ContainerRetention.DELETE)


def reassign_retention_policy_owner(policy: V3ContainerRetentionPolicy) -> None:
    authority = policy.delete_authority
    if authority is None:
        return
    try:
        binding = resource_retention_authority._DELETE_AUTHORITY_BINDINGS[id(authority)]
        resource_retention_authority._DELETE_AUTHORITY_BINDINGS[id(authority)] = (
            binding._replace(policy=policy)
        )
    except DeleteAuthorityError as error:
        _ = error


def reassign_phase5_receipt_owner(
    receipt_path: Path,
    registry: Phase5AuthorityRegistry,
) -> None:
    try:
        phase5_authority_registry._RECEIPT_OWNERSHIP._issuers[
            str(receipt_path.resolve())
        ] = ref(registry)
    except (AttributeError, Phase5AuthorityError) as error:
        _ = error


def mutate_continuation_eligibility(
    policy: V3ContainerRetentionPolicy,
) -> None:
    authority = policy.delete_authority
    if not isinstance(authority, ContinuationContainerDeleteAuthority):
        raise DeleteAuthorityError("continuation deletion authority required")
    object.__setattr__(authority.eligibility, "ownership_token", "foreign-token")
