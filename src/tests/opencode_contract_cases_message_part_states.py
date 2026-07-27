from __future__ import annotations

import pytest
from typing import final

from harness.session.opencode_contract import (
    CompletedToolState,
    Completeness,
    Compatibility,
    ErrorToolState,
    JsonObject,
    PendingToolState,
    ReasoningPart,
    RunningToolState,
    TextPart,
    ToolPart,
    UnknownPart,
    UnknownToolState,
    parse_trace_contract,
)
from tests.opencode_contract_test_helpers import fixture, tool_part, trace_with_part


def test_with_parts_discriminates_known_and_unknown_variants() -> None:
    # Given / When
    contract = parse_trace_contract(fixture("trace_complete.json"))

    # Then
    parts = [part for message in contract.messages.messages for part in message.parts]
    assert isinstance(parts[0], TextPart)
    assert isinstance(parts[1], ReasoningPart)
    tools = [part for part in parts if isinstance(part, ToolPart)]
    assert [type(part.state) for part in tools] == [
        PendingToolState,
        RunningToolState,
        CompletedToolState,
        ErrorToolState,
        UnknownToolState,
    ]
    assert isinstance(parts[-1], UnknownPart)
    assert contract.messages.completeness is Completeness.PARTIAL


def test_malformed_required_message_info_is_incompatible() -> None:
    # Given / When
    contract = parse_trace_contract(fixture("trace_malformed_known_fields.json"))

    # Then
    assert contract.compatibility is Compatibility.INCOMPATIBLE
    assert contract.messages.completeness is Completeness.INCOMPATIBLE


def test_missing_tool_name_and_completed_fields_are_incompatible() -> None:
    # Given
    raw = fixture("trace_complete.json").replace(
        '"tool": "task",', '"futureTool": "task",', 1
    )

    # When
    contract = parse_trace_contract(raw)

    # Then
    assert contract.compatibility is Compatibility.INCOMPATIBLE
    assert contract.messages.completeness is Completeness.INCOMPATIBLE


def test_lowercase_cursor_header_marks_history_partial() -> None:
    # Given
    raw = fixture("trace_complete.json").replace(
        '"headers": {},', '"headers": {"x-next-cursor": "next"},', 1
    )

    # When
    contract = parse_trace_contract(raw)

    # Then
    assert contract.messages.is_full_history is False
    assert contract.messages.completeness is Completeness.PARTIAL


@pytest.mark.parametrize(
    "part",
    [
        tool_part(
            {
                "status": "completed",
                "input": {},
                "output": "done",
                "title": "Task",
                "metadata": {},
                "time": {"start": 1, "end": 2},
            },
            call_id="",
        ),
        tool_part(
            {
                "status": "completed",
                "input": [],
                "output": "done",
                "title": "Task",
                "metadata": {},
                "time": {"start": 1, "end": 2},
            }
        ),
        tool_part(
            {
                "status": "completed",
                "input": {},
                "output": 7,
                "title": "Task",
                "metadata": {},
                "time": {"start": 1, "end": 2},
            }
        ),
        tool_part(
            {
                "status": "error",
                "input": {},
                "error": {"message": "bad"},
                "time": {"start": 1, "end": 2},
            }
        ),
        tool_part({"status": "running", "input": {}, "time": {"start": -1}}),
    ],
)
def test_malformed_tool_base_or_state_is_incompatible(part: JsonObject) -> None:
    # Given / When
    contract = parse_trace_contract(trace_with_part(part))

    # Then
    assert contract.compatibility is Compatibility.INCOMPATIBLE
    assert contract.messages.completeness is Completeness.INCOMPATIBLE


def test_part_base_identifiers_must_match_message() -> None:
    # Given
    part: JsonObject = {
        "id": "prt_text",
        "sessionID": "other_session",
        "messageID": "other_message",
        "type": "text",
        "text": "hello",
    }

    # When
    contract = parse_trace_contract(trace_with_part(part))

    # Then
    assert contract.compatibility is Compatibility.INCOMPATIBLE


def test_known_opaque_part_is_not_misclassified_as_unknown() -> None:
    # Given
    part: JsonObject = {
        "id": "prt_snapshot",
        "sessionID": "ses_root",
        "messageID": "msg_assistant",
        "type": "snapshot",
        "snapshot": "sha256:abc",
    }

    # When
    contract = parse_trace_contract(trace_with_part(part))

    # Then
    parsed = contract.messages.messages[-1].parts[0]
    assert type(parsed).__name__ == "KnownPart"
    assert contract.messages.completeness is Completeness.COMPLETE


@pytest.mark.parametrize(
    "part",
    [
        {
            "id": "prt_file",
            "sessionID": "ses_root",
            "messageID": "msg_assistant",
            "type": "file",
            "mime": "text/plain",
            "url": "file:///a.txt",
            "source": {"type": "file", "path": "a.txt"},
        },
        {
            "id": "prt_agent",
            "sessionID": "ses_root",
            "messageID": "msg_assistant",
            "type": "agent",
            "name": "build",
            "source": {"value": "x", "start": -1, "end": 1},
        },
        {
            "id": "prt_retry",
            "sessionID": "ses_root",
            "messageID": "msg_assistant",
            "type": "retry",
            "attempt": 1,
            "error": {},
            "time": {"created": 1},
        },
        {
            "id": "prt_finish",
            "sessionID": "ses_root",
            "messageID": "msg_assistant",
            "type": "step-finish",
            "reason": "stop",
            "snapshot": 7,
            "cost": 0,
            "tokens": {
                "input": 1,
                "output": 1,
                "reasoning": 0,
                "cache": {"read": 0, "write": 0},
            },
        },
        {
            "id": "prt_reasoning",
            "sessionID": "ses_root",
            "messageID": "msg_assistant",
            "type": "reasoning",
            "text": "x",
            "time": {"start": 1, "end": "invalid"},
        },
        tool_part(
            {
                "status": "completed",
                "input": {},
                "output": "done",
                "title": "Task",
                "metadata": {},
                "time": {"start": 1, "end": 2},
                "attachments": [{"type": "text"}],
            }
        ),
    ],
)
def test_malformed_nested_known_fields_are_incompatible(part: JsonObject) -> None:
    # Given / When
    contract = parse_trace_contract(trace_with_part(part))

    # Then
    assert contract.compatibility is Compatibility.INCOMPATIBLE


@final
class TestMessagePartStates:
    test_with_parts_discriminates_known_and_unknown_variants = staticmethod(
        test_with_parts_discriminates_known_and_unknown_variants
    )
    test_malformed_required_message_info_is_incompatible = staticmethod(
        test_malformed_required_message_info_is_incompatible
    )
    test_missing_tool_name_and_completed_fields_are_incompatible = staticmethod(
        test_missing_tool_name_and_completed_fields_are_incompatible
    )
    test_lowercase_cursor_header_marks_history_partial = staticmethod(
        test_lowercase_cursor_header_marks_history_partial
    )
    test_malformed_tool_base_or_state_is_incompatible = staticmethod(
        test_malformed_tool_base_or_state_is_incompatible
    )
    test_part_base_identifiers_must_match_message = staticmethod(
        test_part_base_identifiers_must_match_message
    )
    test_known_opaque_part_is_not_misclassified_as_unknown = staticmethod(
        test_known_opaque_part_is_not_misclassified_as_unknown
    )
    test_malformed_nested_known_fields_are_incompatible = staticmethod(
        test_malformed_nested_known_fields_are_incompatible
    )
