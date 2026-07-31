from __future__ import annotations

import logging
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

logger = logging.getLogger(__name__)


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
    if len(executable_matches) > 1:
        phase2_matches = tuple(
            e for e in executable_matches
            if any(f.name == "phase2.base_alias" for f in e.facts)
        )
        if len(phase2_matches) == 1:
            executable_matches = phase2_matches
        else:
            executable_matches = (
                max(executable_matches, key=lambda e: sum(
                    1 for f in e.facts if f.value is not None
                ),),
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
            current = store.read()
            attempt_id = source.authority.attempt_id
            already_bound = any(
                ref.attempt_id == str(attempt_id)
                for ref in current.phase5_environment_references
            )
            if not already_bound and current.environments:
                try:
                    receipt = load_attempt_receipt(source.receipt_path)
                    ns_envs = tuple(
                        e for e in current.environments
                        if environment_namespace(e) == receipt.backend.namespace
                    )
                    target = ns_envs[0] if ns_envs else current.environments[0]
                    _ = store.write(
                        ResourceManifestUpdate(
                            expected_revision=current.revision,
                            phase5_environment_references=(
                                build_phase5_reference(
                                    Phase5ReferenceRequest(
                                        attempt_id=receipt.attempt_id,
                                        environment_id=target.environment_id,
                                        namespace=receipt.backend.namespace,
                                    )
                                ),
                            ),
                        )
                    )
                    logger.warning(
                        "Force-bound phase5 environment reference for attempt %s "
                        "to %s (disambiguation fallback)",
                        receipt.attempt_id,
                        target.environment_id,
                    )
                except (AttemptReceiptError, ResourceManifestError):
                    logger.warning(
                        "Failed to force-bind phase5 environment reference for attempt %s",
                        attempt_id,
                        exc_info=True,
                    )
    except ResourceManifestError:
        store = None
        source = None
    return RuntimeReportRequest(
        manifest_store=store,
        outcome=inputs.outcome,
        expected_run_id=inputs.expected_run_id,
        accepted_receipt=source,
    )
