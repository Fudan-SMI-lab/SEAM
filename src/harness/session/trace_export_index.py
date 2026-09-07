from __future__ import annotations

import hashlib
from typing import Final, final

from harness.session.opencode_contract import JsonObject, JsonValue
from harness.session.trace_export_io import (
    artifact_value,
    copy_overflow,
    encode_json,
    write_atomic,
)
from harness.session.trace_export_manifest import write_trace_manifest
from harness.session.trace_correlation_models import TraceCorrelationProjection
from harness.session.trace_export_models import (
    OverflowCopyRequest,
    OverflowStatus,
    StoredArtifact,
    TraceExportRequest,
    TraceWriteError,
)
from harness.session.trace_seeds import TraceSeed

MAX_OVERFLOW_REFERENCES: Final = 100_000
MAX_TOTAL_ARTIFACT_BYTES: Final = 512 * 1024 * 1024
MAX_OVERFLOW_REFERENCE_CHARS: Final = 32_768
MAX_TRACE_ERRORS: Final = 100_000
MAX_ERROR_DETAIL_CHARS: Final = 4_096


@final
class TraceExportIndex:
    """Mutable artifact index owned by one synchronous export."""

    def __init__(self, request: TraceExportRequest) -> None:
        self.request: TraceExportRequest = request
        self.sessions: list[JsonObject] = []
        self.errors: list[JsonObject] = []
        self.overflows: list[JsonObject] = []
        self.overflow_by_reference: dict[str, JsonObject] = {}
        self.artifacts: list[StoredArtifact] = []
        self.versions: set[str] = set()
        self.capabilities: dict[str, JsonObject] = {}
        self.message_count: int = 0
        self.part_count: int = 0
        self.child_edge_count: int = 0
        self.total_artifact_bytes: int = 0
        self.error_limit_reported = False
        self.error_limit_sessions: set[str] = set()
        self.overflow_limit_sessions: set[str] = set()

    def add_error(self, code: str, session_id: str | None, detail: str) -> None:
        if len(self.errors) >= MAX_TRACE_ERRORS:
            if session_id is not None:
                self.error_limit_sessions.add(session_id)
            if not self.error_limit_reported:
                self.errors.append(
                    {
                        "code": "trace_error_limit_exceeded",
                        "session_id": None,
                        "detail": "additional errors omitted",
                    }
                )
                self.error_limit_reported = True
            return
        self.errors.append(
            {
                "code": code,
                "session_id": session_id,
                "detail": detail[:MAX_ERROR_DETAIL_CHARS],
            }
        )
        if session_id is None:
            return
        for record in self.sessions:
            if record.get("session_id") != session_id:
                continue
            reasons = record.get("reasons")
            errors = record.get("errors")
            if isinstance(reasons, list) and code not in reasons:
                reasons.append(code)
            if isinstance(errors, list):
                errors.append(code)
            record["complete"] = False
            return

    def error_codes_for(self, session_id: str) -> tuple[str, ...]:
        codes = tuple(
            str(error["code"])
            for error in self.errors
            if error.get("session_id") == session_id
        )
        if session_id in self.error_limit_sessions:
            return (*codes, "trace_error_limit_exceeded")
        return codes

    def capture_overflows(
        self,
        session_id: str,
        references: tuple[str, ...],
    ) -> tuple[str, ...]:
        failures: list[str] = []
        for reference in references:
            existing = self.overflow_by_reference.get(reference)
            if existing is not None:
                session_ids = existing["session_ids"]
                assert isinstance(session_ids, list)
                if session_id not in session_ids:
                    session_ids.append(session_id)
                if existing["status"] != OverflowStatus.COPIED.value:
                    failures.append(f"overflow_{existing['status']}")
                continue
            if len(reference) > MAX_OVERFLOW_REFERENCE_CHARS:
                self._add_overflow_limit_error(session_id)
                if len(self.overflows) < MAX_OVERFLOW_REFERENCES:
                    limit_record: JsonObject = {
                        "reference": reference[:128],
                        "session_ids": [session_id],
                        "status": OverflowStatus.LIMIT_EXCEEDED.value,
                        "artifact": None,
                        "size": None,
                        "sha256": None,
                        "error": "overflow reference length limit exceeded",
                    }
                    self.overflows.append(limit_record)
                failures.append("overflow_limit_exceeded")
                continue
            if len(self.overflow_by_reference) >= MAX_OVERFLOW_REFERENCES:
                self._add_overflow_limit_error(session_id)
                failures.append("overflow_limit_exceeded")
                continue
            if len(self.overflows) >= MAX_OVERFLOW_REFERENCES:
                self._add_overflow_limit_error(session_id)
                failures.append("overflow_limit_exceeded")
                continue
            remaining = MAX_TOTAL_ARTIFACT_BYTES - self.total_artifact_bytes
            if remaining <= 0:
                self.add_error("artifact_size_limit_exceeded", session_id, "")
                exhausted_record: JsonObject = {
                    "reference": reference,
                    "session_ids": [session_id],
                    "status": OverflowStatus.LIMIT_EXCEEDED.value,
                    "artifact": None,
                    "size": None,
                    "sha256": None,
                    "error": "aggregate artifact byte limit exceeded",
                }
                self.overflows.append(exhausted_record)
                self.overflow_by_reference[reference] = exhausted_record
                failures.append("artifact_size_limit_exceeded")
                continue
            capture = copy_overflow(
                OverflowCopyRequest(
                    reference=reference,
                    destination=self.request.destination
                    / "overflows"
                    / f"{hashlib.sha256(reference.encode('utf-8', errors='surrogatepass')).hexdigest()}.bin",
                    allowed_roots=self.request.overflow_roots,
                    max_bytes=min(self.request.max_overflow_bytes, remaining),
                )
            )
            if (
                capture.status is OverflowStatus.OVERSIZED
                and remaining < self.request.max_overflow_bytes
            ):
                capture = type(capture)(
                    OverflowStatus.LIMIT_EXCEEDED,
                    None,
                    "aggregate artifact byte limit exceeded",
                )
            artifact: JsonValue = None
            size: JsonValue = None
            digest: JsonValue = None
            if capture.artifact is not None:
                self.artifacts.append(capture.artifact)
                self.total_artifact_bytes += capture.artifact.size
                artifact = artifact_value(capture.artifact, self.request.destination)
                size = capture.artifact.size
                digest = capture.artifact.sha256
            session_values: list[JsonValue] = [session_id]
            capture_record: JsonObject = {
                "reference": reference,
                "session_ids": session_values,
                "status": capture.status.value,
                "artifact": artifact,
                "size": size,
                "sha256": digest,
                "error": capture.detail,
            }
            self.overflows.append(capture_record)
            self.overflow_by_reference[reference] = capture_record
            if capture.status is not OverflowStatus.COPIED:
                failures.append(f"overflow_{capture.status.value}")
        return tuple(dict.fromkeys(failures))

    def _add_overflow_limit_error(self, session_id: str) -> None:
        if session_id in self.overflow_limit_sessions:
            return
        self.overflow_limit_sessions.add(session_id)
        self.add_error("overflow_reference_limit_exceeded", session_id, "")

    def write_session_payload(
        self,
        session_id: str,
        payload: JsonObject,
        reasons: list[str],
    ) -> JsonValue:
        filename = (
            hashlib.sha256(
                session_id.encode("utf-8", errors="surrogatepass")
            ).hexdigest()
            + ".json"
        )
        path = self.request.destination / "sessions" / filename
        try:
            content = encode_json(payload)
        except (RecursionError, TypeError, ValueError, UnicodeError) as exc:
            reasons.append("artifact_serialization_error")
            self.add_error("artifact_serialization_error", session_id, str(exc))
            return None
        if self.total_artifact_bytes + len(content) > MAX_TOTAL_ARTIFACT_BYTES:
            reasons.append("artifact_size_limit_exceeded")
            self.add_error("artifact_size_limit_exceeded", session_id, "")
            return None
        try:
            artifact = write_atomic(path, content)
        except TraceWriteError as exc:
            reasons.append("artifact_write_interrupted")
            self.add_error("artifact_write_interrupted", session_id, exc.detail)
            return None
        self.artifacts.append(artifact)
        self.total_artifact_bytes += artifact.size
        return artifact_value(artifact, self.request.destination)

    def write_manifest(
        self,
        seeds: dict[str, tuple[TraceSeed, ...]],
        correlation: TraceCorrelationProjection | None = None,
    ) -> StoredArtifact:
        return write_trace_manifest(self, seeds, correlation)
