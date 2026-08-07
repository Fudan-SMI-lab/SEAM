from __future__ import annotations

import json
from typing import Final, Protocol

from harness.session.opencode_contract import JsonObject, JsonValue, TraceContract
from harness.session.opencode_trace_models import EndpointCapture

UNSUPPORTED_STATUSES: Final = (404, 405)


class _JsonSyntaxLoader(Protocol):
    def __call__(self, value: str, /) -> JsonValue: ...


_JSON_LOADS: _JsonSyntaxLoader = json.loads


def _envelope_metadata(
    capture: EndpointCapture,
    messages: bool = False,
) -> JsonObject:
    envelope: JsonObject = {}
    if capture.status is not None:
        envelope["status"] = capture.status
    if messages:
        envelope["query"] = {}
        envelope["headers"] = capture.headers
    return envelope


def _envelope_text(
    capture: EndpointCapture,
    messages: bool = False,
) -> str:
    metadata = json.dumps(
        _envelope_metadata(capture, messages),
        ensure_ascii=False,
    )
    body = _body_text(capture)
    return f'{metadata[:-1]}, "body": {body}}}'


def _body_text(capture: EndpointCapture) -> str:
    raw_body = capture.raw_body
    if not raw_body:
        return json.dumps(capture.body, ensure_ascii=False)
    try:
        _ = _JSON_LOADS(raw_body)
    except (json.JSONDecodeError, RecursionError, ValueError):
        return json.dumps(raw_body, ensure_ascii=False)
    return raw_body


def bundle_text(captures: dict[str, EndpointCapture]) -> str:
    return (
        "{"
        + ", ".join(
            (
                f'"health": {_envelope_text(captures["health"])}',
                f'"doc": {_envelope_text(captures["doc"])}',
                f'"messages": {_envelope_text(captures["messages"], True)}',
                f'"children": {_envelope_text(captures["children"])}',
            )
        )
        + "}"
    )


def bundle_with_raw_evidence(
    captures: dict[str, EndpointCapture],
    name: str,
    capture: EndpointCapture,
) -> str:
    bundle = bundle_text(captures)
    return bundle[:-1] + f", {json.dumps(name)}: {_body_text(capture)}" + "}"


def capture_errors(captures: dict[str, EndpointCapture]) -> list[str]:
    errors: list[str] = []
    for name, capture in captures.items():
        status = capture.status
        if status is None:
            errors.append(f"{name}:transport_error")
            continue
        if status == 200:
            continue
        if name in {"doc", "children"} and status in UNSUPPORTED_STATUSES:
            continue
        errors.append(f"{name}:http_{status}")
    return errors


def identity_errors(session_id: str, contract: TraceContract) -> list[str]:
    errors: list[str] = []
    if any(
        message.info.get("sessionID") != session_id
        for message in contract.messages.messages
    ):
        errors.append("messages:session_mismatch")
    if any(child.parent_id != session_id for child in contract.children.sessions):
        errors.append("children:parent_mismatch")
    return errors
