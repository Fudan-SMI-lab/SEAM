from __future__ import annotations

from pathlib import Path
from typing import Literal

from core.continuation_environment import (
    ActiveProjectOwnerLock,
    AnchorRelation,
    BindMount,
    ContainerObservation,
    ContainerRequirements,
    ContinuationEnvironmentRequest,
    EnvironmentFingerprint,
    ParentPhase2State,
)
from core.execution_env_context import BackendFactRequest
from core.resource_manifest import (
    EnvironmentRecord,
    EnvironmentType,
    FactProvenance,
    FactStatus,
    ProvenanceFact,
    ResourceManifest,
    build_backend_facts,
)


def fingerprint(
    *,
    environment_type: EnvironmentType = EnvironmentType.BASE,
    namespace: str = "host",
    container_id: str | None = None,
    interpreter: str = "/usr/bin/python3",
    available: bool = True,
    executable: bool = True,
    package_hash: str = "a" * 64,
) -> EnvironmentFingerprint:
    prefix = "/usr" if environment_type is EnvironmentType.BASE else "/project/.venv"
    return EnvironmentFingerprint(
        environment_type=environment_type,
        namespace=namespace,
        container_id=container_id,
        interpreter_realpath=interpreter,
        sys_executable=interpreter,
        sys_prefix=prefix,
        sys_base_prefix="/usr",
        python_implementation="CPython",
        python_version="3.11.9",
        platform_system="Linux",
        platform_architecture="x86_64",
        package_inventory_hash=package_hash,
        interpreter_available=available,
        interpreter_executable=executable,
    )


def environment_record(
    value: EnvironmentFingerprint,
    environment_id: str = "target-env",
) -> EnvironmentRecord:
    names_and_values = (
        ("environment.type", value.environment_type.value),
        ("environment.namespace", value.namespace),
        ("environment.container_id", value.container_id),
        ("interpreter.realpath", value.interpreter_realpath),
        ("interpreter.sys_executable", value.sys_executable),
        ("interpreter.sys_prefix", value.sys_prefix),
        ("interpreter.sys_base_prefix", value.sys_base_prefix),
        ("python.implementation", value.python_implementation),
        ("python.version", value.python_version),
        ("platform.system", value.platform_system),
        ("platform.architecture", value.platform_architecture),
        ("packages.inventory_sha256", value.package_inventory_hash),
    )
    facts = tuple(
        ProvenanceFact(
            name=name,
            value=item,
            detail=None if item is not None else "host environment has no container",
            provenance=(
                FactProvenance.DERIVED
                if name.startswith("environment.")
                else FactProvenance.FRAMEWORK_OBSERVED
            ),
            namespace=value.namespace,
            status=FactStatus.KNOWN if item is not None else FactStatus.UNKNOWN,
        )
        for name, item in names_and_values
    )
    return EnvironmentRecord(environment_id=environment_id, facts=facts)


def manifest(
    *,
    backend: Literal["local", "container"] = "local",
    environment: EnvironmentRecord | None = None,
    owner_kind: Literal["framework", "user", "external", "unknown"] = "framework",
) -> ResourceManifest:
    container = backend == "container"
    facts = build_backend_facts(
        BackendFactRequest(
            requested_workflow="workflow.yaml",
            effective_workflow="workflow.yaml",
            requested_backend=backend,
            effective_backend=backend,
            attachment_mode=(
                "image_created"
                if container and owner_kind == "framework"
                else "existing_container"
                if container
                else None
            ),
            owner_kind=owner_kind,
            original_owner_run_id="parent-run-001"
            if container and owner_kind == "framework"
            else None,
            lineage_root_run_id="parent-run-001"
            if container and owner_kind == "framework"
            else None,
            framework_ownership_token="owner-token"
            if container and owner_kind == "framework"
            else None,
            framework_ownership_label="seam.owner=parent-run-001"
            if container and owner_kind == "framework"
            else None,
            container_runtime="docker" if container else None,
            container_id="immutable-id" if container else None,
            image="sha256:image-id" if container else None,
            probe_status="ok",
        )
    )
    return ResourceManifest(
        run_id="parent-run-001",
        workflow_digest="a" * 64,
        workspace_digest="b" * 64,
        revision=2,
        sealed=True,
        facts=facts,
        environments=() if environment is None else (environment,),
    )


def container_observation(project: Path) -> ContainerObservation:
    return ContainerObservation(
        runtime="docker",
        container_id="immutable-id",
        name="seam-parent",
        running=True,
        image_identity="sha256:image-id",
        image_reference="cpu:test",
        workdir="/workspace",
        devices=("/dev/null",),
        bind_mounts=(BindMount(source=project.resolve(), destination="/workspace"),),
        ownership_token="owner-token",
        ownership_label="seam.owner=parent-run-001",
    )


def request(
    project: Path,
    *,
    resource_manifest: ResourceManifest,
    observed_environment: EnvironmentFingerprint | None,
    observed_container: ContainerObservation | None = None,
    owner_lock: ActiveProjectOwnerLock | None = None,
    phase2_state: ParentPhase2State = ParentPhase2State.TARGET_ESTABLISHED,
    anchor_relation: AnchorRelation = AnchorRelation.AFTER_PHASE2,
) -> ContinuationEnvironmentRequest:
    container = observed_container is not None
    requirements = (
        ContainerRequirements(
            name="seam-parent",
            workdir="/workspace",
            devices=("/dev/null",),
            project_mount_destination="/workspace",
        )
        if container
        else None
    )
    return ContinuationEnvironmentRequest(
        resource_manifest=resource_manifest,
        output_project=project,
        target_environment_id="target-env" if observed_environment else None,
        parent_phase2_state=phase2_state,
        anchor_relation=anchor_relation,
        child_run_id="child-run",
        child_lineage_root_run_id="parent-run-001",
        container_requirements=requirements,
        observed_container=observed_container,
        observed_environment=observed_environment,
        owner_lock=owner_lock,
    )
