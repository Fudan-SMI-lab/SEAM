from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol

from typing_extensions import TypeAlias

from core.atomic_file import atomic_write_bytes
from core.run_outcome import TerminalOutcome
from core.secret_redaction import JsonValue, redact_json_value, redact_sensitive_text

from .cleanup import ResourceCleanup
from .models import (
    EMPTY_ARTIFACT_UPDATE,
    FinalizationHooks,
    PhaseStatus,
    RunArtifactUpdate,
)
from .sidecars import copy_run_artifacts, write_json_text
from .v3_snapshot import (
    SnapshotResult as SnapshotResult,
    persist_python_snapshot as persist_python_snapshot,
)

MetricPathSource: TypeAlias = Callable[[], Mapping[str, str]]
CountSource: TypeAlias = Callable[[], int]
CountPairSource: TypeAlias = Callable[[], "RunCounts"]
Action: TypeAlias = Callable[[], None]
CountAction: TypeAlias = Callable[[int], None]
LogSink: TypeAlias = Callable[[str], None]
AgentPathSource: TypeAlias = Callable[[], Mapping[str, str]]


class CleanupFailureAction(Protocol):
    def __call__(
        self,
        resource: str,
        error_type: str,
        detail: str,
    ) -> None: ...


def _ignore_cleanup_failure(
    resource: str,
    error_type: str,
    detail: str,
) -> None:
    del resource, error_type, detail
    return None


@dataclass(frozen=True, slots=True)
class RunCounts:
    session_count: int
    command_count: int


@dataclass(frozen=True, slots=True)
class ObserverSidecar:
    save_metrics: MetricPathSource
    counts: CountPairSource
    record_cleanup_requested: Action
    cleanup_sessions: CountSource
    record_cleaned_sessions: CountAction
    record_cleanup_failure: CleanupFailureAction = _ignore_cleanup_failure


@dataclass(frozen=True, slots=True)
class BridgeSidecar:
    save_metrics: MetricPathSource
    command_count: CountSource


class ObserverSource(Protocol):
    @property
    def session_count(self) -> int: ...

    @property
    def command_count(self) -> int: ...

    def save_metrics(self) -> Mapping[str, str]: ...

    def record_event(
        self,
        event_type: str,
        **details: str | int | bool,
    ) -> None: ...

    def cleanup_all(self) -> int: ...

    def set_metadata(self, key: str, value: int) -> None: ...


class BridgeSource(Protocol):
    def save_metrics(
        self,
        *,
        filename: str = "telemetry.json",
        return_key: str = "telemetry_json",
    ) -> Mapping[str, str]: ...


class AgentPathProvider(Protocol):
    def paths(self) -> Mapping[str, str]: ...


@dataclass(frozen=True, slots=True)
class V3TelemetrySources:
    observer: ObserverSource | None
    bridge: BridgeSource | None
    agent: AgentPathProvider | None
    keep_temp_dir: bool
    bridge_command_count: CountSource | None


def build_telemetry_sidecars(sources: V3TelemetrySources) -> TelemetrySidecars:
    observer: ObserverSidecar | None = None
    if sources.observer is not None:
        source = sources.observer
        observer = ObserverSidecar(
            save_metrics=source.save_metrics,
            counts=lambda: RunCounts(source.session_count, source.command_count),
            record_cleanup_requested=lambda: source.record_event(
                "cleanup_requested", keep_temp_dir=sources.keep_temp_dir
            ),
            cleanup_sessions=source.cleanup_all,
            record_cleaned_sessions=lambda count: source.set_metadata(
                "cleaned_sessions", count
            ),
            record_cleanup_failure=lambda resource, error_type, detail: (
                source.record_event(
                    "cleanup_failed",
                    resource=resource,
                    error_type=error_type,
                    detail=detail,
                )
            ),
        )
    bridge: BridgeSidecar | None = None
    if sources.bridge is not None:
        source_bridge = sources.bridge
        count = sources.bridge_command_count or (lambda: 0)
        bridge = BridgeSidecar(
            save_metrics=lambda: source_bridge.save_metrics(
                filename="telemetry_bridge.json",
                return_key="telemetry_bridge_json",
            ),
            command_count=count,
        )
    return TelemetrySidecars(
        observer=observer,
        bridge=bridge,
        agent_paths=sources.agent.paths if sources.agent is not None else None,
    )


