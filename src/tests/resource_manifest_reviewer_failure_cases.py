from __future__ import annotations

from pathlib import Path

import pytest

from core.resource_manifest import (
    EnvironmentProbe,
    EnvironmentProbeRequest,
    ProbedEnvironment,
    ResourceManifestContext,
    ResourceManifestError,
    ResourceManifestErrorKind,
    ResourceManifestStore,
    ResourceManifestUpdate,
)
from tests.resource_manifest_test_support import (
    initial_manifest,
    manifest_identity,
    manifest_store,
)


def _failed_probe(context: ResourceManifestContext) -> ProbedEnvironment:
    return context._capture_environment_probe(
        EnvironmentProbeRequest(
            probe_id="probe-failed",
            environment_id="env-failed",
            namespace="host",
            probe=EnvironmentProbe(status="error", error="python unavailable"),
        )
    )


def test_failed_observed_probe_requires_failed_receipt(tmp_path: Path) -> None:
    # Given a framework-observed failed probe record.
    store = manifest_store(tmp_path)
    failed = _failed_probe(store.context)

    # When persistence omits its failed receipt.
    with pytest.raises(ResourceManifestError) as refusal:
        _ = store.write(
            ResourceManifestUpdate(
                expected_revision=1,
                environments=(failed.environment,),
            )
        )

    # Then observed failures remain receipt-bound audit evidence.
    assert refusal.value.kind is ResourceManifestErrorKind.PROVENANCE_ESCALATION


def test_failed_observed_probe_with_receipt_persists(tmp_path: Path) -> None:
    # Given a failed probe and its matching ERROR receipt.
    store = manifest_store(tmp_path)
    failed = _failed_probe(store.context)

    # When both are appended in one revision.
    revised = store.write(
        ResourceManifestUpdate(
            expected_revision=1,
            environments=(failed.environment,),
            probe_receipts=(failed.receipt,),
        )
    )

    # Then the explicit error provenance remains readable.
    assert revised.environments == (failed.environment,)
    assert revised.probe_receipts[-1] == failed.receipt
    assert store.read().revision == 2


def test_initial_failed_observed_probe_with_receipt_persists(
    tmp_path: Path,
) -> None:
    # Given revision one includes a failed probe and its matching receipt.
    identity = manifest_identity()
    report_dir = tmp_path / "e2e-reports" / identity.run_id
    report_dir.mkdir(parents=True)
    context = ResourceManifestContext.bind(report_dir, identity)
    failed = _failed_probe(context)
    base = initial_manifest(tmp_path, context)
    manifest = base.model_copy(
        update={
            "environments": (failed.environment,),
            "probe_receipts": base.probe_receipts + (failed.receipt,),
        }
    )

    # When the initial manifest is persisted.
    store = ResourceManifestStore.create(context, manifest)

    # Then revision one retains the explicit failed observation.
    persisted = store.read()
    assert persisted.environments == (failed.environment,)
    assert persisted.probe_receipts[-1] == failed.receipt
