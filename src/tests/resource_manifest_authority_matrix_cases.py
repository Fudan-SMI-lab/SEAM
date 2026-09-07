from __future__ import annotations

from pathlib import Path

import pytest
import core.resource_manifest as resource_manifest_api

from core.resource_manifest import (
    BackendFactRequest,
    EnvironmentProbe,
    EnvironmentProbeRequest,
    OpenCodeFactRequest,
    ResourceManifestContext,
    ResourceManifestError,
    ResourceManifestErrorKind,
    ResourceManifestIdentity,
    ResourceManifestStore,
    ResourceManifestUpdate,
    build_backend_facts,
    build_initial_manifest,
    build_opencode_facts,
)
from tests.resource_manifest_authority_cases import _error_probe, _known_probe
from tests.resource_manifest_test_support import manifest_store


def _known_request() -> EnvironmentProbeRequest:
    return EnvironmentProbeRequest(
        probe_id="trusted-known",
        environment_id="trusted-env",
        namespace="host",
        probe=EnvironmentProbe(
            status="ok",
            interpreter_realpath="/trusted/python",
            sys_executable="/trusted/python",
            sys_prefix="/trusted",
            sys_base_prefix="/usr",
            python_implementation="CPython",
            python_version="3.12.0",
            platform="TrustedOS",
            architecture="trusted64",
            package_inventory_hash="a" * 64,
        ),
    )


def _error_request() -> EnvironmentProbeRequest:
    return EnvironmentProbeRequest(
        probe_id="trusted-error",
        environment_id="trusted-error-env",
        namespace="host",
        probe=EnvironmentProbe(status="error", error="trusted unavailable"),
    )


@pytest.mark.parametrize("probe_request", [_known_request(), _error_request()])
def test_framework_capture_persists_authenticated_probe(
    tmp_path: Path,
    probe_request: EnvironmentProbeRequest,
) -> None:
    # Given the run context performs the official probe capture.
    store = manifest_store(tmp_path)
    captured = store.context._capture_environment_probe(probe_request)

    # When its authenticated environment and receipt are persisted.
    revised = store.write(
        ResourceManifestUpdate(
            expected_revision=1,
            environments=(captured.environment,),
            probe_receipts=(captured.receipt,),
        )
    )

    # Then both successful values and typed failures retain framework origin.
    assert revised.environments == (captured.environment,)
    assert revised.probe_receipts[-1] == captured.receipt


def test_capture_replay_across_run_and_workspace_is_rejected(tmp_path: Path) -> None:
    # Given one run issues an authenticated environment capture.
    source = manifest_store(tmp_path / "source")
    captured = source.context._capture_environment_probe(_known_request())
    identity = ResourceManifestIdentity(
        run_id="other-run",
        workflow_digest="c" * 64,
        workspace_digest="d" * 64,
    )
    report_dir = tmp_path / "target" / "reports" / identity.run_id
    report_dir.mkdir(parents=True)
    context = ResourceManifestContext.bind(report_dir, identity)
    launcher = context.capture_launcher()
    backend = build_backend_facts(
        BackendFactRequest(
            requested_workflow="wf.yaml",
            effective_workflow="wf.yaml",
            requested_backend="local",
            effective_backend="local",
        )
    )
    opencode = build_opencode_facts(
        OpenCodeFactRequest(
            endpoint="http://127.0.0.1:4096",
            version="1.18.5",
            owner_kind="framework",
            process_id="1234",
        )
    )
    target = ResourceManifestStore.create(
        context,
        build_initial_manifest(
            identity,
            launcher.facts + backend + opencode,
            (launcher.receipt,),
        ),
    )

    # When run A evidence is replayed into run B.
    with pytest.raises(ResourceManifestError) as refusal:
        _ = target.write(
            ResourceManifestUpdate(
                expected_revision=1,
                environments=(captured.environment,),
                probe_receipts=(captured.receipt,),
            )
        )

    # Then public identity fields cannot transfer the secret authority.
    assert refusal.value.kind is ResourceManifestErrorKind.AUTHORITY_MISMATCH


def test_fresh_matching_context_reopens_persisted_authority(tmp_path: Path) -> None:
    # Given a manifest created with one in-memory run context.
    store = manifest_store(tmp_path)
    original = store.read()

    # When the same durable run identity is reconstructed from its report path.
    reopened_context = ResourceManifestContext.bind(
        store.context.report_dir,
        store.context.identity,
    )
    reopened = ResourceManifestStore.open(reopened_context)

    # Then external authority survives context reconstruction without entering JSON.
    assert reopened.read() == original
    content = reopened.path.read_text(encoding="utf-8")
    assert '"secret"' not in content
    assert '"_secret"' not in content


