from __future__ import annotations

import json
from dataclasses import dataclass as frozen_dataclass
from dataclasses import replace
from typing import final

from harness.session.opencode_contract import (
    CapabilityState,
    ChildSession,
    ChildrenResult,
    Completeness,
    Compatibility,
    JsonObject,
    JsonValue,
    parse_trace_contract,
)
from harness.session.opencode_trace_models import (
    EndpointCapture,
    SessionGraphRetrieval,
    TraceCapabilityState,
)
from harness.session.trace_seeds import (
    TraceSeed,
    TraceSeedMetadataState,
)


@final
class FakeTraceClient:
    def __init__(
        self,
        graphs: dict[str, SessionGraphRetrieval],
        session_infos: dict[str, EndpointCapture] | None = None,
    ) -> None:
        self._graphs = graphs
        self._session_infos = session_infos or {
            session_id: session_info_capture(session_id) for session_id in graphs
        }
        self.calls: list[str] = []
        self.info_calls: list[str] = []

    def get_session_info(self, session_id: str) -> EndpointCapture:
        self.info_calls.append(session_id)
        return self._session_infos[session_id]

    def retrieve_session_graph(self, session_id: str) -> SessionGraphRetrieval:
        self.calls.append(session_id)
        return self._graphs[session_id]


@final
@frozen_dataclass(frozen=True)
class GraphFixture:
    __slots__ = ("retrieval", "messages")
    retrieval: SessionGraphRetrieval
    messages: list[JsonValue]


def seed(session_id: str, *, complete: bool = True) -> TraceSeed:
    return TraceSeed(
        session_id=session_id,
        logical_role="main" if complete else None,
        scope="phase:5" if complete else None,
        lifecycle="persistent",
        agent="build",
        working_directory="D:/workspace",
        metadata_state=(
            TraceSeedMetadataState.COMPLETE
            if complete
            else TraceSeedMetadataState.PARTIAL
        ),
    )


def child_info(session_id: str, parent_id: str) -> JsonObject:
    return {
        "id": session_id,
        "parentID": parent_id,
        "slug": f"slug-{session_id}",
        "projectID": "prj_trace",
        "directory": "D:/workspace",
        "title": f"Child {session_id}",
        "version": "1.18.5",
        "time": {"created": 1, "updated": 2},
        "futureSessionInfo": {"exact": [True, None, 7]},
    }


def root_info(session_id: str) -> JsonObject:
    return {
        "id": session_id,
        "slug": f"slug-{session_id}",
        "projectID": "prj_trace",
        "directory": "D:/workspace",
        "title": f"Root {session_id}",
        "version": "1.18.5",
        "time": {"created": 0, "updated": 2},
        "futureRootInfo": {"exact": [True, None, 9]},
    }


def session_info_capture(
    session_id: str,
    status: int = 200,
) -> EndpointCapture:
    body: JsonValue = root_info(session_id) if status == 200 else "session unavailable"
    return EndpointCapture(
        CapabilityState.SUPPORTED if status == 200 else CapabilityState.UNKNOWN,
        status,
        body,
        {},
        json.dumps(body, ensure_ascii=False),
        None if status == 200 else "session unavailable",
        {"ok": status == 200, "status": status, "data": body},
    )


