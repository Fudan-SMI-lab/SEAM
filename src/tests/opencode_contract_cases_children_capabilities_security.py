from __future__ import annotations

import json
from pathlib import Path
from typing import final

import pytest

from harness.session.opencode_contract import (
    CapabilityState,
    Completeness,
    Compatibility,
    TextPart,
    parse_trace_contract,
)
from tests.opencode_contract_test_helpers import (
    fixture,
    json_object,
    object_list_member,
    object_member,
    set_object_list_member,
    snapshot_trace,
)


def test_children_are_immediate_and_unknown_metadata_is_lossless() -> None:
    # Given / When
    contract = parse_trace_contract(fixture("trace_complete.json"))

    # Then
    assert contract.children.capability is CapabilityState.SUPPORTED
    assert contract.children.completeness is Completeness.COMPLETE
    assert [child.parent_id for child in contract.children.sessions] == [
        "ses_root",
        "ses_root",
    ]
    assert contract.children.sessions[0].raw["metadata"] == {
        "taskKind": "explore",
        "unknownChildMetadata": {"priority": "future"},
    }


@pytest.mark.parametrize("status", [404, 405])
def test_unsupported_children_can_never_look_complete(status: int) -> None:
    # Given
    raw = fixture("trace_children_unsupported.json").replace(
        '"status": 404', f'"status": {status}'
    )

    # When
    contract = parse_trace_contract(raw)

    # Then
    assert contract.children.capability is CapabilityState.UNSUPPORTED
    assert contract.children.completeness is Completeness.UNSUPPORTED
    assert contract.children.sessions == ()
    assert contract.completeness is Completeness.PARTIAL


def test_non_pinned_server_version_is_compatible_when_shapes_are_valid() -> None:
    # Given / When
    contract = parse_trace_contract(fixture("trace_non_pinned_compatible.json"))

    # Then
    assert contract.server_version == "1.18.10"
    assert contract.features.health is CapabilityState.SUPPORTED
    assert contract.compatibility is Compatibility.COMPATIBLE
    assert contract.completeness is Completeness.COMPLETE
    assert contract.features.v2_history is CapabilityState.SUPPORTED


def test_missing_server_version_is_incompatible_and_health_unknown() -> None:
    # Given
    raw = fixture("trace_complete.json").replace(
        '"healthy": true, "version": "1.18.5"',
        '"healthy": true',
        1,
    )

    # When
    contract = parse_trace_contract(raw)

    # Then
    assert contract.server_version == ""
    assert contract.features.health is CapabilityState.UNKNOWN
    assert contract.compatibility is Compatibility.INCOMPATIBLE
    assert contract.completeness is Completeness.INCOMPATIBLE
    assert contract.to_json_value() is not None


