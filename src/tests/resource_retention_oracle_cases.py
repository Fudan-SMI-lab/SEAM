from __future__ import annotations

import copy
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.resource_manifest import ResourceManifestError
from core.resource_retention import (
    ContainerCleanupStatus,
    ContainerDeletionError,
    ContainerRetention,
)
from core.resource_retention_finalizer import (
    RetentionLifecycleRecorder,
    _authorized_retention_finalization,
)
from core.resource_retention_lifecycle import (
    RetentionLifecycleRecord,
    write_measured_retention_lifecycle,
)
from core.resource_retention_manifest import RetentionManifestFinalizer
from tests.resource_retention_manifest_cases import _retained_store


def test_recorder_subclass_cannot_issue_forged_measurement(tmp_path: Path) -> None:
    # Given a runtime subclass that overrides record verification.
    store, backend, _finalizer, _recorder = _retained_store(tmp_path)

    def forged_record(_self, _backend):
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

    forged_type = type(
        "ForgedRecorder",
        (RetentionLifecycleRecorder,),
        {"require_record": forged_record},
    )
    forged = forged_type()

    # When inherited issuance is asked to mint a sealed measurement.
    with pytest.raises(ContainerDeletionError, match="measurement capability"):
        measurement = forged.issue_measurement(backend)
        _ = write_measured_retention_lifecycle(store, measurement, backend)

    # Then the manifest remains unsealed and without forged lifecycle authority.
    assert store.read().sealed is False


def test_measured_recorder_cannot_rebind_to_different_manifest(tmp_path: Path) -> None:
    # Given a legitimate measurement for one authenticated container manifest.
    store_a, backend_a, finalizer_a, recorder_a = _retained_store(
        tmp_path / "source",
        container_id="source-id",
    )
    manifest_a = RetentionManifestFinalizer(store_a, recorder_a, backend_a)
    with patch("core.execution_backend.subprocess.run") as run:
        run.return_value = MagicMock(
            returncode=0,
            stdout="running|source-id\n",
            stderr="",
        )
        with _authorized_retention_finalization(finalizer_a):
            finalizer_a.run()
    del manifest_a
    store_b, _backend_b, _finalizer_b, _recorder_b = _retained_store(
        tmp_path / "target",
        container_id="target-id",
    )

    # When the source recorder/backend is attached to the target manifest.
    with pytest.raises(ResourceManifestError, match="manifest.*container identity"):
        target = RetentionManifestFinalizer(store_b, recorder_a, backend_a)
        _ = target.persist_and_seal("passed")

    # Then target lifecycle authority and access can never derive from source measurement.
    assert store_b.read().sealed is False


def test_measurement_cannot_transplant_to_equal_identity_context(
    tmp_path: Path,
) -> None:
    # Given two stores with value-equal identities and the same container ID.
    shared_workspace = tmp_path / "shared" / "output-project"
    shared_workflow = tmp_path / "shared" / "workflow.yaml"
    store_a, backend_a, finalizer_a, recorder_a = _retained_store(
        tmp_path / "source-equal",
        workspace=shared_workspace,
        workflow_path=shared_workflow,
    )
    _ = RetentionManifestFinalizer(store_a, recorder_a, backend_a)
    with patch("core.execution_backend.subprocess.run") as run:
        run.return_value = MagicMock(
            returncode=0,
            stdout="running|immutable-id\n",
            stderr="",
        )
        with _authorized_retention_finalization(finalizer_a):
            finalizer_a.run()
    store_b, _backend_b, _finalizer_b, _recorder_b = _retained_store(
        tmp_path / "target-equal",
        workspace=shared_workspace,
        workflow_path=shared_workflow,
    )
    assert store_a.context.identity == store_b.context.identity
    measurement = recorder_a.issue_measurement(backend_a)

    # When the legitimate measurement is offered to another equal-value context.
    with pytest.raises(ContainerDeletionError, match="measurement capability"):
        _ = write_measured_retention_lifecycle(store_b, measurement, backend_a)

    # Then the target context remains unsigned and unsealed.
    assert store_b.read().sealed is False


def test_measurement_capability_cannot_be_copied(tmp_path: Path) -> None:
    # Given an authorized, unconsumed one-shot lifecycle measurement.
    store, backend, finalizer, recorder = _retained_store(tmp_path)
    _ = RetentionManifestFinalizer(store, recorder, backend)
    with patch("core.execution_backend.subprocess.run") as run:
        run.return_value = MagicMock(
            returncode=0,
            stdout="running|immutable-id\n",
            stderr="",
        )
        with _authorized_retention_finalization(finalizer):
            finalizer.run()
    measurement = recorder.issue_measurement(backend)

    # When ordinary copy machinery attempts to duplicate the capability.
    with pytest.raises(ContainerDeletionError, match="cannot be copied"):
        _ = copy.copy(measurement)

    # Then the original remains the sole consumable capability.
    assert store.read().sealed is False
