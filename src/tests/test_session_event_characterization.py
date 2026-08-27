from __future__ import annotations

import json
import socket
import time
import urllib.request
from typing import NoReturn

import pytest

from harness.session.events import TransportAttemptEvent
from harness.session.manager import MigrationSessionManager, SessionTransportError

from .session_event_test_support import FakeResponse, no_sleep, request_identity


def test_characterization_request_timeout_is_session_transport_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a request/socket timeout at the existing urllib boundary.
    def time_out(
        _request: urllib.request.Request,
        timeout: float | None = None,
    ) -> NoReturn:
        del timeout
        raise socket.timeout("timed out")

    monkeypatch.setattr(urllib.request, "urlopen", time_out)
    manager = MigrationSessionManager(auto_detect_agent=False)

    # When one raw message transport is attempted.
    with pytest.raises(SessionTransportError, match="timed out"):
        manager._send_message_raw("ses-1", "do work", timeout=5)


def test_request_timeout_never_reposts_to_the_same_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given every physical request times out and retry backoff is deterministic.
    requests: list[tuple[str, str]] = []
    events: list[TransportAttemptEvent] = []

    def time_out(
        request: urllib.request.Request,
        timeout: float | None = None,
    ) -> NoReturn:
        del timeout
        requests.append(request_identity(request))
        raise socket.timeout("timed out")

    monkeypatch.setattr(urllib.request, "urlopen", time_out)
    monkeypatch.setattr(time, "sleep", no_sleep)
    manager = MigrationSessionManager(
        auto_detect_agent=False,
        transport_observer=events.append,
    )

    # When the public command surface allows retries but the POST times out.
    result = json.loads(manager.send_command("ses-1", "do work", timeout=5, retries=2))

    # Then the accepted state is ambiguous, so no same-session repost occurs.
    posts = [request for request in requests if request[0] == "POST"]
    assert result == {
        "ok": False,
        "error": "POST /session/ses-1/message failed: timed out",
    }
    assert posts == [("POST", "/session/ses-1/message")]
    assert [event.phase for event in events] == [
        "started",
        "timeout",
        "exhausted",
    ]
    assert [event.attempt for event in events] == [1, 1, 1]
    assert [event.retry_decision for event in events] == [
        "pending",
        "no_repost",
        "no_repost",
    ]
    assert [event.exhausted for event in events] == [
        False,
        True,
        True,
    ]


def test_characterization_post_acceptance_timeout_does_not_repost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given one accepted POST followed by a session that remains running.
    requests: list[tuple[str, str]] = []
    events: list[TransportAttemptEvent] = []
    clock = {"now": 0.0}

    def advance_time() -> float:
        clock["now"] += 1.0
        return clock["now"]

    def respond(
        request: urllib.request.Request,
        timeout: float | None = None,
    ) -> FakeResponse:
        del timeout
        identity = request_identity(request)
        requests.append(identity)
        routes = {
            ("GET", "/session/ses-1/message"): "[]",
            ("POST", "/session/ses-1/message"): json.dumps(
                {
                    "info": {"finish": "stop"},
                    "parts": [{"type": "text", "text": "accepted"}],
                }
            ),
            ("GET", "/session/status"): json.dumps({"ses-1": {"type": "running"}}),
        }
        payload = routes.get(identity)
        if payload is None:
            raise AssertionError(identity)
        return FakeResponse(payload)

    monkeypatch.setattr(urllib.request, "urlopen", respond)
    monkeypatch.setattr(time, "time", advance_time)
    monkeypatch.setattr(time, "sleep", no_sleep)
    manager = MigrationSessionManager(
        auto_detect_agent=False,
        transport_observer=events.append,
    )

    # When convergence times out after the server accepted the command.
    with pytest.raises(TimeoutError, match="Session still running"):
        manager._send_message_raw("ses-1", "do work", timeout=1)

    # Then the accepted command is never reposted.
    posts = [request for request in requests if request[0] == "POST"]
    assert posts == [("POST", "/session/ses-1/message")]
    assert [event.phase for event in events] == [
        "started",
        "timeout",
        "exhausted",
    ]
    assert [event.retry_decision for event in events] == [
        "pending",
        "no_repost",
        "no_repost",
    ]
    assert [event.reason for event in events] == [
        "attempt_started",
        "post_acceptance_timeout",
        "post_acceptance_timeout",
    ]
    assert [event.attempt for event in events] == [1, 1, 1]
    assert all(event.exhausted is (index > 0) for index, event in enumerate(events))
