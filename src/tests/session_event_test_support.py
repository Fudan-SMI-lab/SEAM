from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from types import TracebackType
from typing import Literal

import pytest

from harness.session.events import TransportAttemptEvent
from harness.session.manager import MigrationSessionManager, SessionTransportError


@dataclass(frozen=True, slots=True)
class PlainObserverError(Exception):
    message: str

    def __str__(self) -> str:
        return self.message


class FakeResponse:
    _payload: bytes
    status: int

    def __init__(self, payload: str, status: int = 200) -> None:
        self._payload = payload.encode()
        self.status = status

    def read(self, size: int = -1) -> bytes:
        return self._payload if size < 0 else self._payload[:size]

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        return False


def request_identity(request: urllib.request.Request) -> tuple[str, str]:
    return request.get_method(), urllib.parse.urlsplit(request.full_url).path


def no_sleep(_seconds: float) -> None:
    return None


def successful_response(
    request: urllib.request.Request,
    timeout: float | None = None,
) -> FakeResponse:
    del timeout
    identity = request_identity(request)
    routes = {
        ("GET", "/session/ses-1/message"): json.dumps(
            [{"todos": [{"status": "completed"}]}]
        ),
        ("POST", "/session/ses-1/message"): json.dumps(
            {
                "info": {"finish": "stop"},
                "parts": [{"type": "text", "text": "complete"}],
            }
        ),
        ("GET", "/session/status"): json.dumps({"ses-1": {"type": "idle"}}),
    }
    payload = routes.get(identity)
    if payload is None:
        raise AssertionError(identity)
    return FakeResponse(payload)


