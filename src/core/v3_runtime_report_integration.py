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
    if len(executable_matches) != 1:
        return
    _ = store.write(
        ResourceManifestUpdate(
            expected_revision=manifest.revision,
            phase5_environment_references=(
                build_phase5_reference(
                    Phase5ReferenceRequest(
                        attempt_id=receipt.attempt_id,
                        environment_id=executable_matches[0].environment_id,
                        namespace=receipt.backend.namespace,
                    )
                ),
            ),
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
            current = store.read()
            if not any(
                environment.environment_id == phase2_environment.environment_id
                for environment in current.environments
            ):
                _ = store.write(
                    ResourceManifestUpdate(
                        expected_revision=current.revision,
                        environments=(build_phase2_environment(phase2_environment),),
                    )
                )
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
