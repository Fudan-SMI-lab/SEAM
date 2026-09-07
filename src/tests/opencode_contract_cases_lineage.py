from __future__ import annotations

import json
from typing import final

import pytest

from harness.session.opencode_contract import (
    Completeness,
    Compatibility,
    JsonObject,
    JsonValue,
    ToolPart,
    parse_trace_contract,
)
from tests.opencode_contract_test_helpers import user_message


def _child(session_id: str, parent_id: str) -> JsonObject:
    return {
        "id": session_id,
        "slug": "child",
        "projectID": "prj_1",
        "directory": "D:/workspace",
        "parentID": parent_id,
        "title": "Child",
        "version": "1.18.5",
        "time": {"created": 1, "updated": 2},
    }


def _task_trace(metadata: JsonObject, children: list[JsonObject]) -> str:
    child_values: list[JsonValue] = [child for child in children]
    info: JsonObject = {
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
    part: JsonObject = {
        "id": "prt_task",
        "sessionID": "ses_root",
        "messageID": "msg_assistant",
        "type": "tool",
        "callID": "call_task",
        "tool": "task",
        "state": {
            "status": "completed",
            "input": {},
            "output": "done",
            "title": "Task",
            "metadata": metadata,
            "time": {"start": 1, "end": 2},
        },
    }
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
            "body": [user_message(), {"info": info, "parts": [part]}],
        },
        "children": {"status": 200, "body": child_values},
    }
    return json.dumps(payload)


def _task_part(raw: str) -> tuple[ToolPart, Compatibility, Completeness]:
    contract = parse_trace_contract(raw)
    part = next(
        part
        for message in contract.messages.messages
        for part in message.parts
        if isinstance(part, ToolPart)
    )
    assert isinstance(part, ToolPart)
    return part, contract.compatibility, contract.completeness


@pytest.mark.parametrize(
    "metadata",
    [
        {"parentSessionId": "ses_root"},
        {"sessionId": "ses_child"},
        {"parentSessionId": 7, "sessionId": "ses_child"},
        {"parentSessionId": "ses_root", "sessionId": "child"},
        {"parentSessionId": "ses_root", "sessionId": "ses_root"},
        {"parentSessionId": "ses_other", "sessionId": "ses_child"},
        {
            "parentSessionId": "ses_root",
            "sessionId": "ses_child",
            "model": [],
        },
        {
            "parentSessionId": "ses_root",
            "sessionId": "ses_child",
            "background": "yes",
        },
        {
            "parentSessionId": "ses_root",
            "sessionId": "ses_child",
            "jobId": 7,
        },
    ],
)
def test_malformed_task_lineage_is_incompatible(metadata: JsonObject) -> None:
    # Given / When
    contract = parse_trace_contract(_task_trace(metadata, []))

    # Then
    assert contract.compatibility is Compatibility.INCOMPATIBLE
    assert contract.completeness is Completeness.INCOMPATIBLE


@pytest.mark.parametrize("child_id", ["ses_", "ses_ ", "ses_\u0000"])
def test_schema_legal_task_lineage_suffix_is_partial(child_id: str) -> None:
    # Given
    metadata: JsonObject = {
        "parentSessionId": "ses_root",
        "sessionId": child_id,
    }

    # When
    task, compatibility, completeness = _task_part(_task_trace(metadata, []))

    # Then
    assert task.lineage is not None
    assert task.lineage.child_session_id == child_id
    assert compatibility is Compatibility.COMPATIBLE
    assert completeness is Completeness.PARTIAL


def test_completed_task_without_lineage_is_partial() -> None:
    # Given / When
    task, compatibility, completeness = _task_part(_task_trace({}, []))

    # Then
    assert task.lineage is None
    assert compatibility is Compatibility.COMPATIBLE
    assert completeness is Completeness.PARTIAL


def test_task_without_child_corroboration_is_partial() -> None:
    # Given
    metadata: JsonObject = {
        "parentSessionId": "ses_root",
        "sessionId": "ses_child",
    }

    # When
    task, compatibility, completeness = _task_part(_task_trace(metadata, []))

    # Then
    assert task.lineage is not None
    assert compatibility is Compatibility.COMPATIBLE
    assert completeness is Completeness.PARTIAL


def test_matching_child_confirms_task_lineage() -> None:
    # Given
    metadata: JsonObject = {
        "parentSessionId": "ses_root",
        "sessionId": "ses_child",
    }

    # When
    task, compatibility, completeness = _task_part(
        _task_trace(metadata, [_child("ses_child", "ses_root")])
    )

    # Then
    assert task.lineage is not None
    assert compatibility is Compatibility.COMPATIBLE
    assert completeness is Completeness.COMPLETE


def test_matching_child_with_conflicting_parent_is_incompatible() -> None:
    # Given
    metadata: JsonObject = {
        "parentSessionId": "ses_root",
        "sessionId": "ses_child",
    }

    # When
    contract = parse_trace_contract(
        _task_trace(metadata, [_child("ses_child", "ses_other")])
    )

    # Then
    assert contract.compatibility is Compatibility.INCOMPATIBLE
    assert contract.completeness is Completeness.INCOMPATIBLE


@final
class TestLineage:
    test_malformed_task_lineage_is_incompatible = staticmethod(
        test_malformed_task_lineage_is_incompatible
    )
    test_schema_legal_task_lineage_suffix_is_partial = staticmethod(
        test_schema_legal_task_lineage_suffix_is_partial
    )
    test_completed_task_without_lineage_is_partial = staticmethod(
        test_completed_task_without_lineage_is_partial
    )
    test_task_without_child_corroboration_is_partial = staticmethod(
        test_task_without_child_corroboration_is_partial
    )
    test_matching_child_confirms_task_lineage = staticmethod(
        test_matching_child_confirms_task_lineage
    )
    test_matching_child_with_conflicting_parent_is_incompatible = staticmethod(
        test_matching_child_with_conflicting_parent_is_incompatible
    )
