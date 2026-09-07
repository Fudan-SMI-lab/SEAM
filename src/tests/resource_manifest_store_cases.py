from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.resource_manifest import (
    EnvironmentProbe,
    EnvironmentProbeRequest,
    FactProvenance,
    Phase2EnvironmentReport,
    Phase2EnvironmentRequest,
    ProvenanceFact,
    ResourceManifestContext,
    ResourceManifestError,
    ResourceManifestErrorKind,
    ResourceManifestStore,
    ResourceManifestUpdate,
    build_phase2_environment,
)
from tests.resource_manifest_test_support import (
    container_manifest_store,
    initial_manifest,
    manifest_identity,
    manifest_store,
)


def test_store_incrementally_writes_and_terminally_seals(tmp_path: Path) -> None:
    # Given a manifest in an exclusive Task 2/5 report directory.
    store = manifest_store(tmp_path)
    marker = ProvenanceFact(
        name="lifecycle.phase",
        value="phase_2",
        provenance=FactProvenance.DERIVED,
        namespace="host",
    )

    # When one revision is appended and then terminally sealed.
    revised = store.write(ResourceManifestUpdate(expected_revision=1, facts=(marker,)))
    sealed = store.seal(expected_revision=2, terminal_status="passed")

    # Then revisions are atomic, bounded, and immutable after the terminal seal.
    assert revised.revision == 2
    assert sealed.revision == 3
    assert sealed.sealed is True
    assert store.path.name == "resource-manifest.v1.json"
    assert json.loads(store.path.read_text(encoding="utf-8"))["sealed"] is True
    with pytest.raises(ResourceManifestError) as duplicate:
        _ = store.seal(expected_revision=3, terminal_status="passed")
    assert duplicate.value.kind is ResourceManifestErrorKind.SEALED
    with pytest.raises(ResourceManifestError) as mutation:
        _ = store.write(ResourceManifestUpdate(expected_revision=3))
    assert mutation.value.kind is ResourceManifestErrorKind.SEALED


def test_store_rejects_stale_duplicate_and_context_mismatch(tmp_path: Path) -> None:
    # Given one allocated, readable resource manifest.
    store = manifest_store(tmp_path)

    # When stale, duplicate, and mismatched handles are attempted.
    with pytest.raises(ResourceManifestError) as stale:
        _ = store.write(ResourceManifestUpdate(expected_revision=0))
    with pytest.raises(ResourceManifestError) as duplicate:
        _ = ResourceManifestStore.create(
            store.context, initial_manifest(tmp_path, store.context)
        )
    wrong_identity = manifest_identity().model_copy(
        update={"workflow_digest": "c" * 64}
    )
    wrong_context = ResourceManifestContext.bind(store.path.parent, wrong_identity)
    with pytest.raises(ResourceManifestError) as mismatch:
        _ = ResourceManifestStore.open(wrong_context)

    # Then each refusal has a stable machine-readable reason.
    assert stale.value.kind is ResourceManifestErrorKind.STALE_WRITE
    assert duplicate.value.kind is ResourceManifestErrorKind.DUPLICATE_MANIFEST
    assert mismatch.value.kind is ResourceManifestErrorKind.DIGEST_MISMATCH


def test_probe_promotion_requires_receipt_and_retains_report(tmp_path: Path) -> None:
    # Given an Agent-reported project venv persisted in revision two.
    store = container_manifest_store(tmp_path, "cid-1")
    reported = build_phase2_environment(
        Phase2EnvironmentRequest(
            environment_id="project-venv",
            namespace="container:cid-1",
            container_id="cid-1",
            report=Phase2EnvironmentReport(
                venv_path="/workspace/.venv",
                python_path="/workspace/.venv/bin/python",
                installed_packages=("torch==2.1.0",),
            ),
        )
    )
    _ = store.write(
        ResourceManifestUpdate(expected_revision=1, environments=(reported,))
    )
    promoted = store.context._capture_environment_probe(
        EnvironmentProbeRequest(
            probe_id="probe-1",
            environment_id="project-venv",
            namespace="container:cid-1",
            probe=EnvironmentProbe(
                status="ok",
                interpreter_realpath="/workspace/.venv/bin/python3.10",
                sys_executable="/workspace/.venv/bin/python",
                sys_prefix="/workspace/.venv",
                sys_base_prefix="/usr",
                python_implementation="CPython",
                python_version="3.10.12",
                platform="Linux",
                architecture="x86_64",
                package_inventory_hash="d" * 64,
            ),
        )
    )

    # When observed facts are appended without and then with their probe receipt.
    with pytest.raises(ResourceManifestError) as escalation:
        _ = store.write(
            ResourceManifestUpdate(
                expected_revision=2,
                environments=(promoted.environment,),
            )
        )
    revised = store.write(
        ResourceManifestUpdate(
            expected_revision=2,
            environments=(promoted.environment,),
            probe_receipts=(promoted.receipt,),
        )
    )

    # Then unauthorized trust escalation is refused and both layers remain.
    assert escalation.value.kind is ResourceManifestErrorKind.PROVENANCE_ESCALATION
    facts = revised.environments[0].facts
    executable_sources = {
        fact.provenance for fact in facts if fact.name == "interpreter.sys_executable"
    }
    assert executable_sources == {
        FactProvenance.AGENT_REPORTED,
        FactProvenance.FRAMEWORK_OBSERVED,
    }


def test_interrupted_replace_preserves_prior_readable_revision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a readable initial manifest and a replacement failure.
    store = manifest_store(tmp_path)
    baseline = store.path.read_bytes()

    def interrupt(_source: Path, _destination: Path) -> None:
        raise OSError("replace interrupted")

    monkeypatch.setattr("core.resource_manifest_io.atomic_replace", interrupt)

    # When a revision replacement is interrupted.
    with pytest.raises(ResourceManifestError) as failure:
        _ = store.write(ResourceManifestUpdate(expected_revision=1))

    # Then the prior revision remains byte-for-byte readable without temp residue.
    assert failure.value.kind is ResourceManifestErrorKind.WRITE_INTERRUPTED
    assert store.path.read_bytes() == baseline
    assert store.read().revision == 1
    assert tuple(store.path.parent.glob(".*.tmp")) == ()


@pytest.mark.parametrize("field", ["schema", "schema_version", "run_id"])
def test_open_rejects_schema_version_and_run_context_tampering(
    tmp_path: Path,
    field: str,
) -> None:
    # Given a persisted manifest with one authoritative field changed externally.
    store = manifest_store(tmp_path)
    payload = json.loads(store.path.read_text(encoding="utf-8"))
    payload[field] = "wrong" if field != "schema_version" else 99
    _ = store.path.write_text(json.dumps(payload), encoding="utf-8")

    # When the bound store reopens the file.
    with pytest.raises(ResourceManifestError) as refusal:
        _ = store.read()

    # Then schema/version/context drift is never accepted as the current revision.
    expected = {
        "schema": ResourceManifestErrorKind.SCHEMA_MISMATCH,
        "schema_version": ResourceManifestErrorKind.VERSION_MISMATCH,
        "run_id": ResourceManifestErrorKind.RUN_CONTEXT_MISMATCH,
    }
    assert refusal.value.kind is expected[field]
