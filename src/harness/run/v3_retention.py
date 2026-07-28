from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable

from typing_extensions import assert_never

from core.resource_retention import ContainerDeletionError
from core.resource_retention_finalizer import (
    ContainerRetentionFinalizer,
    _authorized_retention_finalization,
)
from core.resource_retention_manifest import RetentionManifestFinalizer
from core.resource_manifest import ResourceManifestError
from core.run_outcome import TerminalOutcome

from .cleanup import ResourceCleanup
from .models import (
    EMPTY_ARTIFACT_UPDATE,
    FinalizationHookError,
    FinalizationHooks,
    RunArtifactUpdate,
)


@dataclass(frozen=True, slots=True)
class V3AuthorizedResourceCleanup:
    resources: ResourceCleanup
    container: ContainerRetentionFinalizer

    def __call__(self, outcome: TerminalOutcome) -> RunArtifactUpdate:
        container_failure: ContainerDeletionError | None = None
        resource_failure: FinalizationHookError | None = None
        try:
            with _authorized_retention_finalization(self.container):
                self.container.run()
        except ContainerDeletionError as error:
            container_failure = error
        try:
            _ = self.resources(outcome)
        except FinalizationHookError as error:
            resource_failure = error
        if container_failure is not None:
            if resource_failure is None:
                raise container_failure
            raise ContainerDeletionError(
                container_failure.container_id,
                container_failure.pre_state,
                container_failure.post_state,
                f"{container_failure.detail}; {resource_failure}",
            ) from container_failure
        if resource_failure is not None:
            raise resource_failure
        return EMPTY_ARTIFACT_UPDATE


PostCleanupHook = Callable[[TerminalOutcome], RunArtifactUpdate]


@dataclass(frozen=True, slots=True)
class V3RetentionManifestHook:
    manifest: RetentionManifestFinalizer
    existing: PostCleanupHook

    def __call__(self, outcome: TerminalOutcome) -> RunArtifactUpdate:
        match outcome:
            case TerminalOutcome.PASSED:
                terminal_status = "passed"
            case TerminalOutcome.PASSED_WITH_REVIEWS:
                terminal_status = "passed_with_reviews"
            case TerminalOutcome.FAILED:
                terminal_status = "failed"
            case unreachable:
                assert_never(unreachable)
        path = self.manifest.persist_and_seal(terminal_status)
        current = self.existing(outcome)
        return replace(
            current,
            telemetry_paths=current.telemetry_paths
            + (("resource_manifest_json", str(path)),),
        )


@dataclass(frozen=True, slots=True)
class V3RetentionManifestFailureHook:
    error: OSError | ResourceManifestError
    existing: PostCleanupHook

    def __call__(self, outcome: TerminalOutcome) -> RunArtifactUpdate:
        _ = self.existing(outcome)
        raise self.error


def compose_v3_retention_hooks(
    hooks: FinalizationHooks,
    resources: ResourceCleanup,
    container: ContainerRetentionFinalizer,
    manifest: RetentionManifestFinalizer | None,
    manifest_error: OSError | ResourceManifestError | None,
) -> FinalizationHooks:
    post_cleanup = hooks.post_cleanup_manifest
    if manifest is not None:
        post_cleanup = V3RetentionManifestHook(manifest, post_cleanup)
    elif manifest_error is not None:
        post_cleanup = V3RetentionManifestFailureHook(manifest_error, post_cleanup)
    return FinalizationHooks(
        evidence_replay=hooks.evidence_replay,
        trace_export=hooks.trace_export,
        authorized_cleanup=V3AuthorizedResourceCleanup(resources, container),
        post_cleanup_manifest=post_cleanup,
    )
