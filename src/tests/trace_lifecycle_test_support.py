from __future__ import annotations

from collections.abc import Mapping

from harness.session.opencode_contract import JsonValue


class TraceExporterUnavailableError(RuntimeError):
    pass


class CleanupUnavailableError(RuntimeError):
    pass


class TraceTelemetryUnavailableError(RuntimeError):
    pass


class RecordingTraceTelemetry:
    def __init__(self) -> None:
        self.metadata: dict[str, Mapping[str, JsonValue]] = {}
        self.events: list[tuple[str, dict[str, JsonValue]]] = []

    def set_metadata(self, key: str, value: Mapping[str, JsonValue]) -> None:
        self.metadata[key] = value

    def record_event(self, event_type: str, **details: JsonValue) -> None:
        self.events.append((event_type, details))


class ThrowingTraceTelemetry:
    def set_metadata(self, key: str, value: Mapping[str, JsonValue]) -> None:
        del key, value
        raise TraceTelemetryUnavailableError("telemetry unavailable")

    def record_event(self, event_type: str, **details: JsonValue) -> None:
        del event_type, details
        raise TraceTelemetryUnavailableError("telemetry unavailable")
