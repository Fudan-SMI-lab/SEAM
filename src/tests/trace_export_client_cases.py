from __future__ import annotations

import json
import urllib.parse
from typing import final

from harness.session.opencode_contract import JsonObject
from harness.session.opencode_trace_client import OpenCodeTraceClient
from tests.opencode_trace_test_support import FakeTraceHttp


def test_typed_client_retrieves_percent_encoded_root_session_info() -> None:
    session_id = "ses root/🙂"
    encoded = urllib.parse.quote(session_id, safe="")
    body: JsonObject = {"id": session_id, "future": {"kept": True}}
    raw_body = json.dumps(body, ensure_ascii=False)
    http = FakeTraceHttp(
        {
            ("GET", f"/session/{encoded}"): {
                "ok": True,
                "status": 200,
                "data": body,
                "headers": {"X-Trace": "raw"},
                "raw_body": raw_body,
            }
        }
    )

    capture = OpenCodeTraceClient(http).get_session_info(session_id)

    assert http.calls == [("GET", f"/session/{encoded}", None)]
    assert capture.body == body
    assert capture.raw_body == raw_body


@final
class TestTraceExportClientCases:
    test_typed_client_retrieves_percent_encoded_root_session_info = staticmethod(
        test_typed_client_retrieves_percent_encoded_root_session_info
    )


__all__ = ["TestTraceExportClientCases"]
