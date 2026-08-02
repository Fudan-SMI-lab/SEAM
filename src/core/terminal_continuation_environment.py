from __future__ import annotations

from typing import Literal

from .continuation_environment import (
    AnchorRelation,
    ContainerObservation,
    ContainerRequirements,
    ContinuationEnvironmentEligibility,
    ContinuationEnvironmentRequest,
    EnvironmentFingerprint,
    ParentPhase2State,
    RetainedContainerProbeRequest,
    RetainedEnvironmentProbeRequest,
    RetainedEnvironmentEligible,
    verify_continuation_environment,
)
from .continuation_environment_manifest import known_fact, required_fact
from .continuation_hydration_models import (
    ContinuationHydration,
    ParentAcceptedAttemptReference,
)
from .continuation_lock import current_project_owner_lock
from .continuation_models import ResolvedTerminalParent
from .execution_backend import inspect_retained_container, probe_retained_environment
from .resource_manifest_models import EnvironmentRecord
from .terminal_continuation_models import (
    TerminalEnvironmentVerificationRequest,
    TerminalContinuationError,
    TerminalContinuationErrorKind,
)
from .types import ExecutionBackendConfig, WorkflowDefinition


def _target_environment_id(
    parent: ResolvedTerminalParent,
    accepted: ParentAcceptedAttemptReference | None,
) -> str | None:
    manifest = parent.resource_manifest
    if accepted is not None:
        references = tuple(
            reference.environment_reference.value
            for reference in manifest.phase5_environment_references
            if reference.attempt_id == str(accepted.attempt_id)
        )
        if len(references) != 1 or references[0] is None:
            raise TerminalContinuationError(
                TerminalContinuationErrorKind.RESOURCE_CONTEXT_AMBIGUOUS,
                "accepted Phase 5 has no unique retained environment",
            )
        return references[0]
    explicit_target = manifest.continuation_target
    if explicit_target is not None:
        return explicit_target.environment_id
    environment_ids = tuple(item.environment_id for item in manifest.environments)
    if len(environment_ids) > 1:
        raise TerminalContinuationError(
            TerminalContinuationErrorKind.RESOURCE_CONTEXT_AMBIGUOUS,
            "parent has multiple environments without an explicit continuation target",
        )
    if environment_ids:
        raise TerminalContinuationError(
            TerminalContinuationErrorKind.RESOURCE_CONTEXT_AMBIGUOUS,
            "parent has an environment without an explicit continuation target",
        )
    return None


def _environment_record(
    parent: ResolvedTerminalParent,
    environment_id: str | None,
) -> EnvironmentRecord | None:
    if environment_id is None:
        return None
    matches = tuple(
        item
        for item in parent.resource_manifest.environments
        if item.environment_id == environment_id
    )
    if len(matches) != 1:
        raise TerminalContinuationError(
            TerminalContinuationErrorKind.RESOURCE_CONTEXT_AMBIGUOUS,
            "target environment record is not unique",
        )
    return matches[0]


def _anchor_relation(
    workflow: WorkflowDefinition,
    hydration: ContinuationHydration,
) -> AnchorRelation:
    phase_ids = tuple(phase.id for phase in workflow.phases)
    phase2_indexes = tuple(
        index
        for index, phase_id in enumerate(phase_ids)
        if phase_id.startswith("phase_2")
    )
    if not phase2_indexes:
        return AnchorRelation.AFTER_PHASE2
    start_index = phase_ids.index(str(hydration.start_phase_id))
    return (
        AnchorRelation.AT_OR_BEFORE_PHASE2
        if start_index <= phase2_indexes[0]
        else AnchorRelation.AFTER_PHASE2
    )


