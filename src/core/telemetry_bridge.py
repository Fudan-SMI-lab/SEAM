from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path


def _trim_text(text: str, limit: int = 500) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TelemetryBridge:
    """Record workflow execution telemetry metrics."""

    def __init__(self, output_dir: str) -> None:
        self._output_dir = Path(output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._run_started = time.monotonic()
        self._run_started_iso = _utc_now()
        self._phase_timings: dict[str, dict] = (
            {}
        )  # phase_id -> {started_at, ended_at, duration, status}
        self._commands: list[dict] = []
        self._events: list[dict] = []
        self._active_phase: str | None = None
        self._command_seq = 0
        self._metadata: dict[str, object] = {}

    def on_phase_start(self, phase_id: str) -> None:
        started_at = _utc_now()
        self._phase_timings[phase_id] = {
            "phase_id": phase_id,
            "started_at": started_at,
            "ended_at": None,
            "duration_seconds": 0.0,
            "status": "running",
        }
        self._active_phase = phase_id

    def on_phase_end(self, phase_id: str, status: str, duration: float) -> None:
        metric = self._phase_timings.get(phase_id)
        if metric is None:
            metric = {
                "phase_id": phase_id,
                "started_at": _utc_now(),
                "ended_at": None,
                "duration_seconds": 0.0,
                "status": "running",
            }
            self._phase_timings[phase_id] = metric
        metric["ended_at"] = _utc_now()
        metric["duration_seconds"] = round(duration, 3)
        metric["status"] = status

    def on_command(
        self,
        session_id: str,
        phase_id: str,
        cmd_preview: str,
        resp_preview: str,
        duration: float,
        status: str,
        cmd_length: int = 0,
        resp_length: int = 0,
        error: str | None = None,
    ) -> None:
        self._command_seq += 1
        self._commands.append(
            {
                "sequence": self._command_seq,
                "phase_id": phase_id,
                "session_id": session_id,
                "command_preview": _trim_text(cmd_preview),
                "response_preview": _trim_text(resp_preview),
                "duration_seconds": round(duration, 3),
                "status": status,
                "command_length": cmd_length,
                "response_length": resp_length,
                "error": error,
            }
        )

    def on_event(self, event_type: str, **kwargs) -> None:
        evt: dict[str, object] = {
            "event_type": event_type,
            "timestamp": _utc_now(),
        }
        if kwargs:
            evt["details"] = kwargs
        self._events.append(evt)

    def set_metadata(self, key: str, value: object) -> None:
        self._metadata[key] = value

    def save_metrics(
        self,
        *,
        filename: str = "telemetry.json",
        return_key: str = "telemetry_json",
    ) -> dict[str, str]:
        output_path = self._output_dir / filename
        phases_list = list(self._phase_timings.values())
        payload = {
            "metadata": {
                "run_started_at": self._run_started_iso,
                "generated_at": _utc_now(),
                "elapsed_seconds": round(time.monotonic() - self._run_started, 3),
                "session_count": 0,
                "command_count": len(self._commands),
                **self._metadata,
            },
            "phases": phases_list,
            "sessions": [],
            "commands": self._commands,
            "events": self._events,
        }
        output_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return {return_key: str(output_path)}
