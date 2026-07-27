from __future__ import annotations

import json
from typing import final

import pytest

import harness.session.opencode_contract as contract_module

from harness.session.opencode_contract import (
    CapabilityState,
    Completeness,
    Compatibility,
    ToolPart,
    parse_trace_contract,
)
from tests.opencode_contract_test_helpers import fixture, snapshot_trace


def test_public_facade_preserves_original_authored_surface() -> None:
    # Given
    expected = {
        "PINNED_VERSION",
        "MAX_CAPTURE_CHARS",
        "JsonValue",
        "JsonObject",
        "Compatibility",
        "CapabilityState",
        "Completeness",
        "EndpointFeatures",
        "TaskLineage",
        "PendingToolState",
        "RunningToolState",
        "CompletedToolState",
        "ErrorToolState",
        "UnknownToolState",
        "ToolState",
        "TextPart",
        "ReasoningPart",
        "ToolPart",
        "UnknownPart",
        "Part",
        "MessageWithParts",
        "MessagesResult",
        "ChildSession",
        "ChildrenResult",
        "TraceContract",
        "parse_trace_contract",
    }

    # When / Then
    assert set(contract_module.__all__) == expected


def test_complete_capture_parses_version_features_and_round_trips_losslessly() -> None:
    # Given
    raw = fixture("trace_complete.json")

    # When
    contract = parse_trace_contract(raw)

    # Then
    assert contract.server_version == "1.18.5"
    assert contract.compatibility is Compatibility.COMPATIBLE
    assert contract.features.health is CapabilityState.SUPPORTED
    assert contract.features.messages is CapabilityState.SUPPORTED
    assert contract.features.children is CapabilityState.SUPPORTED
    assert contract.features.v2_history is CapabilityState.SUPPORTED
    assert contract.history_authority == "v1"
    assert contract.to_json_value() == json.loads(raw)


def test_task_lineage_and_truncated_output_reference_are_preserved() -> None:
    # Given / When
    contract = parse_trace_contract(fixture("trace_complete.json"))

    # Then
    tools = [
        part
        for message in contract.messages.messages
        for part in message.parts
        if isinstance(part, ToolPart)
    ]
    task = next(part for part in tools if part.tool == "task")
    assert task.lineage is not None
    assert task.lineage.parent_session_id == "ses_root"
    assert task.lineage.child_session_id == "ses_child_1"
    assert task.lineage.raw["futureMetadata"] == {"nested": [1, 2, 3]}
    assert task.output_paths == ("D:/managed/tool-output/tool_abc",)
    assert contract.overflow_paths == ("D:/managed/tool-output/tool_abc",)
    assert contract.completeness is Completeness.PARTIAL


def test_unpaginated_v1_messages_are_full_and_v2_is_optional_enrichment() -> None:
    # Given / When
    complete = parse_trace_contract(fixture("trace_complete.json"))
    no_v2 = parse_trace_contract(fixture("trace_children_unsupported.json"))

    # Then
    assert complete.messages.is_full_history is True
    assert complete.v2_history is not None
    assert no_v2.features.v2_history is CapabilityState.UNKNOWN
    assert no_v2.v2_history is None
    assert no_v2.history_authority == "v1"


def test_round_trip_value_is_a_defensive_copy() -> None:
    # Given
    contract = parse_trace_contract(fixture("trace_complete.json"))

    # When
    exported = contract.to_json_value()
    assert isinstance(exported, dict)
    exported["mutated"] = True

    # Then
    assert contract.to_json_value() != exported


def test_pinned_identifier_capture_without_underscores_is_compatible() -> None:
    # Given
    raw = (
        fixture("trace_complete.json")
        .replace("msg_user_1", "msguser1")
        .replace("msg_assistant_1", "msgassistant1")
        .replace("prt_text", "prttext")
    )

    # When
    contract = parse_trace_contract(raw)

    # Then
    assert contract.compatibility is Compatibility.COMPATIBLE


def test_pinned_identifiers_accept_schema_legal_suffixes() -> None:
    # Given
    raw = (
        fixture("trace_complete.json")
        .replace("msg_user_1", "msg:user/🙂")
        .replace("msg_assistant_1", "msg assistant/🙂")
        .replace("prt_text", "prt?text/🙂")
        .replace("ses_root", "ses root/🙂")
        .replace("ses_child_1", "ses:child/🙂")
        .replace("ses_child_2", "ses child two/🙂")
    )

    # When
    contract = parse_trace_contract(raw)

    # Then
    assert contract.compatibility is Compatibility.COMPATIBLE


def test_pinned_identifier_bare_prefixes_are_compatible() -> None:
    # Given
    raw = (
        snapshot_trace()
        .replace("ses_root", "ses")
        .replace("msg_user", "msg")
        .replace("msg_assistant", "msgassistant")
        .replace("prt_snapshot", "prt")
    )

    # When
    contract = parse_trace_contract(raw)

    # Then
    assert contract.compatibility is Compatibility.COMPATIBLE


@pytest.mark.parametrize("identifier", ["ses_root", "msg_user", "prt_snapshot"])
def test_empty_identifier_is_incompatible(identifier: str) -> None:
    # Given
    raw = snapshot_trace().replace(identifier, "")

    # When
    contract = parse_trace_contract(raw)

    # Then
    assert contract.compatibility is Compatibility.INCOMPATIBLE


@final
class TestHappyRoundTrip:
    test_public_facade_preserves_original_authored_surface = staticmethod(
        test_public_facade_preserves_original_authored_surface
    )
    test_complete_capture_parses_version_features_and_round_trips_losslessly = (
        staticmethod(
            test_complete_capture_parses_version_features_and_round_trips_losslessly
        )
    )
    test_task_lineage_and_truncated_output_reference_are_preserved = staticmethod(
        test_task_lineage_and_truncated_output_reference_are_preserved
    )
    test_unpaginated_v1_messages_are_full_and_v2_is_optional_enrichment = staticmethod(
        test_unpaginated_v1_messages_are_full_and_v2_is_optional_enrichment
    )
    test_round_trip_value_is_a_defensive_copy = staticmethod(
        test_round_trip_value_is_a_defensive_copy
    )
    test_pinned_identifier_capture_without_underscores_is_compatible = staticmethod(
        test_pinned_identifier_capture_without_underscores_is_compatible
    )
    test_pinned_identifiers_accept_schema_legal_suffixes = staticmethod(
        test_pinned_identifiers_accept_schema_legal_suffixes
    )
    test_pinned_identifier_bare_prefixes_are_compatible = staticmethod(
        test_pinned_identifier_bare_prefixes_are_compatible
    )
    test_empty_identifier_is_incompatible = staticmethod(
        test_empty_identifier_is_incompatible
    )
