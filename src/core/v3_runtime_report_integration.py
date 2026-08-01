from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core.artifact_store import ArtifactStore
from core.execution_env_context import Phase5ReferenceRequest
from core.execution_env_context import Phase2EnvironmentRequest
from core.phase5_attempt_receipt import (
    AttemptReceiptError,
    load_attempt_receipt,
    receipt_matches_authority,
)
from core.resource_manifest import (
    ResourceManifestError,
    ResourceManifestStore,
    ResourceManifestUpdate,
    build_phase2_environment,
    build_phase5_reference,
)
from core.resource_manifest_models import (
    ContinuationTargetReference,
    EnvironmentRecord,
    FactStatus,
    ResourceManifestErrorKind,
)
from core.resource_manifest_provenance import environment_namespace
from core.run_outcome import RunOutcome
from core.v3_runtime_report import (
    AcceptedReplaySource,
    RuntimeReportRequest,
)


@dataclass(frozen=True)
class RuntimeReportingInputs:
    manifest_store: ResourceManifestStore | None
    artifact_store: ArtifactStore | None
    outcome: RunOutcome
    expected_run_id: str
    phase2_environment: Phase2EnvironmentRequest | None = None


def _accepted_source(
    inputs: RuntimeReportingInputs,
) -> AcceptedReplaySource | None:
    accepted_attempt_id = inputs.outcome.accepted_attempt_id
    artifact_store = inputs.artifact_store
    if accepted_attempt_id is None or artifact_store is None:
        return None
    authority = artifact_store.phase5_attempt_authority_by_id(str(accepted_attempt_id))
    if authority is None or authority.run_id != inputs.expected_run_id:
        return None
    return AcceptedReplaySource(
        receipt_path=Path(authority.receipt_path),
        authority=authority,
    )


def _bind_environment(
    store: ResourceManifestStore,
    source: AcceptedReplaySource,
) -> None:
    try:
        receipt = load_attempt_receipt(source.receipt_path)
    except AttemptReceiptError:
        return
    if not receipt_matches_authority(receipt, source.authority) or not receipt.accepted:
        return
    if receipt.run_id != store.context.identity.run_id:
        return
    manifest = store.read()
    if any(
        reference.attempt_id == receipt.attempt_id
        for reference in manifest.phase5_environment_references
    ):
        return
    namespace_environments = tuple(
        environment
        for environment in manifest.environments
        if environment_namespace(environment) == receipt.backend.namespace
    )
    executable = receipt.invocation.argv[0]
    executable_matches = tuple(
        environment
        for environment in namespace_environments
        if any(
            fact.name == "interpreter.sys_executable" and fact.value == executable
            for fact in environment.facts
        )
    )
    if len(executable_matches) == 1:
        selected_environment = executable_matches[0]
    else:
        target = manifest.continuation_target
        if target is None:
            return
        target_matches = tuple(
            environment
            for environment in executable_matches
            if environment.environment_id == target.environment_id
        )
        if len(target_matches) != 1:
            return
        selected_environment = target_matches[0]
    _ = store.write(
        ResourceManifestUpdate(
            expected_revision=manifest.revision,
            phase5_environment_references=(
                build_phase5_reference(
                    Phase5ReferenceRequest(
                        attempt_id=receipt.attempt_id,
                        environment_id=selected_environment.environment_id,
                        namespace=receipt.backend.namespace,
                    )
                ),
            ),
        )
    )


def _environment_known_executables(
    environment: EnvironmentRecord,
) -> frozenset[str]:
    """Collect every KNOWN non-null ``interpreter.sys_executable`` value.

    The environment model permits multiple provenance facts with the same
    semantic name. Returning only the first would silently mask ambiguity;
    returning all distinct known values lets the caller require exactly
    one before treating the executable as identity proof.
    """
    return frozenset(
        fact.value
        for fact in environment.facts
        if fact.name == "interpreter.sys_executable"
        and fact.status is FactStatus.KNOWN
        and fact.value is not None
    )


