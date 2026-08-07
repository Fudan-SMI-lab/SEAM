from __future__ import annotations

from harness.session.opencode_contract import JsonObject, JsonValue
from harness.session import opencode_contract_json
from harness.session.trace_export_models import (
    ResolvedSessionInfo,
    TraceGraphClient,
)


def resolve_session_info(
    client: TraceGraphClient,
    session_id: str,
    child_value: JsonObject | None,
) -> ResolvedSessionInfo:
    if child_value is not None:
        return ResolvedSessionInfo(child_value, None, ())
    try:
        capture = client.get_session_info(session_id)
    except (OSError, RuntimeError, UnicodeError) as exc:
        reason = f"root_session_info_retrieval_error:{type(exc).__name__}"
        return ResolvedSessionInfo(None, None, (reason,))
    if capture.status != 200:
        suffix = (
            f"http_{capture.status}"
            if capture.status is not None
            else "transport_error"
        )
        return ResolvedSessionInfo(
            None,
            capture,
            (f"root_session_info_{suffix}",),
        )
    if (
        capture.raw_body is not None
        and len(capture.raw_body) > opencode_contract_json.MAX_CAPTURE_CHARS
    ):
        return ResolvedSessionInfo(
            None,
            capture,
            ("root_session_info_size_limit_exceeded",),
        )
    value = _raw_info(capture.raw_body, capture.body)
    if value is None:
        return ResolvedSessionInfo(
            None,
            capture,
            ("root_session_info_malformed",),
        )
    if value.get("id") != session_id:
        return ResolvedSessionInfo(
            value,
            capture,
            ("root_session_info_mismatch",),
        )
    return ResolvedSessionInfo(value, capture, ())


def _raw_info(raw_body: str | None, body: JsonValue) -> JsonObject | None:
    if raw_body:
        decoded = opencode_contract_json.decode_capture(raw_body)
        return decoded if isinstance(decoded, dict) else None
    return body if isinstance(body, dict) else None
