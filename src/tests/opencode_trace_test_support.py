from __future__ import annotations

import json
import urllib.request
from collections.abc import Callable
from typing import final

from harness.session.opencode_contract import JsonObject
from harness.session.opencode_trace_client import OpenCodeTraceClient
from tests.opencode_contract_test_helpers import fixture, json_object, object_member
from tests.session_event_test_support import FakeResponse


@final
class FakeTraceHttp:
    def __init__(self, routes: dict[tuple[str, str], JsonObject]) -> None:
        self._routes = routes
        self.calls: list[tuple[str, str, JsonObject | None]] = []

    def __call__(
        self,
        method: str,
        path: str,
        query: JsonObject | None = None,
    ) -> JsonObject:
        self.calls.append((method, path, query))
        return self._routes[(method, path)]

    def set_route(self, method: str, path: str, response: JsonObject) -> None:
        self._routes[(method, path)] = response


def http_response(endpoint: JsonObject) -> JsonObject:
    status = endpoint.get("status")
    assert isinstance(status, int) and not isinstance(status, bool)
    body = endpoint.get("body")
    if status == 200:
        return {"ok": True, "status": status, "data": body}
    return {
        "ok": False,
        "status": status,
        "details": json.dumps(body, ensure_ascii=False),
    }


def fixture_routes(
    name: str,
    request_session_id: str = "ses_root",
) -> dict[tuple[str, str], JsonObject]:
    capture = json_object(fixture(name))
    return {
        ("GET", "/global/health"): http_response(object_member(capture, "health")),
        ("GET", "/doc"): http_response(object_member(capture, "doc")),
        ("GET", f"/session/{request_session_id}/message"): http_response(
            object_member(capture, "messages")
        ),
        ("GET", f"/session/{request_session_id}/children"): http_response(
            object_member(capture, "children")
        ),
    }


def client_for_fixture(
    name: str,
    request_session_id: str = "ses_root",
) -> tuple[OpenCodeTraceClient, FakeTraceHttp]:
    http = FakeTraceHttp(fixture_routes(name, request_session_id))
    return OpenCodeTraceClient(http), http


def session_create_response(
    session_id: str,
) -> Callable[[urllib.request.Request, float | None], FakeResponse]:
    def respond(
        request: urllib.request.Request,
        timeout: float | None = None,
    ) -> FakeResponse:
        del request, timeout
        return FakeResponse(json.dumps({"id": session_id}))

    return respond
