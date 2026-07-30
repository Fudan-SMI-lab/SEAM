from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from core.runtime_observability import RuntimeObservability
from core.runtime_observability_models import ObservabilitySummary
from core.secret_redaction import redact_json_value, redact_sensitive_text
from harness.session.opencode_contract import JsonObject, JsonValue
from harness.session.opencode_contract_json import load_json

from .e2e_observer_models import CommandMetric, PhaseMetric, SessionMetric

AtomicWriter = Callable[[Path, bytes], None]


@dataclass(frozen=True, slots=True)
class TelemetryArtifactSnapshot:
    output_dir: Path
    run_started_at: str
    run_started_monotonic: float
    metadata: Mapping[str, JsonValue]
    phases: tuple[PhaseMetric, ...]
    sessions: tuple[SessionMetric, ...]
    commands: tuple[CommandMetric, ...]
    events: tuple[JsonObject, ...]
    observability: ObservabilitySummary
    agent_io_paths: Mapping[str, str] | None


def persist_telemetry_artifacts(
    snapshot: TelemetryArtifactSnapshot,
    runtime_observability: RuntimeObservability,
    atomic_writer: AtomicWriter,
) -> dict[str, str]:
    output_path = snapshot.output_dir / "telemetry.json"
    concise_metadata: JsonObject = {
        key: redact_sensitive_text(value) if isinstance(value, str) else value
        for key, value in snapshot.metadata.items()
    }
    metadata: JsonObject = {
        "run_started_at": snapshot.run_started_at,
        "generated_at": _utc_now(),
        "elapsed_seconds": round(time.monotonic() - snapshot.run_started_monotonic, 3),
        "session_count": len(snapshot.sessions),
        "command_count": len(snapshot.commands),
        "review_timeout_observability": asdict(snapshot.observability),
        **concise_metadata,
    }
    if snapshot.agent_io_paths is not None:
        metadata["agent_io_paths"] = dict(snapshot.agent_io_paths)
    phase_payload: list[JsonValue] = [asdict(metric) for metric in snapshot.phases]
    session_payload: list[JsonValue] = [asdict(metric) for metric in snapshot.sessions]
    command_payload: list[JsonValue] = [asdict(metric) for metric in snapshot.commands]
    event_payload: list[JsonValue] = [dict(event) for event in snapshot.events]
    payload: JsonObject = {
        "metadata": metadata,
        "phases": phase_payload,
        "sessions": session_payload,
        "commands": command_payload,
        "events": event_payload,
    }
    serialized = json.dumps(payload, indent=2, ensure_ascii=False)
    atomic_writer(
        output_path,
        json.dumps(
            redact_json_value(load_json(serialized)),
            indent=2,
            ensure_ascii=False,
        ).encode("utf-8"),
    )
    paths = {"telemetry_json": str(output_path)}
    observability_path = runtime_observability.write_artifact(snapshot.observability)
    if observability_path is not None:
        paths["phase_observability_json"] = observability_path
    return paths


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