def _require_phase2_identity_compatible(
    environment: EnvironmentRecord,
    request: Phase2EnvironmentRequest,
) -> None:
    """Refuse a same-ID record whose namespace or executable is not exact.

    A pre-existing environment with the same identifier may carry richer
    framework-observed facts (e.g. from a probe). Those facts must be
    preserved. But before the explicit continuation target can be set or
    reasserted against that environment, its recorded namespace and
    interpreter executable must be the exact identity the Phase-2 request
    names.

    Identity proof requires exactly one known, non-null
    ``interpreter.sys_executable`` value that equals the Phase-2 request's
    ``python_path``. Zero known values (failed probe, unknown status) and
    multiple distinct values (ambiguous provenance) are both refused —
    absence and ambiguity can never substitute for exact identity proof.
    A same-ID impostor from a different namespace is likewise refused.
    """
    existing_namespace = environment_namespace(environment)
    if existing_namespace != request.namespace:
        raise ResourceManifestError(
            ResourceManifestErrorKind.RUN_CONTEXT_MISMATCH,
            "Phase-2 environment namespace conflicts with an existing "
            + f"same-ID record: {request.environment_id}",
        )
    known_executables = _environment_known_executables(environment)
    if len(known_executables) != 1:
        raise ResourceManifestError(
            ResourceManifestErrorKind.RUN_CONTEXT_MISMATCH,
            "Phase-2 target requires exactly one known interpreter "
            + f"executable for same-ID record: {request.environment_id}",
        )
    known_executable = next(iter(known_executables))
    if known_executable != request.report.python_path:
        raise ResourceManifestError(
            ResourceManifestErrorKind.RUN_CONTEXT_MISMATCH,
            "Phase-2 interpreter executable conflicts with an existing "
            + f"same-ID record: {request.environment_id}",
        )


def _continuation_target_for(
    request: Phase2EnvironmentRequest,
) -> ContinuationTargetReference:
    return ContinuationTargetReference(
        environment_id=request.environment_id,
        namespace=request.namespace,
    )


def _record_phase2_environment(
    store: ResourceManifestStore,
    phase2_environment: Phase2EnvironmentRequest,
) -> None:
    """Record the Phase-2 environment and pin its exact authority atomically.

    For a new Phase-2 environment the environment record and the typed
    continuation-target reference are written in a SINGLE
    ``ResourceManifestUpdate`` so there is no intermediate committed
    manifest containing the newly recorded environment without its
    explicit authority.

    For a pre-existing same-ID environment the function verifies the
    recorded namespace and interpreter executable are compatible with the
    Phase-2 request, then sets or reasserts the continuation target
    WITHOUT re-writing the environment facts (preserving richer
    framework-observed evidence). A same-ID impostor from a different
    namespace or executable is refused.
    """
    current = store.read()
    existing = next(
        (
            environment
            for environment in current.environments
            if environment.environment_id == phase2_environment.environment_id
        ),
        None,
    )
    target = _continuation_target_for(phase2_environment)
    if current.continuation_target == target:
        if existing is not None:
            _require_phase2_identity_compatible(existing, phase2_environment)
        return
    if existing is not None:
        _require_phase2_identity_compatible(existing, phase2_environment)
        _ = store.write(
            ResourceManifestUpdate(
                expected_revision=current.revision,
                continuation_target=target,
            )
        )
        return
    _ = store.write(
        ResourceManifestUpdate(
            expected_revision=current.revision,
            environments=(build_phase2_environment(phase2_environment),),
            continuation_target=target,
        )
    )


def prepare_runtime_report_request(
    inputs: RuntimeReportingInputs,
) -> RuntimeReportRequest:
    source = _accepted_source(inputs)
    store = inputs.manifest_store
    if store is not None and store.context.identity.run_id != inputs.expected_run_id:
        store = None
        source = None
    phase2_environment = inputs.phase2_environment
    try:
        if store is not None and phase2_environment is not None:
            _record_phase2_environment(store, phase2_environment)
        if store is not None and source is not None:
            _bind_environment(store, source)
    except ResourceManifestError:
        store = None
        source = None
    return RuntimeReportRequest(
        manifest_store=store,
        outcome=inputs.outcome,
        expected_run_id=inputs.expected_run_id,
        accepted_receipt=source,
    )
