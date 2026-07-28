from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, final

from .resource_manifest_models import (
    FactProvenance,
    ProvenanceFact,
    ResourceManifest,
    ResourceManifestIdentity,
    ResourceManifestUpdate,
)
from .resource_retention import (
    ContainerCleanupStatus,
    ContainerDeleteAuthority,
    ContainerDeletionError,
    ContainerDeletionReceipt,
    ContainerOwnerKind,
    ContainerRetention,
)


class RetentionBackend(Protocol):
    @property
    def container_id(self) -> str | None: ...

    def retention_entry_command(self) -> tuple[str, ...]: ...

    def retention_state(self) -> str: ...

    def delete_container(
        self,
        authority: ContainerDeleteAuthority,
    ) -> ContainerDeletionReceipt: ...


class RetentionManifestBinding(Protocol):
    @property
    def identity(self) -> ResourceManifestIdentity: ...


@dataclass(frozen=True, slots=True)
class RetentionLifecycleRecord:
    requested: ContainerRetention
    effective: ContainerRetention
    owner_kind: ContainerOwnerKind
    entry_command: str
    pre_state: str
    post_state: str
    cleanup_status: ContainerCleanupStatus
    continuation_available: bool


@final
class _MeasurementSeal:
    __slots__ = ()


_MEASUREMENT_SEAL = _MeasurementSeal()


@final
class RetentionLifecycleMeasurement:
    __slots__ = ("_backend", "_binding", "_record", "_seal", "_used")

    def __init__(
        self,
        record: RetentionLifecycleRecord,
        backend: RetentionBackend | None,
        binding: RetentionManifestBinding,
        seal: _MeasurementSeal,
    ) -> None:
        if seal is not _MEASUREMENT_SEAL:
            raise ContainerDeletionError(
                "unknown",
                record.pre_state,
                record.post_state,
                "valid lifecycle measurement capability required",
            )
        self._record = record
        self._backend = backend
        self._binding = binding
        self._seal = seal
        self._used = False

    def __copy__(self) -> RetentionLifecycleMeasurement:
        raise ContainerDeletionError(
            self._backend.container_id
            if self._backend is not None and self._backend.container_id is not None
            else "unknown",
            self._record.pre_state,
            self._record.post_state,
            "lifecycle measurement capability cannot be copied",
        )

    def _consume(
        self,
        binding: RetentionManifestBinding,
        backend: RetentionBackend | None,
    ) -> RetentionLifecycleRecord:
        if (
            self._seal is not _MEASUREMENT_SEAL
            or self._used
            or self._backend is not backend
            or self._binding is not binding
        ):
            raise ContainerDeletionError(
                backend.container_id
                if backend is not None and backend.container_id is not None
                else "unknown",
                self._record.pre_state,
                self._record.post_state,
                "valid lifecycle measurement capability required",
            )
        self._used = True
        return self._record


def _issue_retention_lifecycle_measurement(
    record: RetentionLifecycleRecord,
    backend: RetentionBackend | None,
    binding: RetentionManifestBinding,
) -> RetentionLifecycleMeasurement:
    return RetentionLifecycleMeasurement(record, backend, binding, _MEASUREMENT_SEAL)


def consume_retention_lifecycle_measurement(
    measurement: RetentionLifecycleMeasurement,
    binding: RetentionManifestBinding,
    backend: RetentionBackend | None,
) -> RetentionLifecycleRecord:
    if type(measurement) is not RetentionLifecycleMeasurement:
        raise ContainerDeletionError(
            backend.container_id
            if backend is not None and backend.container_id is not None
            else "unknown",
            "unknown",
            "unknown",
            "valid lifecycle measurement capability required",
        )
    return measurement._consume(binding, backend)


class RetentionLifecycleCaptureContext(RetentionManifestBinding, Protocol):
    def capture_measured_retention_lifecycle(
        self,
        measurement: RetentionLifecycleMeasurement,
        backend: RetentionBackend | None,
        expected_revision: int,
    ) -> ResourceManifestUpdate: ...


class RetentionLifecycleStore(Protocol):
    @property
    def context(self) -> RetentionLifecycleCaptureContext: ...

    def read(self) -> ResourceManifest: ...

    def write(self, update: ResourceManifestUpdate) -> ResourceManifest: ...


def retention_manifest_update(
    record: RetentionLifecycleRecord,
    expected_revision: int,
) -> ResourceManifestUpdate:
    values = (
        ("retention.owner_kind", record.owner_kind),
        ("retention.entry_command", record.entry_command or "not_applicable"),
        ("retention.pre_state", record.pre_state),
        ("retention.post_state", record.post_state),
        ("retention.cleanup_result", record.cleanup_status.value),
        (
            "retention.continuation_available",
            "true" if record.continuation_available else "false",
        ),
    )
    return ResourceManifestUpdate(
        expected_revision=expected_revision,
        facts=tuple(
            ProvenanceFact(
                name=name,
                value=value,
                provenance=FactProvenance.DERIVED,
                namespace="host",
            )
            for name, value in values
        ),
    )


def write_measured_retention_lifecycle(
    store: RetentionLifecycleStore,
    measurement: RetentionLifecycleMeasurement,
    backend: RetentionBackend | None,
) -> ResourceManifest:
    current = store.read()
    authenticated = store.context.capture_measured_retention_lifecycle(
        measurement,
        backend,
        current.revision,
    )
    return store.write(authenticated)