def test_modified_external_capability_fails_closed_on_reopen(tmp_path: Path) -> None:
    # Given a valid persisted manifest whose external capability is replaced.
    store = manifest_store(tmp_path)
    (capability,) = tmp_path.rglob("*.key")
    assert capability.parent != store.context.report_dir
    _ = capability.write_bytes(b"x" * 32)

    # When a fresh matching context attempts to verify the persisted bytes.
    reopened_context = ResourceManifestContext.bind(
        store.context.report_dir,
        store.context.identity,
    )
    with pytest.raises(ResourceManifestError) as refusal:
        _ = ResourceManifestStore.open(reopened_context)

    # Then replacement cannot authorize the previously captured observations.
    assert refusal.value.kind is ResourceManifestErrorKind.AUTHORITY_MISMATCH


def test_public_context_has_no_caller_data_signing_oracle(tmp_path: Path) -> None:
    # Given a caller binds the public manifest context for a valid run.
    store = manifest_store(tmp_path)
    context = store.context

    # When its public capture surface and report files are inspected.
    signing_methods = (
        "capture_backend",
        "capture_opencode",
        "capture_environment_probe",
    )
    signing_primitives = (
        "create_capture_authority",
        "load_or_create_capture_secret",
        "capture_environment_probe",
    )

    # Then caller-supplied records cannot be upgraded and no key is report-visible.
    assert all(not hasattr(context, name) for name in signing_methods)
    assert all(not hasattr(resource_manifest_api, name) for name in signing_primitives)
    assert tuple(context.report_dir.glob("*.key")) == ()
    assert tuple(context.report_dir.glob(".*.key")) == ()


def test_coherent_fact_and_receipt_mutation_invalidates_authority(
    tmp_path: Path,
) -> None:
    # Given a valid framework capture is changed coherently by a caller.
    store = manifest_store(tmp_path)
    captured = store.context._capture_environment_probe(_known_request())
    original = captured.receipt.verified_facts[0]
    forged = original.model_copy(update={"value": "/forged/python"})
    facts = tuple(
        forged if fact == original else fact for fact in captured.environment.facts
    )
    environment = captured.environment.model_copy(update={"facts": facts})
    receipt = captured.receipt.model_copy(
        update={
            "probe_id": "replayed-probe",
            "verified_facts": tuple(
                forged if fact == original else fact
                for fact in captured.receipt.verified_facts
            ),
        }
    )

    # When the matching model values retain old authentication tags.
    with pytest.raises(ResourceManifestError) as refusal:
        _ = store.write(
            ResourceManifestUpdate(
                expected_revision=1,
                environments=(environment,),
                probe_receipts=(receipt,),
            )
        )

    # Then fact value and probe identity are both cryptographically bound.
    assert refusal.value.kind is ResourceManifestErrorKind.AUTHORITY_MISMATCH


def test_top_level_capture_authenticates_launcher_backend_and_opencode(
    tmp_path: Path,
) -> None:
    # Given one run captures launcher, backend, and OpenCode observations.
    identity = ResourceManifestIdentity(
        run_id="captured-top-level",
        workflow_digest="e" * 64,
        workspace_digest="f" * 64,
    )
    report_dir = tmp_path / "reports" / identity.run_id
    report_dir.mkdir(parents=True)
    context = ResourceManifestContext.bind(report_dir, identity)
    launcher = context.capture_launcher()
    backend = build_backend_facts(
        BackendFactRequest(
            requested_workflow="wf.yaml",
            effective_workflow="wf.yaml",
            requested_backend="local",
            effective_backend="local",
        )
    )
    opencode = build_opencode_facts(
        OpenCodeFactRequest(
            endpoint="http://127.0.0.1:4096",
            version="1.18.5",
            owner_kind="framework",
            process_id="4321",
        )
    )

    # When the three authenticated top-level records form revision one.
    store = ResourceManifestStore.create(
        context,
        build_initial_manifest(
            identity,
            launcher.facts + backend + opencode,
            (launcher.receipt,),
        ),
    )

    # Then tags are bounded and no authority secret is serialized.
    content = store.path.read_text(encoding="utf-8")
    assert all(
        len(receipt.authority_tag or "") == 64
        for receipt in store.read().probe_receipts
    )
    assert '"_secret"' not in content
    assert '"secret"' not in content


def test_unsigned_public_normalizers_remain_untrusted() -> None:
    # Given public builders can still normalize caller data without a capability.
    known_environment, known_receipt = _known_probe()
    error_environment, error_receipt = _error_probe()

    # When their result models are inspected before persistence.
    tags = (
        known_receipt.authority_tag,
        error_receipt.authority_tag,
        *(fact.authority_tag for fact in known_environment.facts),
        *(fact.authority_tag for fact in error_environment.facts),
    )

    # Then normalization never mints framework authority.
    assert all(tag is None for tag in tags)
