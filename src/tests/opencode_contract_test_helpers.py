from __future__ import annotations

import json
from pathlib import Path

from harness.session.opencode_contract import JsonObject, JsonValue
from harness.session.opencode_contract_json import load_json


FIXTURES = Path(__file__).parent / "fixtures" / "opencode_v1_18_5"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def json_object(raw: str) -> JsonObject:
    value = load_json(raw)
    assert isinstance(value, dict)
    return value


def object_member(parent: JsonObject, key: str) -> JsonObject:
    value = parent.get(key)
    assert isinstance(value, dict)
    return value


def object_list_member(parent: JsonObject, key: str) -> list[JsonObject]:
    value = parent.get(key)
    assert isinstance(value, list) and all(isinstance(item, dict) for item in value)
    return [item for item in value if isinstance(item, dict)]


def set_object_list_member(
    parent: JsonObject, key: str, items: list[JsonObject]
) -> None:
    values: list[JsonValue] = []
    values.extend(items)
    parent[key] = values


def assistant_info() -> JsonObject:
    return {
        "id": "msg_assistant",
        "sessionID": "ses_root",
        "role": "assistant",
        "parentID": "msg_user",
        "time": {"created": 1, "completed": 2},
        "agent": "build",
        "modelID": "test-model",
        "providerID": "test",
        "mode": "build",
        "path": {"cwd": "D:/workspace", "root": "D:/workspace"},
        "cost": 0,
        "tokens": {
            "input": 1,
            "output": 1,
            "reasoning": 0,
            "cache": {"read": 0, "write": 0},
        },
    }


def user_message() -> JsonObject:
    return {
        "info": {
            "id": "msg_user",
            "sessionID": "ses_root",
            "role": "user",
            "time": {"created": 0},
            "agent": "build",
            "model": {"providerID": "test", "modelID": "test-model"},
        },
        "parts": [],
    }


def tool_part(state: JsonObject, call_id: str = "call_1") -> JsonObject:
    part: JsonObject = {
        "id": "prt_tool",
        "sessionID": "ses_root",
        "messageID": "msg_assistant",
        "type": "tool",
        "tool": "task",
        "state": state,
    }
    if call_id:
        part["callID"] = call_id
    return part


def trace_with_part(part: JsonObject) -> str:
    payload: JsonObject = {
        "health": {"status": 200, "body": {"healthy": True, "version": "1.18.5"}},
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
        "messages": {
            "status": 200,
            "query": {},
            "headers": {},
            "body": [user_message(), {"info": assistant_info(), "parts": [part]}],
        },
        "children": {"status": 200, "body": []},
    }
    return json.dumps(payload)


def snapshot_trace() -> str:
    return trace_with_part(
        {
            "id": "prt_snapshot",
            "sessionID": "ses_root",
            "messageID": "msg_assistant",
            "type": "snapshot",
            "snapshot": "sha256:abc",
        }
    )
