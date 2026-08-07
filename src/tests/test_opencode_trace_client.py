from __future__ import annotations

import json
import urllib.parse
import urllib.request

import pytest

from core.session_registry import SessionRegistry
from harness.session.manager import MigrationSessionManager
from harness.session.opencode_contract import (
    CapabilityState,
    Completeness,
    Compatibility,
    JsonObject,
)
from harness.session.opencode_trace_client import (
    OpenCodeTraceClient,
    TraceCapabilityState,
)
from tests.opencode_contract_test_helpers import fixture, json_object, object_member
from tests.opencode_trace_review_cases import TestOpenCodeTraceReviewRegressions
from tests.opencode_trace_test_support import (
    FakeTraceHttp,
    client_for_fixture as _client_for_fixture,
    http_response as _http_response,
    session_create_response,
)
from tests.session_event_test_support import FakeResponse


def test_health_and_document_capabilities_are_typed_for_v1_18_5() -> None:
    # Given the pinned Task 3 capture.
    client, _http = _client_for_fixture("trace_complete.json")

    # When health and document features are retrieved independently.
    health = client.get_health()
    document = client.get_document()

    # Then both endpoint states and the pinned version remain typed.
    assert health.capability is CapabilityState.SUPPORTED
    assert health.server_version == "1.18.5"
    assert document.capability is CapabilityState.SUPPORTED


def test_full_messages_never_supply_a_positive_limit_and_round_trip() -> None:
    # Given the pinned capture containing unknown message and part fields.
    client, http = _client_for_fixture("trace_complete.json")
    expected = object_member(json_object(fixture("trace_complete.json")), "messages")

    # When the complete graph is retrieved.
    result = client.retrieve_session_graph("ses_root")

    # Then V1 history is full, typed, and retains every raw WithParts value.
    message_calls = [call for call in http.calls if call[1].endswith("/message")]
    assert message_calls == [("GET", "/session/ses_root/message", None)]
    assert result.contract.messages.is_full_history is True
    assert result.contract.messages.messages
    exported = result.contract.to_json_value()
    assert isinstance(exported, dict)
    exported_messages = exported.get("messages")
    assert isinstance(exported_messages, dict)
    assert exported_messages.get("body") == expected["body"]
    assert result.contract.history_authority == "v1"


def test_direct_children_are_typed_without_session_listing_fallback() -> None:
    # Given a supported immediate-children route.
    client, http = _client_for_fixture("trace_complete.json")

    # When the graph is retrieved.
    result = client.retrieve_session_graph("ses_root")

    # Then direct children are authoritative and no listing request occurs.
    assert result.contract.children.capability is CapabilityState.SUPPORTED
    assert result.contract.children.sessions
    assert result.fallback_children is None
    assert not any(call[1] == "/session" for call in http.calls)


@pytest.mark.parametrize("status", [404, 405])
def test_unsupported_children_remain_unsupported_with_filtered_fallback(
    status: int,
) -> None:
    # Given direct children are unsupported but the V1 session list is available.
    capture = json_object(fixture("trace_complete.json"))
    children = object_member(capture, "children")
    child_body = children.get("body")
    assert isinstance(child_body, list)
    unrelated: JsonObject = {
        "id": "ses_unrelated",
        "parentID": "ses_other",
    }
    routes: dict[tuple[str, str], JsonObject] = {
        ("GET", "/global/health"): _http_response(object_member(capture, "health")),
        ("GET", "/doc"): _http_response(object_member(capture, "doc")),
        ("GET", "/session/ses_root/message"): _http_response(
            object_member(capture, "messages")
        ),
        ("GET", "/session/ses_root/children"): {
            "ok": False,
            "status": status,
            "details": "route unsupported",
        },
        ("GET", "/session"): {
            "ok": True,
            "status": 200,
            "data": [*child_body, unrelated],
        },
    }
    http = FakeTraceHttp(routes)

    # When the graph uses the listing fallback.
    result = OpenCodeTraceClient(http).retrieve_session_graph("ses_root")

    # Then direct capability remains unsupported and fallback children are distinct.
    assert result.state is TraceCapabilityState.PARTIAL
    assert result.contract.children.capability is CapabilityState.UNSUPPORTED
    assert result.contract.children.completeness is Completeness.UNSUPPORTED
    assert result.fallback_children is not None
    assert result.fallback_capture is not None
    assert result.fallback_capture.body == [*child_body, unrelated]
    assert {child.parent_id for child in result.fallback_children.sessions} == {
        "ses_root"
    }
    assert {child.session_id for child in result.fallback_children.sessions} == {
        "ses_child_1",
        "ses_child_2",
    }


def test_non_pinned_server_uses_observed_capabilities() -> None:
    # Given a structurally valid 1.18.10 capture that differs from the pinned version.
    client, _http = _client_for_fixture("trace_non_pinned_compatible.json")

    # When capability is detected from observed endpoint evidence.
    result = client.retrieve_session_graph("ses_root")

    # Then the non-pinned version is metadata, not a verdict: state is COMPATIBLE,
    # the contract is COMPATIBLE/COMPLETE, the observed version is retained, and
    # no retrieval or annotation errors were emitted.
    assert result.state is TraceCapabilityState.COMPATIBLE
    assert result.contract.compatibility is Compatibility.COMPATIBLE
    assert result.contract.completeness is Completeness.COMPLETE
    assert result.contract.server_version == "1.18.10"
    assert result.errors == ()


