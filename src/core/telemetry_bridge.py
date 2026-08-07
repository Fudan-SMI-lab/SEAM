from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from core.agent_io_logger import JsonValue, redact_json_value, redact_sensitive_text


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TelemetryBridge:
    """Record workflow execution telemetry metrics."""

    def __init__(self, output_dir: str) -> None:
        self._output_dir = Path(output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._run_started = time.monotonic()
        self._run_started_iso = _utc_now()
        self._phase_timings: dict[
            str, dict[str, object]
        ] = {}  # phase_id -> {started_at, ended_at, duration, status}
        self._commands: list[dict[str, object]] = []
        self._events: list[dict[str, object]] = []
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
        metric: dict[str, object] | None = self._phase_timings.get(phase_id)
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
        del cmd_preview, resp_preview
        self._command_seq += 1
        self._commands.append(
            {
                "sequence": self._command_seq,
                "phase_id": phase_id,
                "session_id": session_id,
                "duration_seconds": round(duration, 3),
                "status": status,
                "command_length": cmd_length,
                "response_length": resp_length,
                "error": redact_sensitive_text(error) if error is not None else None,
            }
        )

    def on_event(self, event_type: str, **kwargs: JsonValue) -> None:
        evt: dict[str, object] = {
            "event_type": event_type,
            "timestamp": _utc_now(),
        }
        if kwargs:
            evt["details"] = {
                key: redact_json_value(value, key)
                for key, value in kwargs.items()
                if not any(
                    fragment in key.lower()
                    for fragment in (
                        "prompt",
                        "response",
                        "preview",
                        "body",
                        "auth",
                        "secret",
                        "token",
                        "traceback",
                    )
                )
            }
        self._events.append(evt)

    def set_metadata(self, key: str, value: JsonValue) -> None:
        self._metadata[key] = redact_json_value(value, key)

    def save_metrics(
        self,
        *,
        filename: str = "telemetry.json",
        return_key: str = "telemetry_json",
    ) -> dict[str, str]:
        output_path = self._output_dir / filename
        phases_list = list(self._phase_timings.values())
        payload: dict[str, object] = {
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
        _ = output_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return {return_key: str(output_path)}
