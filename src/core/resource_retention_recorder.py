from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Protocol, final

from .resource_retention import ContainerDeletionError
from .resource_retention_lifecycle import (
    RetentionBackend,
    RetentionLifecycleMeasurement,
    RetentionLifecycleRecord,
    RetentionManifestBinding,
    _issue_retention_lifecycle_measurement,
)


class RetentionMeasurementProducer(Protocol):
    @property
    def backend(self) -> RetentionBackend | None: ...


_ACTIVE_RETENTION_FINALIZER: ContextVar[RetentionMeasurementProducer | None] = (
    ContextVar("active_retention_finalizer", default=None)
)


def _retention_finalizer_is_active(
    finalizer: RetentionMeasurementProducer,
) -> bool:
    return _ACTIVE_RETENTION_FINALIZER.get() is finalizer


@final
class RetentionLifecycleRecorder:
    __slots__ = (
        "_bound_backend",
        "_bound_container_id",
        "_binding",
        "_measurement_issued",
        "_record",
        "_record_backend",
    )

    def __init__(self) -> None:
        self._bound_backend: RetentionBackend | None = None
        self._bound_container_id: str | None = None
        self._binding: RetentionManifestBinding | None = None
        self._measurement_issued = False
        self._record: RetentionLifecycleRecord | None = None
        self._record_backend: RetentionBackend | None = None

    def bind_manifest(
        self,
        binding: RetentionManifestBinding,
        backend: RetentionBackend | None,
        container_id: str | None,
    ) -> None:
        if (
            type(self) is not RetentionLifecycleRecorder
            or self._binding is not None
            or self._record is not None
        ):
            raise ContainerDeletionError(
                container_id or "unknown",
                "unknown",
                "unknown",
                "valid lifecycle measurement capability required",
            )
        self._binding = binding
        self._bound_backend = backend
        self._bound_container_id = container_id

    def _record_measured(
        self,
        finalizer: RetentionMeasurementProducer,
        value: RetentionLifecycleRecord,
    ) -> None:
        if not _retention_finalizer_is_active(finalizer):
            raise ContainerDeletionError(
                "unknown",
                "unknown",
                "unknown",
                "authorized cleanup finalization stage required",
            )
        if self._binding is not None and (
            self._bound_backend is not finalizer.backend
            or self._bound_container_id
            != (
                finalizer.backend.container_id
                if finalizer.backend is not None
                else None
            )
        ):
            raise ContainerDeletionError(
                finalizer.backend.container_id
                if finalizer.backend is not None
                and finalizer.backend.container_id is not None
                else "unknown",
                value.pre_state,
                value.post_state,
                "recorded retention backend differs from manifest binding",
            )
        self._record_backend = finalizer.backend
        self._record = value

    def require_record(
        self,
        backend: RetentionBackend | None,
    ) -> RetentionLifecycleRecord:
        if self._record is None:
            raise ContainerDeletionError(
                "unknown", "unknown", "unknown", "cleanup did not run"
            )
        if self._record_backend is not backend:
            raise ContainerDeletionError(
                (backend.container_id or "unknown")
                if backend is not None
                else "unknown",
                self._record.pre_state,
                self._record.post_state,
                "recorded retention backend identity changed before manifest sealing",
            )
        return self._record

    def issue_measurement(
        self,
        backend: RetentionBackend | None,
    ) -> RetentionLifecycleMeasurement:
        record = self._record
        binding = self._binding
        if (
            type(self) is not RetentionLifecycleRecorder
            or record is None
            or binding is None
            or self._measurement_issued
            or self._bound_backend is not backend
            or self._record_backend is not backend
            or self._bound_container_id
            != (backend.container_id if backend is not None else None)
        ):
            raise ContainerDeletionError(
                backend.container_id
                if backend is not None and backend.container_id is not None
                else "unknown",
                record.pre_state if record is not None else "unknown",
                record.post_state if record is not None else "unknown",
                "valid lifecycle measurement capability required",
            )
        self._measurement_issued = True
        return _issue_retention_lifecycle_measurement(record, backend, binding)


@contextmanager
def _authorized_retention_finalization(
    finalizer: RetentionMeasurementProducer,
) -> Iterator[None]:
    token = _ACTIVE_RETENTION_FINALIZER.set(finalizer)
    try:
        yield
    finally:
        _ACTIVE_RETENTION_FINALIZER.reset(token)