def assert_todo_nudge_lifecycle(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given one command whose incomplete TODO state requires one real nudge POST.
    events: list[TransportAttemptEvent] = []
    requests: list[tuple[str, str]] = []
    state = {"posts": 0, "todo_reads": 0}

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
            text = "intermediate" if state["posts"] == 1 else "final"
            return FakeResponse(
                json.dumps(
                    {
                        "info": {"finish": "stop"},
                        "parts": [{"type": "text", "text": text}],
                    }
                )
            )
        if identity == ("GET", "/session/status"):
            return FakeResponse(json.dumps({"ses-1": {"type": "idle"}}))
        if query.get("limit") == ["20"]:
            state["todo_reads"] += 1
            status = "in_progress" if state["todo_reads"] <= 2 else "completed"
            return FakeResponse(json.dumps([{"todos": [{"status": status}]}]))
        text = (
            "old"
            if state["posts"] == 0
            else ("intermediate" if state["posts"] == 1 else "final")
        )
        return FakeResponse(json.dumps([{"parts": [{"type": "text", "text": text}]}]))

    monkeypatch.setattr(urllib.request, "urlopen", respond)
    monkeypatch.setattr(time, "sleep", no_sleep)
    manager = MigrationSessionManager(
        auto_detect_agent=False,
        max_todo_nudges=1,
        todo_stabilize_wait_s=0,
        transport_observer=events.append,
    )

    # When the public command converges after the nudge.
    result = manager.send_command("ses-1", "do work", timeout=10, retries=0)

    # Then both physical POSTs have their own truthful terminal event pair.
    posts = [request for request in requests if request[0] == "POST"]
    assert result == "final"
    assert posts == [("POST", "/session/ses-1/message")] * 2
    assert [event.phase for event in events] == [
        "started",
        "started",
        "completed",
        "completed",
    ]
    assert [str(event.invocation_id) for event in events] == [
        "transport-000001",
        "transport-000001:nudge-01",
        "transport-000001:nudge-01",
        "transport-000001",
    ]
    assert [event.attempt for event in events] == [1, 1, 1, 1]
    assert [event.max_attempts for event in events] == [1, 1, 1, 1]
    assert manager._transport_lifecycle._active == {}


def assert_empty_response_timeout_terminalizes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given one accepted empty response followed by a running session timeout.
    events: list[TransportAttemptEvent] = []
    ticks = iter((0.0, 0.0, 2.0))

    def respond(
        request: urllib.request.Request,
        timeout: float | None = None,
    ) -> FakeResponse:
        del timeout
        identity = request_identity(request)
        if identity == ("POST", "/session/ses-empty/message"):
            return FakeResponse(json.dumps({"info": {"finish": "stop"}, "parts": []}))
        if identity == ("GET", "/session/status"):
            return FakeResponse(json.dumps({"ses-empty": {"type": "running"}}))
        return FakeResponse("[]")

    monkeypatch.setattr(urllib.request, "urlopen", respond)
    monkeypatch.setattr(time, "time", lambda: next(ticks, 2.0))
    monkeypatch.setattr(time, "sleep", no_sleep)
    manager = MigrationSessionManager(
        auto_detect_agent=False,
        transport_observer=events.append,
    )

    # When empty-response recovery reaches the accepted-request timeout.
    with pytest.raises(TimeoutError, match="Session still running"):
        manager._send_message_raw("ses-empty", "do work", timeout=1)

    # Then timeout/exhaustion pair the start and lifecycle accounting is empty.
    assert [event.phase for event in events] == ["started", "timeout", "exhausted"]
    assert manager._transport_lifecycle._active == {}


def assert_failed_initial_prompt_terminalizes(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given session creation succeeds but its initial /message POST fails.
    events: list[TransportAttemptEvent] = []

    def respond(
        request: urllib.request.Request,
        timeout: float | None = None,
    ) -> FakeResponse:
        del timeout
        identity = request_identity(request)
        if identity == ("POST", "/session"):
            return FakeResponse(json.dumps({"id": "ses-initial"}))
        if identity == ("POST", "/session/ses-initial/message"):
            raise ConnectionResetError("initial prompt reset")
        return FakeResponse("[]")

    monkeypatch.setattr(urllib.request, "urlopen", respond)
    manager = MigrationSessionManager(
        auto_detect_agent=False,
        transport_observer=events.append,
    )

    # When create_session sends its direct initial prompt.
    with pytest.raises(SessionTransportError, match="initial prompt reset"):
        manager.create_session(role="worker", initial_prompt="SECRET INITIAL PROMPT")

    # Then the failed physical POST is errored/exhausted without retaining state.
    assert [event.phase for event in events] == ["started", "error", "exhausted"]
    assert "SECRET INITIAL PROMPT" not in repr(events)
    assert manager._transport_lifecycle._active == {}


def assert_plain_observer_exception_isolated(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Given a custom ordinary Exception raised only by the completed observer call.
    seen = 0

    def observer(_event: TransportAttemptEvent) -> None:
        nonlocal seen
        seen += 1
        if seen == 2:
            raise PlainObserverError("SECRET OBSERVER FAILURE")

    monkeypatch.setattr(urllib.request, "urlopen", successful_response)
    manager = MigrationSessionManager(
        auto_detect_agent=False,
        transport_observer=observer,
    )

    # When the request completes successfully.
    result = manager.send_command("ses-1", "do work", retries=0)

    # Then the ordinary observer failure is isolated and active state is removed.
    assert result == "complete"
    assert seen == 2
    assert "SECRET OBSERVER FAILURE" not in caplog.text
    assert manager._transport_lifecycle._active == {}


def assert_control_signal_propagates(
    monkeypatch: pytest.MonkeyPatch,
    signal: BaseException,
) -> None:
    # Given an observer raises a BaseException control signal on completed.
    seen = 0

    def observer(_event: TransportAttemptEvent) -> None:
        nonlocal seen
        seen += 1
        if seen == 2:
            raise signal

    monkeypatch.setattr(urllib.request, "urlopen", successful_response)
    manager = MigrationSessionManager(
        auto_detect_agent=False,
        transport_observer=observer,
    )

    # When the command reaches its completed observer event.
    with pytest.raises((KeyboardInterrupt, SystemExit)) as raised:
        manager.send_command("ses-1", "do work", retries=0)

    # Then control flow is not swallowed or converted into a command result.
    assert type(raised.value) is type(signal)
    assert manager._transport_lifecycle._active == {}
