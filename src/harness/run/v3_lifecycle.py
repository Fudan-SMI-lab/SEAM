from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Final, Protocol, TypeAlias

from core.run_outcome import TerminalOutcome
from .models import (
    EMPTY_ARTIFACT_UPDATE,
    FinalizationHooks,
    PhaseStatus,
    RunArtifactUpdate,
)
from .sidecars import copy_run_artifacts, write_json_text
from .cleanup import ResourceCleanup

MetricPathSource: TypeAlias = Callable[[], Mapping[str, str]]
CountSource: TypeAlias = Callable[[], int]
CountPairSource: TypeAlias = Callable[[], "RunCounts"]
Action: TypeAlias = Callable[[], None]
CountAction: TypeAlias = Callable[[int], None]
LogSink: TypeAlias = Callable[[str], None]
AgentPathSource: TypeAlias = Callable[[], Mapping[str, str]]
_EXCLUDED_SNAPSHOT_DIRS: Final = frozenset(
    {".git", ".sm-artifacts", ".venv", "__pycache__"}
)


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

    def record_event(self, event_type: str, **details: bool) -> None: ...

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
class SnapshotResult:
    path: str
    file_count: int


def persist_python_snapshot(project_dir: Path, output_path: Path) -> SnapshotResult:
    snapshot: dict[str, dict[str, str]] = {}
    for path in sorted(project_dir.rglob("*.py")):
        relative_path = path.relative_to(project_dir)
        if any(part in _EXCLUDED_SNAPSHOT_DIRS for part in relative_path.parts):
            continue
        content = path.read_text(encoding="utf-8")
        snapshot[str(relative_path)] = {
            "sha256": sha256(content.encode()).hexdigest(),
            "content": content,
        }
    serialized = json.dumps(snapshot, indent=2, ensure_ascii=False, default=str)
    return SnapshotResult(
        path=write_json_text(output_path, serialized),
        file_count=len(snapshot),
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
            _ = (self.context.output_dir / "traceback.txt").write_text(
                self.context.traceback_text,
                encoding="utf-8",
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
        phase_payload = [asdict(phase) for phase in self.context.phase_results]
        _ = write_json_text(
            self.context.output_dir / "phase_results.json",
            json.dumps(phase_payload, indent=2, ensure_ascii=False, default=str),
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
