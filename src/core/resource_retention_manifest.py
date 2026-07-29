from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

from .execution_env_context import (
    BackendFactRequest,
    EnvironmentProbe,
    EnvironmentProbeRequest,
    OpenCodeFactRequest,
)
from .resource_manifest import (
    ResourceManifestContext,
    ResourceManifestError,
    ResourceManifestErrorKind,
    ResourceManifestIdentity,
    ResourceManifestStore,
    ResourceManifestUpdate,
    build_backend_facts,
    build_initial_manifest,
    build_opencode_facts,
)
from .resource_manifest_status import (
    TerminalResourceStatus as TerminalResourceStatus,
)
from .resource_retention import (
    ContinuationContainerDeleteAuthority,
    CurrentRunContainerDeleteAuthority,
    V3ContainerRetentionPolicy,
)
from .resource_retention_finalizer import (
    RetentionLifecycleRecorder,
)
from .resource_retention_lifecycle import (
    RetentionBackend,
    write_measured_retention_lifecycle,
)
from .types import ExecutionBackendConfig

BackendMode = Literal["auto", "local", "container"]


class ManifestContainerBackend(RetentionBackend, Protocol):
    config: ExecutionBackendConfig

    @property
    def container_id(self) -> str | None: ...

    @property
    def container_name(self) -> str | None: ...

    @property
    def environment_probe_status(self) -> str: ...

    @property
    def observed_environment_probe(self) -> EnvironmentProbe | None: ...


@dataclass(frozen=True)
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
    store = ResourceManifestStore.create(context, manifest)
    if request.backend is None or request.backend.container_id is None:
        environment = context.capture_local_environment("execution-python")
    else:
        probe = request.backend.observed_environment_probe or EnvironmentProbe(
            status="error",
            error=(
                "container environment probe unavailable: "
                f"{request.backend.environment_probe_status}"
            )[:1024],
        )
        environment = context._capture_environment_probe(
            EnvironmentProbeRequest(
                probe_id="probe-execution-python",
                environment_id="execution-python",
                namespace=f"container:{request.backend.container_id}",
                probe=probe,
            )
        )
    current = store.read()
    _ = store.write(
        ResourceManifestUpdate(
            expected_revision=current.revision,
            environments=(environment.environment,),
            probe_receipts=(environment.receipt,),
        )
    )
    return store


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
    if isinstance(current_authority, CurrentRunContainerDeleteAuthority):
        original_owner = current_authority.original_owner_run_id
        lineage_root = current_authority.lineage_root_run_id
        ownership_token = current_authority.ownership_token
        ownership_label = current_authority.ownership_label
    elif isinstance(current_authority, ContinuationContainerDeleteAuthority):
        verified = current_authority.attachment
        original_owner = verified.original_owner_run_id
        lineage_root = verified.lineage_root_run_id
        ownership_token = verified.ownership_token
        ownership_label = verified.ownership_label
    elif attachment is not None:
        original_owner = attachment.original_owner_run_id
        lineage_root = attachment.lineage_root_run_id
        ownership_token = attachment.ownership_token
        ownership_label = attachment.ownership_label
    else:
        original_owner = None
        lineage_root = None
        ownership_token = None
        ownership_label = None
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
        container_name=backend.container_name,
        container_id=backend.container_id,
        image=image,
        container_workdir=config.container_workdir,
        container_mount_source=str(request.workspace.resolve()),
        container_mount_destination=config.container_workdir,
        probe_status=backend.environment_probe_status,
        retention_requested=request.policy.requested.value,
        retention_effective=request.policy.effective.value,
    )


@dataclass(frozen=True)
class RetentionManifestFinalizer:
    store: ResourceManifestStore
    recorder: RetentionLifecycleRecorder
    backend: RetentionBackend | None = None
    _captured_container_id: str | None = field(init=False, repr=False)

    def __post_init__(self) -> None:
        backend = self.backend
        manifest = self.store.read()
        manifest_container_id = next(
            (fact.value for fact in manifest.facts if fact.name == "container.id"),
            None,
        )
        backend_container_id = backend.container_id if backend is not None else None
        if manifest_container_id != backend_container_id:
            raise ResourceManifestError(
                ResourceManifestErrorKind.RUN_CONTEXT_MISMATCH,
                "manifest container identity differs from retention backend",
            )
        self.recorder.bind_manifest(
            self.store.context,
            backend,
            manifest_container_id,
        )
        object.__setattr__(
            self,
            "_captured_container_id",
            backend_container_id,
        )

    def persist_and_seal(
        self,
        terminal_status: TerminalResourceStatus,
    ) -> Path:
        backend = self.backend
        deleted = self.recorder.recorded_deletion_matches(backend)
        if (
            backend is not None
            and backend.container_id != self._captured_container_id
            and not deleted
        ):
            raise ResourceManifestError(
                ResourceManifestErrorKind.RUN_CONTEXT_MISMATCH,
                "retained backend identity changed before manifest sealing",
            )
        measurement = self.recorder.issue_measurement(backend)
        revised = write_measured_retention_lifecycle(
            self.store,
            measurement,
            backend,
        )
        _ = self.store.seal(revised.revision, terminal_status)
        return self.store.path
