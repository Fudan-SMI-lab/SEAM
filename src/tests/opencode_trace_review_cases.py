from __future__ import annotations

import json
import urllib.request
from typing import final

import pytest

from harness.session.manager import MigrationSessionManager
from harness.session.opencode_contract import (
    Completeness,
    Compatibility,
    JsonObject,
)
from harness.session.opencode_trace_client import (
    OpenCodeTraceClient,
    TraceCapabilityState,
)
from tests.opencode_contract_test_helpers import fixture, json_object, object_member
from tests.opencode_trace_test_support import (
    FakeTraceHttp,
    client_for_fixture,
    fixture_routes,
    http_response,
    session_create_response,
)
from tests.session_event_test_support import FakeResponse


@final
class HeaderResponse(FakeResponse):
    def __init__(self, payload: str, headers: dict[str, str]) -> None:
        super().__init__(payload)
        self.headers: dict[str, str] = headers


@final
class TestOpenCodeTraceReviewRegressions:
    def test_cursor_header_marks_message_history_partial(self) -> None:
        # Given an unbounded message response that still advertises a next cursor.
        client, http = client_for_fixture("trace_complete.json")
        capture = json_object(fixture("trace_complete.json"))
        response = http_response(object_member(capture, "messages"))
        response["headers"] = {"X-Next-Cursor": "next"}
        http.set_route("GET", "/session/ses_root/message", response)

        # When the requested graph is parsed by Task 3.
        result = client.retrieve_session_graph("ses_root")

        # Then the cursor prevents a false full-history claim.
        assert result.state is TraceCapabilityState.PARTIAL
        assert result.contract.messages.is_full_history is False
        assert result.contract.messages.completeness is Completeness.PARTIAL

    def test_foreign_message_root_is_error(self) -> None:
        # Given the requested path and returned message root disagree.
        client, _http = client_for_fixture(
            "trace_complete.json",
            request_session_id="ses_requested",
        )

        # When the graph is retrieved for the requested root.
        result = client.retrieve_session_graph("ses_requested")

        # Then valid foreign data cannot be accepted as the requested graph.
        assert result.state is TraceCapabilityState.ERROR
        assert "messages:session_mismatch" in result.errors

    @pytest.mark.parametrize("status", [401, 403, 429])
    @pytest.mark.parametrize("endpoint", ["doc", "children"])
    def test_non_success_capability_status_is_error(
        self,
        endpoint: str,
        status: int,
    ) -> None:
        # Given an endpoint fails for a reason other than route unsupported.
        client, http = client_for_fixture("trace_complete.json")
        path = "/doc" if endpoint == "doc" else "/session/ses_root/children"
        http.set_route(
            "GET",
            path,
            {"ok": False, "status": status, "details": "request rejected"},
        )

        # When graph capability is classified.
        result = client.retrieve_session_graph("ses_root")

        # Then authorization and throttling failures cannot look partial.
        assert result.state is TraceCapabilityState.ERROR
        assert f"{endpoint}:http_{status}" in result.errors

    def test_session_id_is_encoded_as_one_path_segment(self) -> None:
        # Given a schema-legal session identifier with URL delimiters.
        session_id = "ses root/child?x=1#frag"
        encoded = "ses%20root%2Fchild%3Fx%3D1%23frag"
        response: JsonObject = {"ok": True, "status": 200, "data": []}
        http = FakeTraceHttp({("GET", f"/session/{encoded}/message"): response})

        # When full messages are retrieved.
        _ = OpenCodeTraceClient(http).get_full_messages(session_id)

        # Then delimiters remain inside one encoded path segment.
        assert http.calls == [("GET", f"/session/{encoded}/message", None)]

    @pytest.mark.parametrize("status", [404, 405])
    def test_unsupported_fallback_listing_keeps_graph_partial(
        self, status: int
    ) -> None:
        # Given both direct children and session-list fallback are unsupported.
        routes = fixture_routes("trace_children_unsupported.json")
        routes[("GET", "/session")] = {
            "ok": False,
            "status": status,
            "details": "route unsupported",
        }
        client = OpenCodeTraceClient(FakeTraceHttp(routes))

        # When graph retrieval attempts the fallback once.
        result = client.retrieve_session_graph("ses_root")

        # Then unsupported remains partial and never becomes an error or empty tree.
        assert result.state is TraceCapabilityState.PARTIAL
        assert result.fallback_children is None
        assert result.errors == ()

    def test_manager_trace_http_preserves_response_headers(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Given the server returns pagination evidence in a response header.
        response = HeaderResponse("[]", {"X-Next-Cursor": "next"})

        def respond(
            request: urllib.request.Request,
            timeout: float | None = None,
        ) -> HeaderResponse:
            del request, timeout
            return response

        monkeypatch.setattr(urllib.request, "urlopen", respond)
        manager = MigrationSessionManager(auto_detect_agent=False)

        # When the typed trace transport performs a GET.
        captured = manager.trace_client.get_full_messages("ses_root")

        # Then the real response headers cross the boundary unchanged.
        assert captured.headers == {"X-Next-Cursor": "next"}

    def test_raw_duplicate_keys_remain_incompatible(self) -> None:
        # Given ordinary transport data collapsed a duplicate key but raw JSON retained it.
        client, http = client_for_fixture("trace_complete.json")
        capture = json_object(fixture("trace_complete.json"))
        messages = object_member(capture, "messages")
        body = messages.get("body")
        raw_body = json.dumps(body, ensure_ascii=False).replace(
            '"id": "msg_user_1"',
            '"id": "msg_user_1", "id": "msg_duplicate"',
            1,
        )
        response = http_response(messages)
        response["raw_body"] = raw_body
        http.set_route("GET", "/session/ses_root/message", response)

        # When Task 3 receives the original body text.
        result = client.retrieve_session_graph("ses_root")

        # Then duplicate-key input fails closed instead of using collapsed data.
        assert result.state is TraceCapabilityState.ERROR
        assert result.contract.compatibility is Compatibility.INCOMPATIBLE

    def test_paginated_fallback_listing_is_retained_but_not_complete(self) -> None:
        # Given direct children are unsupported and the session listing has a cursor.
        capture = json_object(fixture("trace_complete.json"))
        child_body = object_member(capture, "children").get("body")
        assert isinstance(child_body, list)
        routes = fixture_routes("trace_children_unsupported.json")
        routes[("GET", "/session")] = {
            "ok": True,
            "status": 200,
            "data": child_body,
            "headers": {"X-Next-Cursor": "next"},
        }

        # When the fallback evidence is inspected.
        result = OpenCodeTraceClient(FakeTraceHttp(routes)).retrieve_session_graph(
            "ses_root"
        )

        # Then raw listing evidence is retained without fabricating complete children.
        assert result.state is TraceCapabilityState.PARTIAL
        assert result.fallback_children is None
        assert result.fallback_capture is not None
        assert result.fallback_capture.body == child_body

    def test_attach_session_records_a_managed_trace_seed(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Given an existing remote session becomes managed by SEAM.
        monkeypatch.setattr(
            urllib.request,
            "urlopen",
            session_create_response("ses_attached"),
        )
        manager = MigrationSessionManager(auto_detect_agent=False)

        # When the manager successfully attaches it.
        attached = manager.attach_session("ses_attached", role="imported")

        # Then it is a deduplicated trace seed with explicit managed metadata.
        assert attached is True
        assert [seed.session_id for seed in manager.trace_seeds] == ["ses_attached"]
        assert manager.trace_seeds[0].logical_role == "imported"

    def test_plain_text_children_404_remains_unsupported(self) -> None:
        # Given production-shaped HTTPError text accompanies unsupported children.
        capture = json_object(fixture("trace_complete.json"))
        child_body = object_member(capture, "children").get("body")
        assert isinstance(child_body, list)
        routes = fixture_routes("trace_complete.json")
        routes[("GET", "/session/ses_root/children")] = {
            "ok": False,
            "status": 404,
            "details": "Not Found",
            "raw_body": "Not Found",
        }
        routes[("GET", "/session")] = {
            "ok": True,
            "status": 200,
            "data": child_body,
        }

        # When graph retrieval parses the production error envelope.
        result = OpenCodeTraceClient(FakeTraceHttp(routes)).retrieve_session_graph(
            "ses_root"
        )

        # Then plain text remains data and cannot corrupt the Task 3 bundle.
        assert result.state is TraceCapabilityState.PARTIAL
        assert result.contract.children.completeness is Completeness.UNSUPPORTED
        assert result.fallback_children is not None

    def test_duplicate_key_in_fallback_listing_fails_closed(self) -> None:
        # Given parsed fallback data hides a duplicate parentID retained in raw JSON.
        capture = json_object(fixture("trace_complete.json"))
        child_body = object_member(capture, "children").get("body")
        assert isinstance(child_body, list)
        raw_body = json.dumps(child_body, ensure_ascii=False).replace(
            '"parentID": "ses_root"',
            '"parentID": "ses_root", "parentID": "ses_foreign"',
            1,
        )
        routes = fixture_routes("trace_children_unsupported.json")
        routes[("GET", "/session")] = {
            "ok": True,
            "status": 200,
            "data": child_body,
            "raw_body": raw_body,
        }

        # When fallback evidence is validated before filtering.
        result = OpenCodeTraceClient(FakeTraceHttp(routes)).retrieve_session_graph(
            "ses_root"
        )

        # Then collapsed data is not accepted over malformed raw evidence.
        assert result.state is TraceCapabilityState.ERROR
        assert result.fallback_children is None
        assert "session_list:malformed_capture" in result.errors

    def test_foreign_child_parent_is_error_when_messages_are_empty(self) -> None:
        # Given valid direct children belong to a foreign root and messages are empty.
        capture = json_object(fixture("trace_complete.json"))
        message_response = http_response(object_member(capture, "messages"))
        message_response["data"] = []
        child_response = http_response(object_member(capture, "children"))
        child_body = child_response.get("data")
        assert isinstance(child_body, list)
        for child in child_body:
            assert isinstance(child, dict)
            child["parentID"] = "ses_foreign"
        routes = fixture_routes("trace_complete.json")
        routes[("GET", "/session/ses_root/message")] = message_response
        routes[("GET", "/session/ses_root/children")] = child_response

        # When the requested root is bound after Task 3 parsing.
        result = OpenCodeTraceClient(FakeTraceHttp(routes)).retrieve_session_graph(
            "ses_root"
        )

        # Then the exact foreign-child exploit is rejected explicitly.
        assert result.state is TraceCapabilityState.ERROR
        assert "children:parent_mismatch" in result.errors
