from __future__ import annotations

from pathlib import Path

import pytest

from core.resource_manifest import (
    EnvironmentProbe,
    EnvironmentProbeRequest,
    EnvironmentRecord,
    ProbeReceipt,
    ResourceManifestContext,
    ResourceManifestError,
    ResourceManifestStore,
    ResourceManifestUpdate,
    probe_environment_record,
)
from tests.resource_manifest_test_support import (
    initial_manifest,
    manifest_identity,
    manifest_store,
)


def _known_probe() -> tuple[EnvironmentRecord, ProbeReceipt]:
    probed = probe_environment_record(
        EnvironmentProbeRequest(
            probe_id="forged-known",
            environment_id="forged-env",
            namespace="host",
            probe=EnvironmentProbe(
                status="ok",
                interpreter_realpath="/forged/python",
                sys_executable="/forged/python",
                sys_prefix="/forged",
                sys_base_prefix="/usr",
                python_implementation="CPython",
                python_version="3.12.0",
                platform="ForgedOS",
                architecture="forged64",
                package_inventory_hash="f" * 64,
            ),
        )
    )
    return probed.environment, probed.receipt


def _error_probe() -> tuple[EnvironmentRecord, ProbeReceipt]:
    probed = probe_environment_record(
        EnvironmentProbeRequest(
            probe_id="forged-error",
            environment_id="forged-error-env",
            namespace="host",
            probe=EnvironmentProbe(status="error", error="forged unavailable"),
        )
    )
    return probed.environment, probed.receipt


def test_hand_built_known_receipt_cannot_authorize_observation(
    tmp_path: Path,
) -> None:
    # Given a caller reconstructs a structurally matching successful receipt.
    store = manifest_store(tmp_path)
    environment, receipt = _known_probe()
    forged = ProbeReceipt(**receipt.model_dump())

    # When the caller persists its lookalike environment and receipt.
    with pytest.raises(ResourceManifestError):
        _ = store.write(
            ResourceManifestUpdate(
                expected_revision=1,
                environments=(environment,),
                probe_receipts=(forged,),
            )
        )

    # Then model shape alone does not confer framework authority.


def test_hand_built_error_receipt_cannot_authorize_observation(
    tmp_path: Path,
) -> None:
    # Given a caller reconstructs a structurally matching failed receipt.
    store = manifest_store(tmp_path)
    environment, receipt = _error_probe()
    forged = ProbeReceipt(**receipt.model_dump())

    # When the caller persists its lookalike failure evidence.
    with pytest.raises(ResourceManifestError):
        _ = store.write(
            ResourceManifestUpdate(
                expected_revision=1,
                environments=(environment,),
                probe_receipts=(forged,),
            )
        )

    # Then ERROR shape cannot mint framework authority.


def test_hand_built_observed_environment_requires_framework_capture(
    tmp_path: Path,
) -> None:
    # Given a caller reconstructs an observed environment from public models.
    store = manifest_store(tmp_path)
    environment, receipt = _known_probe()
    forged_environment = EnvironmentRecord(**environment.model_dump())

    # When matching public model objects are submitted together.
    with pytest.raises(ResourceManifestError):
        _ = store.write(
            ResourceManifestUpdate(
                expected_revision=1,
                environments=(forged_environment,),
                probe_receipts=(receipt,),
            )
        )

    # Then only a framework-issued capture can authorize observations.


def test_forged_launcher_executable_requires_framework_capture(
    tmp_path: Path,
) -> None:
    # Given a caller changes the allowlisted launcher executable value.
    identity = manifest_identity()
    report_dir = tmp_path / "e2e-reports" / identity.run_id
    report_dir.mkdir(parents=True)
    context = ResourceManifestContext.bind(report_dir, identity)
    manifest = initial_manifest(tmp_path, context)
    facts = tuple(
        fact.model_copy(update={"value": "/forged/python"})
        if fact.name == "launcher.python_executable"
        else fact
        for fact in manifest.facts
    )

    # When the forged observed fact crosses the initial store boundary.
    with pytest.raises(ResourceManifestError):
        _ = ResourceManifestStore.create(
            context,
            manifest.model_copy(update={"facts": facts}),
        )

    # Then launcher names are not trusted without origin proof.