def graph(
    session_id: str,
    *,
    child_ids: tuple[str, ...] = (),
    parts: tuple[JsonObject, ...] = (),
    child_status: int = 200,
    version: str = "1.18.5",
    malformed_messages: bool = False,
    fallback_ids: tuple[str, ...] = (),
    fallback_capture_only: bool = False,
) -> GraphFixture:
    children: list[JsonValue] = [
        child_info(child_id, session_id) for child_id in child_ids
    ]
    messages = _messages(session_id, parts)
    message_body: JsonValue = (
        [{"malformed": ["retained"]}] if malformed_messages else messages
    )
    payload: JsonObject = {
        "health": {"status": 200, "body": {"healthy": True, "version": version}},
        "doc": {
            "status": 200,
            "body": {
                "paths": {
                    "/global/health": {},
                    "/session/{sessionID}/message": {},
                    "/session/{sessionID}/children": {},
                }
            },
        },
        "messages": {"status": 200, "query": {}, "headers": {}, "body": message_body},
        "children": {"status": child_status, "body": children},
    }
    contract = parse_trace_contract(json.dumps(payload, ensure_ascii=False))
    errors = _errors(child_status, malformed_messages)
    state = _state(contract.compatibility, contract.completeness, errors)
    fallback_children = None
    fallback_capture = None
    if fallback_ids or fallback_capture_only:
        raw_fallback: list[JsonValue] = [
            child_info(child_id, session_id) for child_id in fallback_ids
        ]
        if not fallback_capture_only:
            fallback_children = ChildrenResult(
                tuple(
                    _child_from_raw(item, session_id)
                    for item in raw_fallback
                    if isinstance(item, dict)
                ),
                CapabilityState.SUPPORTED,
                Completeness.COMPLETE,
                raw_fallback,
            )
        fallback_capture = EndpointCapture(
            CapabilityState.SUPPORTED,
            200,
            raw_fallback,
            {},
            json.dumps(raw_fallback, ensure_ascii=False),
            None,
            {"ok": True, "status": 200, "data": raw_fallback},
        )
    retrieval = SessionGraphRetrieval(
        state,
        contract,
        fallback_children,
        fallback_capture,
        errors,
    )
    return GraphFixture(retrieval, messages)


def with_duplicate_child(
    fixture: GraphFixture, session_id: str, child_id: str
) -> GraphFixture:
    raw = child_info(child_id, session_id)
    child = _child_from_raw(raw, session_id)
    children = ChildrenResult(
        (child, child),
        CapabilityState.SUPPORTED,
        Completeness.COMPLETE,
        [raw, raw],
    )
    contract = replace(fixture.retrieval.contract, children=children)
    return replace(fixture, retrieval=replace(fixture.retrieval, contract=contract))


def _child_from_raw(raw: JsonObject, parent_id: str) -> ChildSession:
    session_id = raw.get("id")
    assert isinstance(session_id, str)
    return ChildSession(session_id, parent_id, raw)


def _messages(session_id: str, parts: tuple[JsonObject, ...]) -> list[JsonValue]:
    user: JsonObject = {
        "info": {
            "id": f"msg_user_{session_id}",
            "sessionID": session_id,
            "role": "user",
            "time": {"created": 0},
            "agent": "build",
            "model": {"providerID": "test", "modelID": "model"},
            "futureInfo": {"unknown": "kept"},
        },
        "parts": [],
    }
    if not parts:
        return [user]
    assistant: JsonObject = {
        "info": {
            "id": f"msg_assistant_{session_id}",
            "sessionID": session_id,
            "role": "assistant",
            "parentID": f"msg_user_{session_id}",
            "time": {"created": 1, "completed": 5},
            "agent": "build",
            "modelID": "model",
            "providerID": "test",
            "mode": "build",
            "path": {"cwd": "D:/workspace", "root": "D:/workspace"},
            "cost": 0,
            "tokens": {
                "input": 1,
                "output": 1,
                "reasoning": 1,
                "cache": {"read": 0, "write": 0},
            },
        },
        "parts": list(parts),
        "futureMessage": [1, None, True],
    }
    return [user, assistant]


def _errors(child_status: int, malformed_messages: bool) -> tuple[str, ...]:
    errors: list[str] = []
    if child_status not in {200, 404, 405}:
        errors.append(f"children:http_{child_status}")
    if malformed_messages:
        errors.append("malformed_contract")
    return tuple(errors)


def _state(
    compatibility: Compatibility,
    completeness: Completeness,
    errors: tuple[str, ...],
) -> TraceCapabilityState:
    if errors:
        return TraceCapabilityState.ERROR
    if compatibility is Compatibility.INCOMPATIBLE:
        return TraceCapabilityState.ERROR
    if completeness is Completeness.COMPLETE:
        return TraceCapabilityState.COMPATIBLE
    return TraceCapabilityState.PARTIAL
