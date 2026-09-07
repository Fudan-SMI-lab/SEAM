from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

import pytest
from pydantic import ValidationError

from core.resource_manifest import (
    BackendFactRequest,
    EnvironmentProbe,
    EnvironmentProbeRequest,
    FactProvenance,
    OpenCodeFactRequest,
    ProbeReceipt,
    ProbedEnvironment,
    ProvenanceFact,
    ResourceManifestContext,
    ResourceManifestError,
    ResourceManifestErrorKind,
    ResourceManifestStore,
    ResourceManifestUpdate,
    build_backend_facts,
    build_initial_manifest,
    build_opencode_facts,
    capture_local_environment,
    probe_environment_record,
)
from tests.resource_manifest_test_support import (
    initial_manifest,
    manifest_identity,
    manifest_store,
)


class ProbeScenario(NamedTuple):
    environment_id: str
    namespace: str
    version: str
    probe_id: str


def _probe(scenario: ProbeScenario) -> ProbedEnvironment:
    return probe_environment_record(
        EnvironmentProbeRequest(
            probe_id=scenario.probe_id,
            environment_id=scenario.environment_id,
            namespace=scenario.namespace,
            probe=EnvironmentProbe(
                status="ok",
                interpreter_realpath="/workspace/.venv/bin/python",
                sys_executable="/workspace/.venv/bin/python",
                sys_prefix="/workspace/.venv",
                sys_base_prefix="/usr",
                python_implementation="CPython",
                python_version=scenario.version,
                platform="Linux",
                architecture="x86_64",
                package_inventory_hash="d" * 64,
            ),
        )
    )


def _captured_probe(
    context: ResourceManifestContext,
    scenario: ProbeScenario,
) -> ProbedEnvironment:
    normalized = _probe(scenario)
    return context._capture_environment_probe(
        EnvironmentProbeRequest(
            probe_id=normalized.receipt.probe_id,
            environment_id=normalized.environment.environment_id,
            namespace=normalized.receipt.namespace,
            probe=EnvironmentProbe(
                status="ok",
                interpreter_realpath=normalized.receipt.verified_facts[0].value,
                sys_executable=normalized.receipt.verified_facts[1].value,
                sys_prefix=normalized.receipt.verified_facts[2].value,
                sys_base_prefix=normalized.receipt.verified_facts[3].value,
                python_implementation=normalized.receipt.verified_facts[4].value,
                python_version=normalized.receipt.verified_facts[5].value,
                platform=normalized.receipt.verified_facts[6].value,
                architecture=normalized.receipt.verified_facts[7].value,
                package_inventory_hash=normalized.receipt.verified_facts[8].value,
            ),
        )
    )


def test_initial_observed_environment_requires_receipt(tmp_path: Path) -> None:
    # Given revision one contains a framework-observed environment.
    report_dir = tmp_path / "e2e-reports" / manifest_identity().run_id
    report_dir.mkdir(parents=True)
    context = ResourceManifestContext.bind(report_dir, manifest_identity())
    observed = capture_local_environment("initial-env")
    manifest = initial_manifest(tmp_path).model_copy(
        update={"environments": (observed.environment,)}
    )

    # When creation omits the successful receipt.
    with pytest.raises(ResourceManifestError) as refusal:
        _ = ResourceManifestStore.create(context, manifest)

    # Then unreceipted observations never become readable revision one.
    assert refusal.value.kind is ResourceManifestErrorKind.PROVENANCE_ESCALATION
    assert not (report_dir / "resource-manifest.v1.json").exists()