@dataclass(frozen=True, slots=True)
class TelemetrySidecars:
    observer: ObserverSidecar | None = None
    bridge: BridgeSidecar | None = None
    agent_paths: AgentPathSource | None = None

    def counts(self) -> RunCounts:
        if self.observer is not None:
            return self.observer.counts()
        if self.bridge is not None:
            return RunCounts(0, self.bridge.command_count())
        return RunCounts(0, 0)

    def evidence_update(self) -> RunArtifactUpdate:
        files: dict[str, str] = {}
        directories: list[tuple[str, str]] = []
        if self.observer is not None:
            files.update(self.observer.save_metrics())
        if self.bridge is not None:
            files.update(self.bridge.save_metrics())
        if self.agent_paths is not None:
            agent_paths = self.agent_paths()
            files["agent_io_jsonl"] = agent_paths["jsonl"]
            directories.append(("agent_io_payload_dir", agent_paths["payload_dir"]))
        return RunArtifactUpdate(
            telemetry_paths=tuple(files.items()),
            directory_paths=tuple(directories),
        )

    def post_cleanup_update(self, _outcome: TerminalOutcome) -> RunArtifactUpdate:
        if self.observer is None:
            return EMPTY_ARTIFACT_UPDATE
        return RunArtifactUpdate(
            telemetry_paths=tuple(self.observer.save_metrics().items())
        )


@dataclass(frozen=True, slots=True)
class EvidenceContext:
    output_dir: Path
    temp_dir: Path | None
    traceback_text: str | None
    phase_results: tuple[PhaseStatus, ...]


@dataclass(frozen=True, slots=True)
class EvidencePersister:
    context: EvidenceContext
    telemetry: TelemetrySidecars
    log: LogSink

    def __call__(self, _outcome: TerminalOutcome) -> RunArtifactUpdate:
        after_snapshot_path: str | None = None
        artifact_dir: str | None = None
        if self.context.traceback_text is not None:
            atomic_write_bytes(
                self.context.output_dir / "traceback.txt",
                redact_sensitive_text(self.context.traceback_text).encode("utf-8"),
            )
        if self.context.temp_dir is not None:
            snapshot = persist_python_snapshot(
                self.context.temp_dir,
                self.context.output_dir / "after_snapshot.json",
            )
            after_snapshot_path = snapshot.path
            self.log(f"After snapshot: {snapshot.file_count} .py files")
            artifact_dir = copy_run_artifacts(
                self.context.temp_dir, self.context.output_dir
            )
            if artifact_dir:
                self.log(f"Artifacts copied to {artifact_dir}")
        telemetry = self.telemetry.evidence_update()
        phase_payload: JsonValue = [
            {
                "phase_number": phase.phase_number,
                "phase_id": phase.phase_id,
                "label": phase.label,
                "status": phase.status,
                "duration_seconds": phase.duration_seconds,
                "error": phase.error,
            }
            for phase in self.context.phase_results
        ]
        _ = write_json_text(
            self.context.output_dir / "phase_results.json",
            json.dumps(
                redact_json_value(phase_payload),
                indent=2,
                ensure_ascii=False,
                default=str,
            ),
        )
        return RunArtifactUpdate(
            artifact_dir=artifact_dir,
            telemetry_paths=telemetry.telemetry_paths,
            directory_paths=telemetry.directory_paths,
            after_snapshot_path=after_snapshot_path,
        )


@dataclass(frozen=True, slots=True)
class V3RunLifecycle:
    evidence: EvidencePersister
    cleanup: ResourceCleanup
    telemetry: TelemetrySidecars
    started_at: datetime

    def counts(self) -> RunCounts:
        return self.telemetry.counts()

    def hooks(self) -> FinalizationHooks:
        return FinalizationHooks(
            evidence_replay=self.evidence,
            authorized_cleanup=self.cleanup,
            post_cleanup_manifest=self.telemetry.post_cleanup_update,
        )

    def elapsed_seconds(self) -> float:
        return (datetime.now(timezone.utc) - self.started_at).total_seconds()
