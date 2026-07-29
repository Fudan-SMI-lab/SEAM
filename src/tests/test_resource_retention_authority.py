from __future__ import annotations

import copy
import inspect
import pickle

import pytest

import core.resource_retention as resource_retention
from core.resource_retention import (
    ContainerDeletionError,
    ContainerRetention,
    CurrentRunContainerDeleteAuthority,
    _authorized_container_cleanup,
    resolve_v3_container_retention,
)
from tests.resource_retention_test_support import container_workflow


def _delete_authority() -> CurrentRunContainerDeleteAuthority:
    policy = resolve_v3_container_retention(
        container_workflow(), ContainerRetention.DELETE, "run-opacity"
    )
    authority = policy.delete_authority
    assert isinstance(authority, CurrentRunContainerDeleteAuthority)
    return authority


def test_delete_authority_cannot_be_manually_reconstructed() -> None:
    capability = _delete_authority()
    constructor_signature = inspect.signature(CurrentRunContainerDeleteAuthority)

    with pytest.raises(TypeError):
        _ = constructor_signature.bind(
            original_owner_run_id=capability.original_owner_run_id,
            lineage_root_run_id=capability.lineage_root_run_id,
            ownership_token=capability.ownership_token,
            ownership_label=capability.ownership_label,
        )


def test_delete_authority_cannot_be_copied() -> None:
    capability = _delete_authority()

    with pytest.raises(TypeError):
        _ = copy.copy(capability)


def test_delete_authority_cannot_be_deep_copied() -> None:
    capability = _delete_authority()

    with pytest.raises(TypeError):
        _ = copy.deepcopy(capability)


def test_delete_authority_cannot_be_pickled() -> None:
    capability = _delete_authority()

    with pytest.raises(TypeError):
        _ = pickle.dumps(capability)


@pytest.mark.parametrize(
    "name",
    [
        "_DeleteAuthorityMint",
        "_DELETE_AUTHORITY_MINT",
        "_issue_current_run_delete_authority",
    ],
)
def test_retention_module_exposes_no_delete_mint_primitive(name: str) -> None:
    assert not hasattr(resource_retention, name)


def test_reconstructed_delete_authority_cannot_enter_cleanup_context() -> None:
    original = _delete_authority()
    reconstructed = object.__new__(CurrentRunContainerDeleteAuthority)
    for slot in CurrentRunContainerDeleteAuthority.__slots__:
        object.__setattr__(reconstructed, slot, getattr(original, slot))

    with pytest.raises(ContainerDeletionError, match="not registered"):
        with _authorized_container_cleanup(reconstructed):
            pytest.fail("reconstructed authority entered destructive context")