def test_container_backend_rejects_foreign_environment_namespace(
    tmp_path: Path,
) -> None:
    # Given revision one binds the run to container cid-a.
    identity = manifest_identity()
    report_dir = tmp_path / "e2e-reports" / identity.run_id
    report_dir.mkdir(parents=True)
    context = ResourceManifestContext.bind(report_dir, identity)
    launcher = context.capture_launcher()
    facts = (
        launcher.facts
        + build_backend_facts(
            BackendFactRequest(
                requested_workflow="wf.yaml",
                effective_workflow="wf.yaml",
                requested_backend="container",
                effective_backend="container",
                attachment_mode="image_created",
                original_owner_run_id=identity.run_id,
                lineage_root_run_id=identity.run_id,
                framework_ownership_token="token-a",
                framework_ownership_label="seam.owner=run-safe-16",
                container_runtime="docker",
                container_id="cid-a",
                image="cpu:test",
                probe_status="ok",
            )
        )
        + build_opencode_facts(
            OpenCodeFactRequest(
                endpoint="http://127.0.0.1:4096",
                owner_kind="framework",
            )
        )
    )
    store = ResourceManifestStore.create(
        context,
        build_initial_manifest(identity, facts, (launcher.receipt,)),
    )
    foreign = _captured_probe(
        context, ProbeScenario("project-venv", "container:cid-b", "3.10.12", "probe-b")
    )

    # When a receipted environment from cid-b is appended.
    with pytest.raises(ResourceManifestError) as refusal:
        _ = store.write(
            ResourceManifestUpdate(
                expected_revision=1,
                environments=(foreign.environment,),
                probe_receipts=(foreign.receipt,),
            )
        )

    # Then backend and environment resource identities cannot diverge.
    assert refusal.value.kind is ResourceManifestErrorKind.RUN_CONTEXT_MISMATCH


def test_initial_singleton_fact_rejects_conflicting_value(tmp_path: Path) -> None:
    # Given a second observed launcher version contradicts the captured version.
    manifest = initial_manifest(tmp_path)
    version = next(
        fact for fact in manifest.facts if fact.name == "launcher.python_version"
    )
    conflicting = version.model_copy(update={"value": "forged"})

    # When initial construction receives both authoritative values.
    with pytest.raises(ResourceManifestError) as refusal:
        _ = build_initial_manifest(manifest_identity(), manifest.facts + (conflicting,))

    # Then semantic duplicates are rejected even when values differ.
    assert refusal.value.kind is ResourceManifestErrorKind.DUPLICATE_FACT


def test_environment_singleton_rejects_conflicting_observation(
    tmp_path: Path,
) -> None:
    # Given one receipted environment observation is persisted.
    store = manifest_store(tmp_path)
    first = _captured_probe(
        store.context, ProbeScenario("env-a", "host", "3.10.12", "probe-a1")
    )
    _ = store.write(
        ResourceManifestUpdate(
            expected_revision=1,
            environments=(first.environment,),
            probe_receipts=(first.receipt,),
        )
    )
    conflicting = _captured_probe(
        store.context, ProbeScenario("env-a", "host", "3.11.0", "probe-a2")
    )

    # When the same observed singleton receives another value.
    with pytest.raises(ResourceManifestError) as refusal:
        _ = store.write(
            ResourceManifestUpdate(
                expected_revision=2,
                environments=(conflicting.environment,),
                probe_receipts=(conflicting.receipt,),
            )
        )

    # Then the manifest cannot retain contradictory authoritative values.
    assert refusal.value.kind is ResourceManifestErrorKind.DUPLICATE_FACT


def test_receipt_verified_facts_are_unique() -> None:
    # Given a successful probe fact appears twice in one receipt.
    probed = _probe(ProbeScenario("env-a", "container:cid-a", "3.10.12", "probe-a"))
    fact = probed.receipt.verified_facts[0]

    # When the duplicate receipt crosses its typed boundary.
    with pytest.raises(ValidationError):
        _ = ProbeReceipt(
            probe_id="duplicate-receipt",
            environment_id="env-a",
            namespace="container:cid-a",
            status=probed.receipt.status,
            verified_facts=(fact, fact),
        )

    # Then receipt evidence remains a set of unique observations.


def test_receipt_cannot_verify_fact_absent_from_environment(tmp_path: Path) -> None:
    # Given a receipt claims one extra observed fact not stored in its environment.
    store = manifest_store(tmp_path)
    probed = _probe(ProbeScenario("env-a", "container:cid-a", "3.10.12", "probe-a"))
    detached = ProvenanceFact(
        name="runtime.detached",
        value="forged",
        provenance=FactProvenance.FRAMEWORK_OBSERVED,
        namespace="container:cid-a",
    )
    receipt = probed.receipt.model_copy(
        update={"verified_facts": probed.receipt.verified_facts + (detached,)}
    )

    # When the environment and detached receipt are persisted together.
    with pytest.raises(ResourceManifestError) as refusal:
        _ = store.write(
            ResourceManifestUpdate(
                expected_revision=1,
                environments=(probed.environment,),
                probe_receipts=(receipt,),
            )
        )

    # Then every verified fact must belong to the target environment.
    assert refusal.value.kind is ResourceManifestErrorKind.PROVENANCE_ESCALATION