def test_missing_server_version_remains_unknown_and_errors() -> None:
    # Given a structurally complete capture whose health body has no version.
    capture = json_object(fixture("trace_non_pinned_compatible.json"))
    health_body = object_member(capture, "health").get("body")
    assert isinstance(health_body, dict)
    assert "version" in health_body
    del health_body["version"]
    routes: dict[tuple[str, str], JsonObject] = {
        ("GET", "/global/health"): _http_response(object_member(capture, "health")),
        ("GET", "/doc"): _http_response(object_member(capture, "doc")),
        ("GET", "/session/ses_root/message"): _http_response(
            object_member(capture, "messages")
        ),
        ("GET", "/session/ses_root/children"): _http_response(
            object_member(capture, "children")
        ),
    }
    http = FakeTraceHttp(routes)
    client = OpenCodeTraceClient(http)

    # When health and graph are retrieved from the version-less capture.
    health = client.get_health()
    result = client.retrieve_session_graph("ses_root")

    # Then health is UNKNOWN, the contract is incompatible/non-complete, and the
    # graph is an explicit ERROR annotated with malformed_contract (no endpoint
    # or identity error explains the failure, so the contract itself is blamed).
    assert health.capability is CapabilityState.UNKNOWN
    assert health.server_version == ""
    assert result.state is TraceCapabilityState.ERROR
    assert result.contract.compatibility is Compatibility.INCOMPATIBLE
    assert result.contract.completeness is Completeness.INCOMPATIBLE
    assert "malformed_contract" in result.errors


def test_malformed_part_is_error_and_never_complete() -> None:
    # Given malformed Task 3 WithParts data.
    client, _http = _client_for_fixture("trace_malformed_with_parts.json")

    # When graph retrieval parses the boundary.
    result = client.retrieve_session_graph("ses_root")

    # Then malformed data remains an explicit error and incompatible contract.
    assert result.state is TraceCapabilityState.ERROR
    assert result.contract.compatibility is Compatibility.INCOMPATIBLE
    assert result.contract.completeness is Completeness.INCOMPATIBLE
    assert "malformed_contract" in result.errors


def test_http_500_is_error_and_never_fabricates_history() -> None:
    # Given a server error from the full-message endpoint.
    client, http = _client_for_fixture("trace_complete.json")
    http.set_route(
        "GET",
        "/session/ses_root/message",
        {
            "ok": False,
            "status": 500,
            "details": "server exploded",
        },
    )

    # When graph retrieval runs once per endpoint.
    result = client.retrieve_session_graph("ses_root")

    # Then the HTTP error is explicit and no message retry or limit occurs.
    assert result.state is TraceCapabilityState.ERROR
    assert result.contract.messages.messages == ()
    assert "messages:http_500" in result.errors
    assert [call for call in http.calls if call[1].endswith("/message")] == [
        ("GET", "/session/ses_root/message", None)
    ]


def test_all_seam_created_roots_are_deduplicated_trace_seeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given selector, persistent, correction, retry, and ephemeral root creation.
    created = iter(
        (
            "ses_selector",
            "ses_main",
            "ses_correction",
            "ses_retry",
            "ses_ephemeral",
        )
    )

    def respond(
        request: urllib.request.Request,
        timeout: float | None = None,
    ) -> FakeResponse:
        del timeout
        assert request.get_method() == "POST"
        assert urllib.parse.urlsplit(request.full_url).path == "/session"
        return FakeResponse(json.dumps({"id": next(created)}))

    monkeypatch.setattr(urllib.request, "urlopen", respond)
    manager = MigrationSessionManager(auto_detect_agent=False)

    # When each root lifecycle is created and the persistent role is reused.
    _ = manager.get_or_create("workflow_selector", lifecycle="ephemeral")
    main = manager.get_or_create("main_engineer", lifecycle="persistent")
    _ = manager.create_session("main_engineer_correction", lifecycle="ephemeral")
    _ = manager.create_session("main_engineer_phase_5_retry", lifecycle="ephemeral")
    _ = manager.create_session("scratch", lifecycle="ephemeral")
    assert manager.get_or_create("main_engineer", lifecycle="persistent") == main

    # Then every created root appears exactly once with role and scope metadata.
    seeds = manager.trace_seeds
    assert {seed.session_id for seed in seeds} == {
        "ses_selector",
        "ses_main",
        "ses_correction",
        "ses_retry",
        "ses_ephemeral",
    }
    assert all(seed.logical_role is not None for seed in seeds)
    assert all(seed.scope is not None for seed in seeds)


def test_registry_enriches_seed_role_and_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given an agent ID whose configured logical role differs from its key.
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        session_create_response("ses_adapter"),
    )
    manager = MigrationSessionManager(auto_detect_agent=False)
    registry = SessionRegistry(
        {"phase_adapter": {"role": "code_adapter", "lifecycle": "persistent"}},
        manager,
    )

    # When the registry resolves the persistent role.
    _ = registry.resolve("phase_adapter")

    # Then the seed carries both the logical role and registry scope.
    assert manager.trace_seeds[0].logical_role == "code_adapter"
    assert manager.trace_seeds[0].scope == "agent:phase_adapter"


def test_missing_role_metadata_is_partial_not_fabricated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from harness.session.trace_seeds import TraceSeedMetadataState

    # Given a SEAM-created root without logical role metadata.
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        session_create_response("ses_missing"),
    )
    manager = MigrationSessionManager(auto_detect_agent=False)

    # When the root is created.
    _ = manager.create_session(role="", lifecycle="ephemeral")

    # Then missing metadata is explicit and cannot masquerade as complete.
    seed = manager.trace_seeds[0]
    assert seed.logical_role is None
    assert seed.metadata_state is TraceSeedMetadataState.PARTIAL


__all__ = ["TestOpenCodeTraceReviewRegressions"]
