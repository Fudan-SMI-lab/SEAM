from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from core.resource_manifest import (
    BackendFactRequest,
    EnvironmentProbe,
    EnvironmentProbeRequest,
    FactProvenance,
    FactStatus,
    OpenCodeFactRequest,
    Phase2EnvironmentReport,
    Phase2EnvironmentRequest,
    ProbeReceipt,
    ProbedEnvironment,
    ResourceManifestError,
    ResourceManifestErrorKind,
    ResourceManifestUpdate,
    build_backend_facts,
    build_opencode_facts,
    build_phase2_environment,
    probe_environment_record,
)
from tests.resource_manifest_test_support import container_manifest_store


def _probed_environment(environment_id: str, namespace: str) -> ProbedEnvironment:
    return probe_environment_record(
        EnvironmentProbeRequest(
            probe_id=f"probe-{environment_id}-{namespace.replace(':', '-')}",
            environment_id=environment_id,
            namespace=namespace,
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


def test_caller_supplied_backend_and_opencode_values_are_not_observed() -> None:
    # Given caller-supplied backend probe and OpenCode process values.
    backend = build_backend_facts(
        BackendFactRequest(
            requested_workflow="wf.yaml",
            effective_workflow="wf.yaml",
            requested_backend="container",
            effective_backend="container",
            attachment_mode="existing_container",
            owner_kind="user",
            container_runtime="docker",
            container_id="user-dev",
            probe_status="ok",
        )
    )
    opencode = build_opencode_facts(
        OpenCodeFactRequest(
            endpoint="http://127.0.0.1:4096",
            owner_kind="external",
            version="forged-version",
            process_id="forged-pid",
        )
    )

    # When provenance is inspected.
    selected = tuple(
        fact
        for fact in backend + opencode
        if fact.name
        in {
            "container.id",
            "container.probe_status",
            "opencode.version",
            "opencode.pid",
        }
    )

    # Then request values never claim framework observation.
    assert all(
        fact.provenance is not FactProvenance.FRAMEWORK_OBSERVED for fact in selected
    )


def test_new_observed_environment_requires_successful_receipt(tmp_path: Path) -> None:
    # Given a new environment containing framework-observed runtime facts.
    store = container_manifest_store(tmp_path, "cid-a")
    probed = _probed_environment("new-env", "container:cid-a")

    # When it is persisted without its successful probe receipt.
    with pytest.raises(ResourceManifestError) as refusal:
        _ = store.write(
            ResourceManifestUpdate(
                expected_revision=1,
                environments=(probed.environment,),
            )
        )

    # Then unreceipted observations cannot enter the manifest.
    assert refusal.value.kind is ResourceManifestErrorKind.PROVENANCE_ESCALATION


def test_unknown_receipt_cannot_verify_observed_facts() -> None:
    # Given one valid observed fact from a successful probe.
    probed = _probed_environment("env-unknown", "container:cid-a")

    # When an UNKNOWN receipt claims it verified that fact.
    with pytest.raises(ValidationError):
        _ = ProbeReceipt(
            probe_id="probe-unknown",
            environment_id="env-unknown",
            namespace="container:cid-a",
            status=FactStatus.UNKNOWN,
            verified_facts=probed.receipt.verified_facts,
        )

    # Then only successful receipts can establish observed provenance.


def test_changed_namespace_cannot_bypass_probe_promotion(tmp_path: Path) -> None:
    # Given an Agent report in one container namespace.
    store = container_manifest_store(tmp_path, "cid-a")
    reported = build_phase2_environment(
        Phase2EnvironmentRequest(
            environment_id="project-venv",
            namespace="container:cid-a",
            container_id="cid-a",
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
    wrong_namespace = _probed_environment("project-venv", "container:cid-b")

    # When a successful receipt from another namespace tries to promote it.
    with pytest.raises(ResourceManifestError) as refusal:
        _ = store.write(
            ResourceManifestUpdate(
                expected_revision=2,
                environments=(wrong_namespace.environment,),
                probe_receipts=(wrong_namespace.receipt,),
            )
        )

    # Then environment identity cannot cross namespaces.
    assert refusal.value.kind is ResourceManifestErrorKind.RUN_CONTEXT_MISMATCH
