from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request

import pytest

from harness.session.events import TransportAttemptEvent
from harness.session.manager import MigrationSessionManager

from core.session_registry import ContextExhaustedError
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


def test_compaction_recovery_does_not_consume_configured_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # rationale: a compaction response is an intermediate state; the manager
    # must NOT burn the caller's retries=2 budget re-POSTing the same session.
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
        if identity == ("GET", "/session/status"):
            return FakeResponse(json.dumps({"ses-1": {"type": "idle"}}))
        return FakeResponse("[]")

    monkeypatch.setattr(urllib.request, "urlopen", respond)
    monkeypatch.setattr(time, "sleep", no_sleep)
    manager = MigrationSessionManager(
        auto_detect_agent=False,
        transport_observer=events.append,
    )

    # When the refetch cannot supply a usable final message, the single
    # recovery attempt exhausts the budget and terminates structurally.
    with pytest.raises(ContextExhaustedError) as exc_info:
        manager.send_command("ses-1", "do work", retries=2)

    # rationale: the retry budget is untouched — exactly one physical POST.
    posts = [request for request in requests if request[0] == "POST"]
    assert posts == [("POST", "/session/ses-1/message")] * 1
    assert exc_info.value.session_id == "ses-1"
    assert exc_info.value.compaction_count == 1
    # rationale: one started parent attempt paired with error/exhausted; the
    # bounded wait + refetch produce no further transport events.
    assert [event.phase for event in events] == [
        "started",
        "error",
        "exhausted",
    ]
    assert [event.attempt for event in events] == [1, 1, 1]
    assert manager._transport_lifecycle._active == {}


@pytest.mark.integration
def test_reproduction_compaction_recovers_without_reposting_same_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression lock for Bug #16 (Phase 5 context exhaustion).

    Bug: `send_command` treated a compaction intermediate state as a plain
    failure and re-POSTed to the SAME session up to 3x; every retry received
    the same compaction payload, so the command never completed and the
    caller saw ``{"ok": false, "error": "Compaction response is
    incomplete"}`` even though the session was full.

    Task 6 contract: a compaction response is an intermediate state. The
    manager performs ONE bounded wait + refetch recovery per command
    (``max_recoveries_per_command=1``), never re-POSTs to the compacting
    session, and terminates structurally with ``ContextExhaustedError`` when
    the recovery budget is exhausted. This test flips the old RED lock to the
    fixed contract.
    """
    # rationale: the session must never be re-POSTed while compacting.
    events: list[TransportAttemptEvent] = []
    requests: list[tuple[str, str]] = []

    session_id = "ses-bug16-full"

    def respond(
        request: urllib.request.Request,
        timeout: float | None = None,
    ) -> FakeResponse:
        del timeout
        identity = request_identity(request)
        requests.append(identity)
        if identity[0] == "POST":
            # Canonical shape: info.mode="compaction" -> _is_compaction_payload True.
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
        if identity == ("GET", "/session/status"):
            return FakeResponse(json.dumps({session_id: {"type": "idle"}}))
        return FakeResponse("[]")

    monkeypatch.setattr(urllib.request, "urlopen", respond)
    monkeypatch.setattr(time, "sleep", no_sleep)
    manager = MigrationSessionManager(
        auto_detect_agent=False,
        transport_observer=events.append,
    )

    # When the refetch cannot converge, the single recovery exhausts the budget.
    with pytest.raises(ContextExhaustedError) as exc_info:
        manager.send_command(session_id, "do long phase 5 work", retries=2)

    # Then the session is POSTed exactly once — never re-POSTed — and recovery
    # terminates structurally naming the affected session.
    posts = [request for request in requests if request[0] == "POST"]
    assert len(posts) == 1
    assert posts == [("POST", f"/session/{session_id}/message")] * 1
    assert {post[1] for post in posts} == {f"/session/{session_id}/message"}
    assert exc_info.value.session_id == session_id
    assert exc_info.value.compaction_count == 1
    assert [event.phase for event in events] == [
        "started",
        "error",
        "exhausted",
    ]
    assert manager._transport_lifecycle._active == {}


def test_env_override_increases_compaction_recovery_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # rationale: the recovery budget must be configurable via the
    # SEAM_MAX_RECOVERIES_PER_COMMAND env override; a budget of 2 means the
    # manager waits + refetches twice before terminating structurally, and the
    # surfaced ContextExhaustedError reports compaction_count=2.
    events: list[TransportAttemptEvent] = []
    requests: list[tuple[str, str]] = []
    state = {"posted": False, "refetches": 0}

    def respond(
        request: urllib.request.Request,
        timeout: float | None = None,
    ) -> FakeResponse:
        del timeout
        identity = request_identity(request)
        requests.append(identity)
        if identity[0] == "POST":
            state["posted"] = True
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
        if identity == ("GET", "/session/status"):
            return FakeResponse(json.dumps({"ses-1": {"type": "idle"}}))
        if identity == ("GET", "/session/ses-1/message"):
            if state["posted"]:
                state["refetches"] += 1
            return FakeResponse("[]")
        return FakeResponse("[]")

    monkeypatch.setattr(urllib.request, "urlopen", respond)
    monkeypatch.setattr(time, "sleep", no_sleep)
    monkeypatch.setenv("SEAM_MAX_RECOVERIES_PER_COMMAND", "2")
    manager = MigrationSessionManager(
        auto_detect_agent=False,
        transport_observer=events.append,
    )

    # When the refetch never converges across the doubled recovery budget.
    with pytest.raises(ContextExhaustedError) as exc_info:
        manager.send_command("ses-1", "do work", retries=2)

    # rationale: the budget drives two wait+refetch cycles, not re-POSTs.
    posts = [request for request in requests if request[0] == "POST"]
    assert posts == [("POST", "/session/ses-1/message")] * 1
    assert state["refetches"] == 2
    assert exc_info.value.session_id == "ses-1"
    assert exc_info.value.compaction_count == 2
    assert [event.phase for event in events] == [
        "started",
        "error",
        "exhausted",
    ]
    assert manager._transport_lifecycle._active == {}