def _container_requirements(
    config: ExecutionBackendConfig,
    observed_name: str,
) -> ContainerRequirements:
    if config.source == "existing_container":
        expected_name = config.container_name or ""
    else:
        prefix = f"{config.container_name_prefix}-"
        if not observed_name.startswith(prefix):
            raise TerminalContinuationError(
                TerminalContinuationErrorKind.RESOURCE_CONTEXT_AMBIGUOUS,
                "retained container name differs from the pinned workflow prefix",
            )
        expected_name = observed_name
    return ContainerRequirements(
        name=expected_name,
        workdir=config.container_workdir,
        devices=tuple(config.devices),
        project_mount_destination=config.container_workdir,
    )


def verify_terminal_continuation_environment(
    request: TerminalEnvironmentVerificationRequest,
) -> ContinuationEnvironmentEligibility:
    parent = request.parent
    hydration = request.hydration
    workflow = request.workflow
    manifest = parent.resource_manifest
    backend = required_fact(manifest.facts, "backend.effective")
    config = workflow.execution_backend
    observed_container: ContainerObservation | None = None
    requirements: ContainerRequirements | None = None
    runtime: Literal["docker", "podman"] | None = None
    container_id: str | None = None
    if backend == "container":
        if config is None:
            raise TerminalContinuationError(
                TerminalContinuationErrorKind.RESOURCE_CONTEXT_AMBIGUOUS,
                "pinned workflow has no retained container requirements",
            )
        runtime_value = required_fact(manifest.facts, "container.runtime")
        if runtime_value not in {"docker", "podman"}:
            raise TerminalContinuationError(
                TerminalContinuationErrorKind.RESOURCE_CONTEXT_AMBIGUOUS,
                "retained container runtime is unsupported",
            )
        runtime = "docker" if runtime_value == "docker" else "podman"
        container_id = required_fact(manifest.facts, "container.id")
        observed_container = inspect_retained_container(
            RetainedContainerProbeRequest(
                runtime=runtime,
                container_id=container_id,
                expected_ownership_token_sha256=known_fact(
                    manifest.facts,
                    "container.framework_ownership_token_sha256",
                    required=False,
                ),
                expected_ownership_label=known_fact(
                    manifest.facts,
                    "container.framework_ownership_label",
                    required=False,
                ),
            )
        )
        requirements = _container_requirements(config, observed_container.name)
    target_id = _target_environment_id(parent, request.parent_accepted_attempt)
    environment = _environment_record(parent, target_id)
    observed_environment: EnvironmentFingerprint | None = None
    if environment is not None:
        observed_environment = probe_retained_environment(
            RetainedEnvironmentProbeRequest(
                interpreter_path=required_fact(
                    environment.facts, "interpreter.sys_executable"
                ),
                runtime=runtime,
                container_id=container_id,
            )
        )
    relation = _anchor_relation(workflow, hydration)
    return verify_continuation_environment(
        ContinuationEnvironmentRequest(
            resource_manifest=manifest,
            output_project=parent.output_project,
            target_environment_id=target_id,
            parent_phase2_state=(
                ParentPhase2State.TARGET_ESTABLISHED
                if environment is not None
                else ParentPhase2State.FAILED_BEFORE_TARGET
            ),
            anchor_relation=relation,
            child_run_id=request.child_run_id,
            child_lineage_root_run_id=str(parent.run_manifest.lineage_root_run_id),
            container_requirements=requirements,
            observed_container=observed_container,
            observed_environment=observed_environment,
            owner_lock=current_project_owner_lock(),
        )
    )


def apply_verified_continuation_backend(
    _parent: ResolvedTerminalParent,
    workflow: WorkflowDefinition,
    eligibility: ContinuationEnvironmentEligibility,
) -> None:
    attachment = eligibility.attachment
    if attachment is None:
        if isinstance(eligibility, RetainedEnvironmentEligible):
            workflow.execution_backend = ExecutionBackendConfig(mode="local")
        return
    config = workflow.execution_backend
    if config is None:
        raise TerminalContinuationError(
            TerminalContinuationErrorKind.RESOURCE_CONTEXT_AMBIGUOUS,
            "verified container has no pinned backend configuration",
        )
    config.mode = "container"
    config.source = "existing_container"
    config.runtime = attachment.runtime
    config.container_name = attachment.container_id
