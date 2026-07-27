from __future__ import annotations

from .continuation_environment_container import container_eligibility
from .continuation_environment_fingerprint import target_environment
from .continuation_environment_manifest import error, required_fact
from .continuation_lock import ActiveProjectOwnerLock as ActiveProjectOwnerLock
from .continuation_environment_models import (
    AnchorRelation as AnchorRelation,
    BindMount as BindMount,
    ContainerDeleteForbidden as ContainerDeleteForbidden,
    ContainerObservation as ContainerObservation,
    ContainerRequirements as ContainerRequirements,
    ContinuationEnvironmentEligibility as ContinuationEnvironmentEligibility,
    ContinuationEnvironmentError as ContinuationEnvironmentError,
    ContinuationEnvironmentErrorKind as ContinuationEnvironmentErrorKind,
    ContinuationEnvironmentRequest as ContinuationEnvironmentRequest,
    EnvironmentFingerprint as EnvironmentFingerprint,
    ExistingContainerAttachment as ExistingContainerAttachment,
    FrameworkContainerDeleteEligible as FrameworkContainerDeleteEligible,
    ParentPhase2State as ParentPhase2State,
    Phase2EstablishmentEligible as Phase2EstablishmentEligible,
    RetainedContainerProbeRequest as RetainedContainerProbeRequest,
    RetainedEnvironmentEligible as RetainedEnvironmentEligible,
    RetainedEnvironmentProbeRequest as RetainedEnvironmentProbeRequest,
)


def verify_continuation_environment(
    request: ContinuationEnvironmentRequest,
) -> ContinuationEnvironmentEligibility:
    """Verify exact retained facts without creating, deleting, or falling back."""
    backend = required_fact(request.resource_manifest.facts, "backend.effective")
    attachment = None
    deletion = ContainerDeleteForbidden("local environment has no container")
    if backend not in ("local", "container"):
        raise error(
            ContinuationEnvironmentErrorKind.BACKEND_MISMATCH,
            "backend.effective",
            "recorded backend is unsupported",
        )
    if backend == "container":
        attachment, deletion = container_eligibility(request)
    elif (
        request.container_requirements is not None
        or request.observed_container is not None
    ):
        raise error(
            ContinuationEnvironmentErrorKind.BACKEND_MISMATCH,
            "backend.effective",
            "local record cannot attach a container",
        )
    target = target_environment(request)
    if target is None:
        return Phase2EstablishmentEligible(
            attachment=attachment,
            deletion=deletion,
        )
    return RetainedEnvironmentEligible(target, attachment, deletion)
