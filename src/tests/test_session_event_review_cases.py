from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request

import pytest

from harness.session.events import TransportAttemptEvent
from harness.session.manager import MigrationSessionManager

from .session_event_test_support import FakeResponse, no_sleep, request_identity


def test_failed_nudge_does_not_repost_accepted_parent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given an accepted command whose required nudge POST fails.
    events: list[TransportAttemptEvent] = []
    requests: list[tuple[str, str]] = []
    state = {"posts": 0}

    def respond(
        request: urllib.request.Request,
        timeout: float | None = None,
    ) -> FakeResponse:
        del timeout
        identity = request_identity(request)
        requests.append(identity)
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(request.full_url).query)
        if identity[0] == "POST":
            state["posts"] += 1
            if state["posts"] == 1:
                return FakeResponse(
                    json.dumps(
                        {
                            "info": {"finish": "stop"},
                            "parts": [{"type": "text", "text": "accepted"}],
                        }
                    )
                )
            raise ConnectionResetError("nudge reset")
        if identity == ("GET", "/session/status"):
            return FakeResponse(json.dumps({"ses-1": {"type": "idle"}}))
        if query.get("limit") == ["20"]:
            return FakeResponse(json.dumps([{"todos": [{"status": "in_progress"}]}]))
        return FakeResponse(
            json.dumps([{"parts": [{"type": "text", "text": "accepted"}]}])
        )

    monkeypatch.setattr(urllib.request, "urlopen", respond)
    monkeypatch.setattr(time, "sleep", no_sleep)
    manager = MigrationSessionManager(
        auto_detect_agent=False,
        max_todo_nudges=1,
        todo_stabilize_wait_s=0,
        transport_observer=events.append,
    )

    # When the child nudge fails while public retries remain available.
    result = json.loads(manager.send_command("ses-1", "do work", retries=1))

    # Then the accepted parent command is not posted again.
    posts = [request for request in requests if request[0] == "POST"]
    assert result == {
        "ok": False,
        "error": "POST /session/ses-1/message failed: nudge reset",
    }
    assert posts == [("POST", "/session/ses-1/message")] * 2
    assert [event.phase for event in events] == [
        "started",
        "started",
        "error",
        "exhausted",
        "error",
        "exhausted",
    ]
    assert [str(event.invocation_id) for event in events] == [
        "transport-000001",
        "transport-000001:nudge-01",
        "transport-000001:nudge-01",
        "transport-000001:nudge-01",
        "transport-000001",
        "transport-000001",
    ]
    assert events[-2].retry_decision == "no_repost"
    assert events[-1].retry_decision == "no_repost"
    assert manager._transport_lifecycle._active == {}


def test_compaction_response_preserves_configured_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given every parent POST returns an accepted compaction response.
    events: list[TransportAttemptEvent] = []
    requests: list[tuple[str, str]] = []

    def respond(
        request: urllib.request.Request,
        timeout: float | None = None,
    ) -> FakeResponse:
        del timeout
        identity = request_identity(request)
        requests.append(identity)
        if identity[0] == "POST":
            return FakeResponse(
                json.dumps(
                    {
                        "info": {
                            "mode": "compaction",
                            "agent": "compaction",
                            "summary": True,
                        },
                        "parts": [{"type": "step-start"}],
                    }
                )
            )
        return FakeResponse("[]")

    monkeypatch.setattr(urllib.request, "urlopen", respond)
    monkeypatch.setattr(time, "sleep", no_sleep)
    manager = MigrationSessionManager(
        auto_detect_agent=False,
        transport_observer=events.append,
    )

    # When the public command uses its original retries=2 policy.
    result = json.loads(manager.send_command("ses-1", "do work", retries=2))

    # Then the accepted semantic failure still receives all three attempts.
    posts = [request for request in requests if request[0] == "POST"]
    assert result == {"ok": False, "error": "Compaction response is incomplete"}
    assert posts == [("POST", "/session/ses-1/message")] * 3
    assert [event.phase for event in events] == [
        "started",
        "error",
        "started",
        "error",
        "started",
        "error",
        "exhausted",
    ]
    assert [event.attempt for event in events] == [1, 1, 2, 2, 3, 3, 3]
    assert manager._transport_lifecycle._active == {}
