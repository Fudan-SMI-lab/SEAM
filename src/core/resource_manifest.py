from __future__ import annotations

from pathlib import Path
from threading import Lock
from typing import Literal, final

from .execution_env_context import (
    BackendFactRequest as BackendFactRequest,
    EnvironmentProbe as EnvironmentProbe,
    EnvironmentProbeRequest as EnvironmentProbeRequest,
    OpenCodeFactRequest as OpenCodeFactRequest,
    Phase2EnvironmentReport as Phase2EnvironmentReport,
    Phase2EnvironmentRequest as Phase2EnvironmentRequest,
    Phase5ReferenceRequest as Phase5ReferenceRequest,
    ProbedEnvironment as ProbedEnvironment,
)
from .execution_env_records import (
    build_phase2_environment as build_phase2_environment,
    capture_local_environment as capture_local_environment,
    probe_environment_record as probe_environment_record,
)
from .execution_env_references import (
    build_phase5_reference as build_phase5_reference,
)
from .resource_manifest_io import (
    atomic_write,
    build_backend_facts as build_backend_facts,
    build_opencode_facts as build_opencode_facts,
    capture_launcher_facts as capture_launcher_facts,
    read_resource_manifest,
    validated_payload,
)
from .resource_manifest_authority import (
    create_resource_capture_authority as _create_capture_authority,
    require_manifest_authority,
)
from .resource_manifest_captures import (
    CapturedFacts as CapturedFacts,
    capture_backend as _capture_backend,
    capture_environment_probe as _capture_environment_probe,
    capture_launcher,
    capture_local_environment as capture_local_environment_with_authority,
)
from .resource_manifest_lock import ResourceManifestLock
from .resource_manifest_paths import (
    ResourceDirectoryBinding,
    bind_resource_directory,
    require_bound_resource_directory,
)
from .resource_manifest_models import (
    RESOURCE_MANIFEST_FILENAME as RESOURCE_MANIFEST_FILENAME,
    RESOURCE_MANIFEST_SCHEMA as RESOURCE_MANIFEST_SCHEMA,
    RESOURCE_MANIFEST_SCHEMA_VERSION as RESOURCE_MANIFEST_SCHEMA_VERSION,
    EnvironmentRecord as EnvironmentRecord,
    EnvironmentType as EnvironmentType,
    FactProvenance as FactProvenance,
    FactStatus as FactStatus,
    Phase5EnvironmentReference as Phase5EnvironmentReference,
    ProbeReceipt as ProbeReceipt,
    ProvenanceFact as ProvenanceFact,
    ResourceManifest as ResourceManifest,
    ResourceManifestError as ResourceManifestError,
    ResourceManifestErrorKind as ResourceManifestErrorKind,
    ResourceManifestIdentity as ResourceManifestIdentity,
    ResourceManifestUpdate as ResourceManifestUpdate,
)
from .resource_manifest_initial import build_initial_manifest as build_initial_manifest
from .resource_manifest_validation import merge_update, validate_manifest_structure


@final
class ResourceManifestContext:
    __slots__ = ("_authority", "_binding", "_identity")

    def __init__(
        self,
        report_dir: Path,
        identity: ResourceManifestIdentity,
    ) -> None:
        binding = bind_resource_directory(report_dir, report_dir.parent)
        canonical = binding.path
        safe = canonical.is_dir() and canonical.name == identity.run_id
        if not safe:
            raise ResourceManifestError(
                ResourceManifestErrorKind.UNSAFE_PATH,
                "resource manifest must use the exact run-qualified report directory",
            )
        self._binding: ResourceDirectoryBinding = binding
        self._identity = identity
        self._authority = _create_capture_authority(identity, canonical)

    @classmethod
    def bind(
        cls,
        report_dir: Path,
        identity: ResourceManifestIdentity,
    ) -> ResourceManifestContext:
        return cls(report_dir, identity)

    @property
    def report_dir(self) -> Path:
        return require_bound_resource_directory(self._binding)

    @property
    def identity(self) -> ResourceManifestIdentity:
        return self._identity

    def capture_launcher(self) -> CapturedFacts:
        return capture_launcher(self._authority)

    def _capture_environment_probe(
        self,
        request: EnvironmentProbeRequest,
    ) -> ProbedEnvironment:
        return _capture_environment_probe(self._authority, request)

    def capture_local_environment(self, environment_id: str) -> ProbedEnvironment:
        return capture_local_environment_with_authority(self._authority, environment_id)

    def _capture_backend_observation(
        self,
        request: BackendFactRequest,
    ) -> CapturedFacts:
        return _capture_backend(self._authority, request)


