from __future__ import annotations

from datetime import datetime, timezone
from typing import Final, Protocol

from harness.session.opencode_contract import JsonObject, JsonValue
from harness.session.trace_export_io import encode_json, inventory_sha256, write_atomic
from harness.session.trace_export_models import (
    OverflowStatus,
    StoredArtifact,
    TraceExportError,
    TraceExportRequest,
    TraceWriteError,
)
from harness.session.trace_correlation_models import TraceCorrelationProjection
from harness.session.trace_correlation_payloads import projection_value
from harness.session.trace_seeds import TraceSeed

MAX_MANIFEST_BYTES: Final = 64 * 1024 * 1024
_TRUNCATION_ERROR_CODES: Final = frozenset(
    {
        "artifact_size_limit_exceeded",
        "overflow_reference_limit_exceeded",
        "trace_depth_limit_exceeded",
        "trace_edge_limit_exceeded",
        "trace_seed_limit_exceeded",
        "trace_session_limit_exceeded",
    }
)


class ManifestIndex(Protocol):
    request: TraceExportRequest
    sessions: list[JsonObject]
    errors: list[JsonObject]
    overflows: list[JsonObject]
    artifacts: list[StoredArtifact]
    versions: set[str]
    capabilities: dict[str, JsonObject]
    message_count: int
    part_count: int
    child_edge_count: int


def write_trace_manifest(
    source: ManifestIndex,
    seeds: dict[str, tuple[TraceSeed, ...]],
    correlation: TraceCorrelationProjection | None = None,
) -> StoredArtifact:
    path = source.request.destination / "manifest.json"
    try:
        content = encode_json(_manifest(source, seeds, correlation))
        if len(content) > MAX_MANIFEST_BYTES:
            raise TraceExportError(path, "manifest size limit exceeded")
        return write_atomic(path, content)
    except TraceWriteError as exc:
        raise TraceExportError(exc.path, exc.detail) from exc


def _manifest(
    source: ManifestIndex,
    seeds: dict[str, tuple[TraceSeed, ...]],
    correlation: TraceCorrelationProjection | None,
) -> JsonObject:
    complete = (
        not source.errors
        and all(record.get("complete") is True for record in source.sessions)
        and (correlation is None or correlation.complete)
    )
    capabilities: JsonObject = {
        session_id: value for session_id, value in source.capabilities.items()
    }
    versions: list[JsonValue] = list(sorted(source.versions))
    server: JsonObject = {
        "versions": versions,
        "capabilities_by_session": capabilities,
    }
    counts: JsonObject = {
        "seed_count": sum(len(values) for values in seeds.values()),
        "unique_seed_count": len(seeds),
        "session_count": len(source.sessions),
        "message_count": source.message_count,
        "part_count": source.part_count,
        "child_edge_count": source.child_edge_count,
        "overflow_reference_count": len(source.overflows),
        "overflow_copied_count": sum(
            item["status"] == OverflowStatus.COPIED.value for item in source.overflows
        ),
    }
    session_ids: list[JsonValue] = [record["session_id"] for record in source.sessions]
    authority: JsonObject = {
        "outcome": False,
        "checkpoint": False,
        "history": "v1",
        "provider_hidden_reasoning": "not_claimed",
    }
    if correlation is not None:
        authority["correlation"] = False
    truncated_by_exporter = any(
        error.get("code") in _TRUNCATION_ERROR_CODES for error in source.errors
    ) or any(record.get("artifact") is None for record in source.sessions)
    raw_policy: JsonObject = {
        "redacted": False,
        "truncated_by_exporter": truncated_by_exporter,
        "source_completeness": "per_session",
        "interpreted": False,
        "executed": False,
    }
    sessions: list[JsonValue] = list(source.sessions)
    overflows: list[JsonValue] = list(source.overflows)
    errors: list[JsonValue] = list(source.errors)
    manifest: JsonObject = {
        "schema": "seam.opencode.raw-trace",
        "schema_version": 2 if correlation is not None else 1,
        "captured_at": source.request.captured_at
        or datetime.now(timezone.utc).isoformat(),
        "capture_scope": "accessible_opencode_v1",
        "complete": complete,
        "authority": authority,
        "raw_policy": raw_policy,
        "server": server,
        "counts": counts,
        "session_ids": session_ids,
        "sessions": sessions,
        "overflows": overflows,
        "artifact_inventory_sha256": inventory_sha256(
            tuple(source.artifacts), source.request.destination
        ),
        "errors": errors,
    }
    if correlation is not None:
        manifest["correlation"] = projection_value(correlation)
    return manifest
