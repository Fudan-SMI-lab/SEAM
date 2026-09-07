from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Tuple, final

from core.run_outcome import TerminalOutcome
from core.secret_redaction import redact_sensitive_text
from harness.session.opencode_contract import JsonValue
from harness.session.trace_correlation_models import TraceCorrelationContext
from harness.session.trace_export_models import TraceGraphClient
from harness.session.trace_exporter import TraceExporter, TraceExportRequest
from harness.session.trace_seeds import TraceSeed

from .models import (
    EMPTY_ARTIFACT_UPDATE,
    FinalizationHook,
    RunArtifactUpdate,
)
from .trace_lifecycle_models import TraceCorrelationSummary, TraceLifecycleStatus

TraceClientSource = Callable[[], TraceGraphClient]
TraceSeedSource = Callable[[], Tuple[TraceSeed, ...]]
TraceCorrelationSource = Callable[[], TraceCorrelationContext]
logger = logging.getLogger("harness.run.trace_lifecycle")


class TraceTelemetrySink(Protocol):
    def set_metadata(self, key: str, value: Mapping[str, JsonValue]) -> None: ...

    def record_event(self, event_type: str, **details: JsonValue) -> None: ...


@dataclass(frozen=True)
class TraceCapturePolicy:
    requested: bool
    enabled: bool

    @classmethod
    def from_cli(cls, value: bool | None) -> TraceCapturePolicy:
        if value is None:
            return cls(requested=False, enabled=False)
        return cls(requested=True, enabled=value)


@dataclass(frozen=True)
class TraceLifecycleRequest:
    policy: TraceCapturePolicy
    destination: Path
    client_source: TraceClientSource
    seeds_source: TraceSeedSource
    overflow_roots: tuple[Path, ...] = ()
    telemetry: TraceTelemetrySink | None = None
    correlation_source: TraceCorrelationSource | None = None


@final
class TraceLifecycle:
    __slots__ = ("_request", "_status")

    def __init__(self, request: TraceLifecycleRequest) -> None:
        self._request = request
        self._status = TraceLifecycleStatus(
            requested=request.policy.requested,
            enabled=request.policy.enabled,
            complete=False,
            path=None,
            errors=(),
        )

    @property
    def request(self) -> TraceLifecycleRequest:
        return self._request

    def __call__(self, _outcome: TerminalOutcome) -> RunArtifactUpdate:
        if not self._request.policy.enabled:
            self._publish()
            return EMPTY_ARTIFACT_UPDATE
        try:
            seeds = self._request.seeds_source()
            if not seeds:
                self._status = TraceLifecycleStatus(
                    requested=True,
                    enabled=True,
                    complete=False,
                    path=None,
                    errors=("no_registered_trace_seeds",),
                )
                self._publish()
                return EMPTY_ARTIFACT_UPDATE
            correlation = (
                self._request.correlation_source()
                if self._request.correlation_source is not None
                else None
            )
            result = TraceExporter(self._request.client_source()).export(
                TraceExportRequest(
                    destination=self._request.destination,
                    seeds=seeds,
                    overflow_roots=self._request.overflow_roots,
                    correlation=correlation,
                )
            )
        except Exception as exc:  # noqa: BLE001  # noqa: BROAD_EXCEPT_OK - optional exporter boundary
            self._status = TraceLifecycleStatus(
                requested=True,
                enabled=True,
                complete=False,
                path=None,
                errors=(redact_sensitive_text(f"{type(exc).__name__}: {exc}"),),
            )
            self._publish()
            return EMPTY_ARTIFACT_UPDATE
        manifest_path = result.manifest_path
        correlation_status = None
        if correlation is not None:
            scope = correlation.scope
            correlation_status = TraceCorrelationSummary(
                schema_version=1,
                complete=result.correlation_complete is True,
                run_id=str(scope.run_id),
                parent_run_id=(
                    str(scope.parent_run_id)
                    if scope.parent_run_id is not None
                    else None
                ),
                lineage_root_run_id=str(scope.lineage_root_run_id),
                diagnostics=tuple(
                    redact_sensitive_text(error) for error in result.correlation_errors
                ),
            )
        self._status = TraceLifecycleStatus(
            requested=True,
            enabled=True,
            complete=result.complete,
            path=str(manifest_path),
            errors=tuple(redact_sensitive_text(error) for error in result.errors),
            correlation=correlation_status,
        )
        self._publish()
        return RunArtifactUpdate(
            telemetry_paths=(("agent_trace_manifest", str(manifest_path)),),
            directory_paths=(("agent_trace_dir", str(manifest_path.parent)),),
        )

    def read(self) -> TraceLifecycleStatus:
        return self._status

    def _publish(self) -> None:
        telemetry = self._request.telemetry
        if telemetry is None:
            return
        status = self._status
        payload: dict[str, JsonValue] = {
            "requested": status.requested,
            "enabled": status.enabled,
            "complete": status.complete,
            "path": status.path,
            "errors": list(status.errors),
        }
        if status.correlation is not None:
            payload["correlation"] = {
                "schema_version": status.correlation.schema_version,
                "complete": status.correlation.complete,
                "run_id": status.correlation.run_id,
                "parent_run_id": status.correlation.parent_run_id,
                "lineage_root_run_id": status.correlation.lineage_root_run_id,
                "diagnostics": list(status.correlation.diagnostics),
            }
        try:
            telemetry.set_metadata("agent_trace", payload)
            telemetry.record_event("agent_trace_status", **payload)
        except Exception as exc:  # noqa: BLE001  # noqa: BROAD_EXCEPT_OK - telemetry sink boundary
            logger.warning(
                "Trace telemetry failed with %s: %s",
                type(exc).__name__,
                redact_sensitive_text(str(exc)),
            )


@dataclass(frozen=True)
class _ComposedTraceHook:
    first: FinalizationHook
    second: FinalizationHook

    def __call__(self, outcome: TerminalOutcome) -> RunArtifactUpdate:
        first = self.first(outcome)
        second = self.second(outcome)
        return RunArtifactUpdate(
            artifact_dir=second.artifact_dir or first.artifact_dir,
            telemetry_paths=first.telemetry_paths + second.telemetry_paths,
            directory_paths=first.directory_paths + second.directory_paths,
            before_snapshot_path=(
                second.before_snapshot_path or first.before_snapshot_path
            ),
            after_snapshot_path=second.after_snapshot_path or first.after_snapshot_path,
            entry_script=second.entry_script or first.entry_script,
        )


def compose_trace_hooks(
    first: FinalizationHook,
    second: FinalizationHook,
) -> FinalizationHook:
    return _ComposedTraceHook(first, second)


__all__ = (
    "TraceCapturePolicy",
    "TraceLifecycle",
    "TraceLifecycleRequest",
    "compose_trace_hooks",
)