def test_hostile_text_is_inert_and_unknown_fields_remain_opaque(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    monkeypatch.chdir(tmp_path)

    # When
    contract = parse_trace_contract(fixture("trace_complete.json"))

    # Then
    text = contract.messages.messages[0].parts[0]
    assert isinstance(text, TextPart)
    assert "Path('PWNED.txt').write_text('owned')" in text.text
    assert text.raw["futureTextField"] == ["lossless", 3]
    assert not (tmp_path / "PWNED.txt").exists()


def test_doc_discovery_has_explicit_supported_and_unsupported_states() -> None:
    # Given / When
    supported = parse_trace_contract(fixture("trace_complete.json"))
    unsupported = parse_trace_contract(fixture("trace_doc_unsupported.json"))

    # Then
    assert supported.features.document is CapabilityState.SUPPORTED
    assert unsupported.features.document is CapabilityState.UNSUPPORTED
    assert unsupported.completeness is Completeness.PARTIAL


def test_child_without_identity_is_incompatible() -> None:
    # Given
    raw = fixture("trace_complete.json").replace(
        '"id": "ses_child_1",', '"futureID": "ses_child_1",', 1
    )

    # When
    contract = parse_trace_contract(raw)

    # Then
    assert contract.children.completeness is Completeness.INCOMPATIBLE
    assert contract.compatibility is Compatibility.INCOMPATIBLE


@pytest.mark.parametrize(
    "raw",
    [
        fixture("trace_complete.json").replace(
            '"healthy": true', '"healthy": false', 1
        ),
        fixture("trace_complete.json").replace('"status": 200,', '"status": 503,', 1),
    ],
)
def test_unhealthy_or_failed_health_probe_is_incompatible(raw: str) -> None:
    # Given / When
    contract = parse_trace_contract(raw)

    # Then
    assert contract.compatibility is Compatibility.INCOMPATIBLE
    assert contract.completeness is Completeness.INCOMPATIBLE


def test_child_session_requires_pinned_info_fields() -> None:
    # Given
    raw = fixture("trace_complete.json").replace(
        '"slug": "child-one",', '"futureSlug": "child-one",', 1
    )

    # When
    contract = parse_trace_contract(raw)

    # Then
    assert contract.children.completeness is Completeness.INCOMPATIBLE


def test_v2_float_status_is_not_treated_as_success() -> None:
    # Given
    raw = fixture("trace_complete.json").replace(
        '"v2History": {\n    "status": 200,',
        '"v2History": {\n    "status": 200.0,',
        1,
    )

    # When
    contract = parse_trace_contract(raw)

    # Then
    assert contract.features.v2_history is CapabilityState.UNKNOWN
    assert contract.v2_history is None


@pytest.mark.parametrize("mutation", ["duplicate", "foreign", "self-parent"])
def test_contradictory_child_identity_is_incompatible(mutation: str) -> None:
    # Given
    payload = json_object(fixture("trace_complete.json"))
    children = object_list_member(object_member(payload, "children"), "body")
    if mutation == "duplicate":
        children[1]["id"] = children[0]["id"]
    elif mutation == "foreign":
        children[1]["parentID"] = "ses_other"
    else:
        children[1]["parentID"] = children[1]["id"]

    # When
    contract = parse_trace_contract(json.dumps(payload))

    # Then
    assert contract.compatibility is Compatibility.INCOMPATIBLE
    assert contract.completeness is Completeness.INCOMPATIBLE


@pytest.mark.parametrize("mutation", ["mixed-session", "duplicate-message"])
def test_contradictory_message_identity_is_incompatible(mutation: str) -> None:
    # Given
    payload = json_object(snapshot_trace())
    messages_response = object_member(payload, "messages")
    messages = object_list_member(messages_response, "body")
    messages.append(json_object(json.dumps(messages[0])))
    set_object_list_member(messages_response, "body", messages)
    if mutation == "mixed-session":
        info = object_member(messages[1], "info")
        info["id"] = "msg_other"
        info["sessionID"] = "ses_other"
        for index, part in enumerate(object_list_member(messages[1], "parts")):
            part["id"] = f"prt_other_{index}"
            part["sessionID"] = "ses_other"
            part["messageID"] = "msg_other"

    # When
    contract = parse_trace_contract(json.dumps(payload))

    # Then
    assert contract.compatibility is Compatibility.INCOMPATIBLE
    assert contract.completeness is Completeness.INCOMPATIBLE


@pytest.mark.parametrize("mutation", ["malformed", "self", "cycle", "unresolved"])
def test_invalid_complete_message_parent_graph_is_incompatible(mutation: str) -> None:
    payload = json_object(snapshot_trace())
    messages_response = object_member(payload, "messages")
    messages = object_list_member(messages_response, "body")
    info = object_member(messages[-1], "info")
    if mutation == "cycle":
        messages.append(json_object(json.dumps(messages[-1])))
        second_info = object_member(messages[-1], "info")
        second_info["id"] = "msg_other"
        for index, part in enumerate(object_list_member(messages[-1], "parts")):
            part["id"] = f"prt_other_{index}"
            part["messageID"] = "msg_other"
        info["parentID"] = "msg_other"
        second_info["parentID"] = "msg_assistant"
        set_object_list_member(messages_response, "body", messages)
    elif mutation == "self":
        info["parentID"] = info["id"]
    elif mutation == "malformed":
        info["parentID"] = "parent"
    else:
        info["parentID"] = "msg_missing"

    contract = parse_trace_contract(json.dumps(payload))

    assert contract.compatibility is Compatibility.INCOMPATIBLE
    assert contract.completeness is Completeness.INCOMPATIBLE


def test_unresolved_message_parent_in_partial_history_remains_partial() -> None:
    payload = json_object(snapshot_trace())
    messages_response = object_member(payload, "messages")
    messages_response["query"] = {"limit": 1}
    info = object_member(object_list_member(messages_response, "body")[-1], "info")
    info["parentID"] = "msg_missing"

    contract = parse_trace_contract(json.dumps(payload))

    assert contract.compatibility is Compatibility.COMPATIBLE
    assert contract.completeness is Completeness.PARTIAL


@final
class TestChildrenCapabilitiesSecurity:
    test_children_are_immediate_and_unknown_metadata_is_lossless = staticmethod(
        test_children_are_immediate_and_unknown_metadata_is_lossless
    )
    test_unsupported_children_can_never_look_complete = staticmethod(
        test_unsupported_children_can_never_look_complete
    )
    test_non_pinned_server_version_is_compatible_when_shapes_are_valid = staticmethod(
        test_non_pinned_server_version_is_compatible_when_shapes_are_valid
    )
    test_missing_server_version_is_incompatible_and_health_unknown = staticmethod(
        test_missing_server_version_is_incompatible_and_health_unknown
    )
    test_hostile_text_is_inert_and_unknown_fields_remain_opaque = staticmethod(
        test_hostile_text_is_inert_and_unknown_fields_remain_opaque
    )
    test_doc_discovery_has_explicit_supported_and_unsupported_states = staticmethod(
        test_doc_discovery_has_explicit_supported_and_unsupported_states
    )
    test_child_without_identity_is_incompatible = staticmethod(
        test_child_without_identity_is_incompatible
    )
    test_unhealthy_or_failed_health_probe_is_incompatible = staticmethod(
        test_unhealthy_or_failed_health_probe_is_incompatible
    )
    test_child_session_requires_pinned_info_fields = staticmethod(
        test_child_session_requires_pinned_info_fields
    )
    test_v2_float_status_is_not_treated_as_success = staticmethod(
        test_v2_float_status_is_not_treated_as_success
    )
    test_contradictory_child_identity_is_incompatible = staticmethod(
        test_contradictory_child_identity_is_incompatible
    )
    test_contradictory_message_identity_is_incompatible = staticmethod(
        test_contradictory_message_identity_is_incompatible
    )


@final
class TestMessageParent:
    test_invalid_complete_message_parent_graph_is_incompatible = staticmethod(
        test_invalid_complete_message_parent_graph_is_incompatible
    )
    test_unresolved_message_parent_in_partial_history_remains_partial = staticmethod(
        test_unresolved_message_parent_in_partial_history_remains_partial
    )
