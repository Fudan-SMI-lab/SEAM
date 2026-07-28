from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

from typing_extensions import assert_never

from .execution_env_context import BackendFactRequest, OpenCodeFactRequest
from .resource_manifest import (
    ResourceManifestContext,
    ResourceManifestError,
    ResourceManifestErrorKind,
    ResourceManifestIdentity,
    ResourceManifestStore,
    build_backend_facts,
    build_initial_manifest,
    build_opencode_facts,
)
from .resource_retention import (
    ContinuationContainerDeleteAuthority,
    CurrentRunContainerDeleteAuthority,
    V3ContainerRetentionPolicy,
)
from .resource_retention_finalizer import (
    RetentionLifecycleRecorder,
    retention_manifest_update,
)
from .types import ExecutionBackendConfig

BackendMode = Literal["auto", "local", "container"]
TerminalResourceStatus = Literal[
    "passed", "passed_with_reviews", "failed", "cancelled", "error"
]


class ManifestContainerBackend(Protocol):
    config: ExecutionBackendConfig

    @property
    def container_id(self) -> str | None: ...

    @property
    def environment_probe_status(self) -> str: ...


@dataclass(frozen=True, slots=True)
class RetentionManifestRequest:
    report_dir: Path
    run_id: str
    requested_workflow: Path
    effective_workflow: Path
    workspace: Path
    requested_backend: BackendMode
    endpoint: str
    server_process_id: int | None
    policy: V3ContainerRetentionPolicy
    backend: ManifestContainerBackend | None


def create_retention_manifest(
    request: RetentionManifestRequest,
) -> ResourceManifestStore:
    workflow_digest = hashlib.sha256(
        request.effective_workflow.read_bytes()
    ).hexdigest()
    workspace_identity = os.path.normcase(str(request.workspace.resolve())).encode()
    workspace_digest = hashlib.sha256(workspace_identity).hexdigest()
    identity = ResourceManifestIdentity(
        run_id=request.run_id,
        workflow_digest=workflow_digest,
        workspace_digest=workspace_digest,
    )
    context = ResourceManifestContext.bind(request.report_dir, identity)
    launcher = context.capture_launcher()
    backend_request = _backend_fact_request(request)
    if request.backend is None:
        backend_facts = build_backend_facts(backend_request)
        backend_receipts = ()
    else:
        captured_backend = context._capture_backend_observation(backend_request)
        backend_facts = captured_backend.facts
        backend_receipts = captured_backend.receipts
    opencode_facts = build_opencode_facts(
        OpenCodeFactRequest(
            endpoint=request.endpoint,
            owner_kind=(
                "framework" if request.server_process_id is not None else "external"
            ),
            process_id=(
                str(request.server_process_id)
                if request.server_process_id is not None
                else None
            ),
        )
    )
    manifest = build_initial_manifest(
        identity,
        launcher.facts + backend_facts + opencode_facts,
        launcher.receipts + backend_receipts,
    )
    return ResourceManifestStore.create(context, manifest)


def _backend_fact_request(request: RetentionManifestRequest) -> BackendFactRequest:
    backend = request.backend
    if backend is None or backend.container_id is None:
        return BackendFactRequest(
            requested_workflow=str(request.requested_workflow),
            effective_workflow=str(request.effective_workflow),
            requested_backend=request.requested_backend,
            effective_backend="local",
            owner_kind="unknown",
            retention_requested=request.policy.requested.value,
            retention_effective="retain",
        )
    config = backend.config
    attachment = request.policy.attachment
    current_authority = request.policy.delete_authority
    match current_authority:
        case CurrentRunContainerDeleteAuthority() as authority:
            original_owner = authority.original_owner_run_id
            lineage_root = authority.lineage_root_run_id
            ownership_token = authority.ownership_token
            ownership_label = authority.ownership_label
        case ContinuationContainerDeleteAuthority(attachment=verified):
            original_owner = verified.original_owner_run_id
            lineage_root = verified.lineage_root_run_id
            ownership_token = verified.ownership_token
            ownership_label = verified.ownership_label
        case None if attachment is not None:
            original_owner = attachment.original_owner_run_id
            lineage_root = attachment.lineage_root_run_id
            ownership_token = attachment.ownership_token
            ownership_label = attachment.ownership_label
        case None:
            original_owner = None
            lineage_root = None
            ownership_token = None
            ownership_label = None
        case unreachable:
            assert_never(unreachable)
    image = config.image
    if image is None and config.images:
        image = config.images[0]
    return BackendFactRequest(
        requested_workflow=str(request.requested_workflow),
        effective_workflow=str(request.effective_workflow),
        requested_backend=request.requested_backend,
        effective_backend="container",
        attachment_mode=(
            "image_created" if config.source == "image" else "existing_container"
        ),
        owner_kind=request.policy.owner_kind,
        original_owner_run_id=original_owner,
        lineage_root_run_id=lineage_root,
        framework_ownership_token=ownership_token,
        framework_ownership_label=ownership_label,
        container_runtime=config.runtime,
        container_id=backend.container_id,
        image=image,
        probe_status=backend.environment_probe_status,
        retention_requested=request.policy.requested.value,
        retention_effective=request.policy.effective.value,
    )


@dataclass(frozen=True, slots=True)
class RetentionManifestFinalizer:
    store: ResourceManifestStore
    recorder: RetentionLifecycleRecorder
    backend: ManifestContainerBackend | None = None
    _captured_container_id: str | None = field(init=False, repr=False)

    def __post_init__(self) -> None:
        backend = self.backend
        object.__setattr__(
            self,
            "_captured_container_id",
            backend.container_id if backend is not None else None,
        )

    def persist_and_seal(
        self,
        terminal_status: TerminalResourceStatus,
    ) -> Path:
        record = self.recorder.require_record()
        backend = self.backend
        if (
            record.effective.value == "retain"
            and backend is not None
            and backend.container_id != self._captured_container_id
        ):
            raise ResourceManifestError(
                ResourceManifestErrorKind.RUN_CONTEXT_MISMATCH,
                "retained backend identity changed before manifest sealing",
            )
        current = self.store.read()
        revised = self.store.write(
            retention_manifest_update(
                record,
                expected_revision=current.revision,
            )
        )
        _ = self.store.seal(revised.revision, terminal_status)
        return self.store.path
