from __future__ import annotations

import hashlib

import pytest

from core.resource_manifest import (
    BackendFactRequest,
    EnvironmentProbe,
    EnvironmentProbeRequest,
    EnvironmentType,
    FactProvenance,
    FactStatus,
    OpenCodeFactRequest,
    Phase5ReferenceRequest,
    build_backend_facts,
    build_opencode_facts,
    build_phase5_reference,
    probe_environment_record,
)


def _fact_values(facts, name: str):
    return tuple(fact for fact in facts if fact.name == name)


@pytest.mark.parametrize(
    ("backend_request", "attachment", "owner", "namespace"),
    [
        (
            BackendFactRequest(
                requested_workflow="wf.yaml",
                effective_workflow="wf.yaml",
                requested_backend="local",
                effective_backend="local",
            ),
            None,
            "framework",
            "host",
        ),
        (
            BackendFactRequest(
                requested_workflow="wf.yaml",
                effective_workflow="wf.yaml",
                requested_backend="container",
                effective_backend="container",
                attachment_mode="image_created",
                owner_kind="framework",
                original_owner_run_id="run-safe-16",
                lineage_root_run_id="run-safe-16",
                framework_ownership_token="token-16",
                framework_ownership_label="seam.owner=run-safe-16",
                container_runtime="docker",
                container_id="created-1",
                image="cpu:test",
                probe_status="ok",
            ),
            "image_created",
            "framework",
            "container:created-1",
        ),
        (
            BackendFactRequest(
                requested_workflow="wf.yaml",
                effective_workflow="wf.yaml",
                requested_backend="container",
                effective_backend="container",
                attachment_mode="existing_container",
                owner_kind="user",
                container_runtime="docker",
                container_id="user-dev",
                probe_status="probe_failed",
            ),
            "existing_container",
            "user",
            "container:user-dev",
        ),
        (
            BackendFactRequest(
                requested_workflow="wf.yaml",
                effective_workflow="wf.yaml",
                requested_backend="container",
                effective_backend="container",
                attachment_mode="existing_container",
                owner_kind="framework",
                original_owner_run_id="parent-1",
                lineage_root_run_id="parent-1",
                framework_ownership_token="token-parent",
                framework_ownership_label="seam.owner=parent-1",
                container_runtime="podman",
                container_id="retained-1",
                probe_status="ok",
            ),
            "existing_container",
            "framework",
            "container:retained-1",
        ),
    ],
)
def test_backend_matrix_separates_attachment_ownership_and_lineage(
    backend_request: BackendFactRequest,
    attachment: str | None,
    owner: str,
    namespace: str,
) -> None:
    # Given local, created, user-attached, or same-lineage backend context.
    # When typed backend facts are built without starting or deleting resources.
    facts = build_backend_facts(backend_request)

    # Then attachment, owner, original owner, lineage, and namespace stay separate.
    attachment_fact = _fact_values(facts, "container.attachment_mode")[0]
    owner_fact = _fact_values(facts, "container.owner_kind")[0]
    assert attachment_fact.value == attachment
    assert owner_fact.value == owner
    assert owner_fact.namespace == namespace
    if backend_request.owner_kind == "framework" and attachment is not None:
        assert _fact_values(facts, "container.original_owner_run_id")[0].value
        assert _fact_values(facts, "container.lineage_root_run_id")[0].value
    token_attestation = _fact_values(
        facts, "container.framework_ownership_token_sha256"
    )[0].value
    if backend_request.framework_ownership_token is None:
        assert token_attestation is None
    else:
        assert (
            token_attestation
            == hashlib.sha256(
                backend_request.framework_ownership_token.encode()
            ).hexdigest()
        )


@pytest.mark.parametrize(
    ("prefix", "base_prefix", "expected_type"),
    [
        ("/usr", "/usr", EnvironmentType.BASE),
        ("/workspace/.venv", "/usr", EnvironmentType.PROJECT_VENV),
    ],
)
def test_probe_distinguishes_base_and_project_environments(
    prefix: str,
    base_prefix: str,
    expected_type: EnvironmentType,
) -> None:
    # Given complete framework-observed Python runtime facts.
    request = EnvironmentProbeRequest(
        probe_id=f"probe-{expected_type.value}",
        environment_id=f"env-{expected_type.value}",
        namespace="container:cid-1",
        probe=EnvironmentProbe(
            status="ok",
            interpreter_realpath=f"{prefix}/bin/python3.10",
            sys_executable=f"{prefix}/bin/python",
            sys_prefix=prefix,
            sys_base_prefix=base_prefix,
            python_implementation="CPython",
            python_version="3.10.12",
            platform="Linux",
            architecture="x86_64",
            package_inventory_hash="e" * 64,
        ),
    )

    # When the bounded probe becomes an environment record.
    result = probe_environment_record(request)

    # Then env type is derived while runtime values remain framework-observed.
    env_type = _fact_values(result.environment.facts, "environment.type")[0]
    assert env_type.value == expected_type.value
    assert env_type.provenance is FactProvenance.DERIVED
    assert all(
        fact.provenance is FactProvenance.FRAMEWORK_OBSERVED
        for fact in result.receipt.verified_facts
    )


def test_failed_probe_and_remote_opencode_remain_explicit_unknowns() -> None:
    # Given an unavailable environment probe and an existing remote OpenCode endpoint.
    probe = probe_environment_record(
        EnvironmentProbeRequest(
            probe_id="probe-failed",
            environment_id="env-unknown",
            namespace="container:user-dev",
            probe=EnvironmentProbe(status="error", error="python unavailable"),
        )
    )
    opencode = build_opencode_facts(
        OpenCodeFactRequest(
            endpoint="https://opencode.example.test",
            owner_kind="external",
        )
    )

    # When facts are inspected without fabricated fallback values.
    version = _fact_values(opencode, "opencode.version")[0]

    # Then unavailable probe fields are errors and unprobed version stays unknown.
    assert version.status is FactStatus.UNKNOWN
    assert version.value is None
    assert all(
        fact.status is FactStatus.ERROR
        for fact in probe.environment.facts
        if fact.name.startswith(("interpreter.", "python.", "platform.", "packages."))
    )


def test_phase5_reference_is_bounded_and_environment_scoped() -> None:
    # Given one real Phase 5 attempt/environment association.
    request = Phase5ReferenceRequest(
        attempt_id="phase5-attempt-3",
        environment_id="project-venv",
        namespace="container:cid-1",
    )

    # When the reference is recorded.
    reference = build_phase5_reference(request)

    # Then it points to the environment with framework-derived provenance.
    assert reference.attempt_id == "phase5-attempt-3"
    assert reference.environment_reference.value == "project-venv"
    assert reference.environment_reference.provenance is FactProvenance.DERIVED