@final
class ResourceManifestStore:
    __slots__ = ("_context", "_path", "_thread_lock")

    def __init__(self, context: ResourceManifestContext) -> None:
        self._context = context
        self._path = context.report_dir / RESOURCE_MANIFEST_FILENAME
        self._thread_lock = Lock()

    @classmethod
    def create(
        cls,
        context: ResourceManifestContext,
        manifest: ResourceManifest,
    ) -> ResourceManifestStore:
        store = cls(context)
        store._require_identity(manifest)
        if manifest.revision != 1 or manifest.sealed:
            raise ResourceManifestError(
                ResourceManifestErrorKind.VERSION_MISMATCH,
                "initial resource manifest must be unsealed revision one",
            )
        validate_manifest_structure(manifest)
        require_manifest_authority(manifest, context._authority)
        with ResourceManifestLock(context.report_dir):
            if store.path.exists():
                raise ResourceManifestError(
                    ResourceManifestErrorKind.DUPLICATE_MANIFEST,
                    "resource manifest already exists in this report namespace",
                )
            atomic_write(store.path, validated_payload(manifest))
        return store

    @classmethod
    def open(cls, context: ResourceManifestContext) -> ResourceManifestStore:
        store = cls(context)
        _ = store.read()
        return store

    @property
    def context(self) -> ResourceManifestContext:
        return self._context

    @property
    def path(self) -> Path:
        _ = self.context.report_dir
        return self._path

    def _require_identity(self, manifest: ResourceManifest) -> None:
        expected = self.context.identity
        if manifest.run_id != expected.run_id:
            raise ResourceManifestError(
                ResourceManifestErrorKind.RUN_CONTEXT_MISMATCH,
                "resource manifest run_id differs from its report namespace",
            )
        if manifest.workflow_digest != expected.workflow_digest:
            raise ResourceManifestError(
                ResourceManifestErrorKind.DIGEST_MISMATCH,
                "resource manifest workflow digest differs from its pinned value",
            )
        if manifest.workspace_digest != expected.workspace_digest:
            raise ResourceManifestError(
                ResourceManifestErrorKind.RUN_CONTEXT_MISMATCH,
                "resource manifest workspace digest differs from its run context",
            )

    def read(self) -> ResourceManifest:
        manifest = read_resource_manifest(self.path)
        self._require_identity(manifest)
        require_manifest_authority(manifest, self.context._authority)
        return manifest

    def write(self, update: ResourceManifestUpdate) -> ResourceManifest:
        with self._thread_lock, ResourceManifestLock(self.context.report_dir):
            current = self.read()
            if current.sealed:
                raise ResourceManifestError(
                    ResourceManifestErrorKind.SEALED,
                    "sealed resource manifests are immutable",
                )
            revised = merge_update(current, update)
            require_manifest_authority(revised, self.context._authority)
            atomic_write(self.path, validated_payload(revised))
            return revised

    def seal(
        self,
        expected_revision: int,
        terminal_status: Literal[
            "passed", "passed_with_reviews", "failed", "cancelled", "error"
        ],
    ) -> ResourceManifest:
        with self._thread_lock, ResourceManifestLock(self.context.report_dir):
            current = self.read()
            if current.sealed:
                raise ResourceManifestError(
                    ResourceManifestErrorKind.SEALED,
                    "resource manifest is already sealed",
                )
            lifecycle = ProvenanceFact(
                name="lifecycle.status",
                value=terminal_status,
                provenance=FactProvenance.DERIVED,
                namespace="host",
            )
            revised = merge_update(
                current,
                ResourceManifestUpdate(
                    expected_revision=expected_revision,
                    facts=(lifecycle,),
                ),
                terminal_seal=True,
            )
            sealed = ResourceManifest.model_validate(
                {**revised.model_dump(by_alias=True, mode="json"), "sealed": True}
            )
            validate_manifest_structure(sealed)
            require_manifest_authority(sealed, self.context._authority)
            atomic_write(self.path, validated_payload(sealed))
            return sealed
