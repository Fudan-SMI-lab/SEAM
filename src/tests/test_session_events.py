from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from typing import NoReturn

import pytest

from harness.session.events import TransportAttemptEvent
from harness.session.manager import MigrationSessionManager
from .session_event_test_support import (
    assert_control_signal_propagates,
    assert_empty_response_timeout_terminalizes,
    assert_failed_initial_prompt_terminalizes,
    assert_plain_observer_exception_isolated,
    assert_todo_nudge_lifecycle,
    successful_response,
)


@dataclass(frozen=True, slots=True)
class ObserverProbeError(RuntimeError):
    message: str

    def __str__(self) -> str:
        return self.message


def test_success_emits_started_then_completed_for_actual_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a successful public command and an optional lifecycle observer.
    events: list[TransportAttemptEvent] = []
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        successful_response,
    )
    manager = MigrationSessionManager(
        auto_detect_agent=False,
        transport_observer=events.append,
    )

    # When the command completes without a retry.
    result = manager.send_command("ses-1", "secret prompt", timeout=7, retries=2)

    # Then concise events describe the one configured transport attempt only.
    assert result == "complete"
    assert [event.phase for event in events] == ["started", "completed"]
    assert {event.invocation_id for event in events} == {"transport-000001"}
    assert [event.attempt for event in events] == [1, 1]
    assert [event.max_attempts for event in events] == [3, 3]
    assert [event.timeout_s for event in events] == [7.0, 7.0]
    assert all(event.session_id == "ses-1" for event in events)
    assert all(event.method == "POST" for event in events)
    assert all(event.path == "/session/ses-1/message" for event in events)
    assert events[0].elapsed_s == 0.0
    assert events[1].elapsed_s >= 0.0
    assert events[0].retry_decision == "pending"
    assert events[1].retry_decision == "complete"
    assert events[0].reason == "attempt_started"
    assert events[1].reason == "request_completed"
    assert not any(event.exhausted for event in events)
    assert not any(
        hasattr(event, "body") or hasattr(event, "command") for event in events
    )


def test_non_timeout_transport_failure_emits_error_then_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a connection failure that is not a request timeout.
    events: list[TransportAttemptEvent] = []

    def reset_connection(
        _request: urllib.request.Request,
        timeout: float | None = None,
    ) -> NoReturn:
        del timeout
        raise ConnectionResetError("connection reset")

    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        reset_connection,
    )
    manager = MigrationSessionManager(
        auto_detect_agent=False,
        transport_observer=events.append,
    )

    # When retries are disabled at the public command surface.
    result = json.loads(manager.send_command("ses-1", "do work", retries=0))

    # Then the failed attempt is visible without adding another POST.
    assert result == {
        "ok": False,
        "error": "POST /session/ses-1/message failed: connection reset",
    }
    assert [event.phase for event in events] == ["started", "error", "exhausted"]
    assert [event.reason for event in events] == [
        "attempt_started",
        "transport_error",
        "retries_exhausted",
    ]
    assert [event.retry_decision for event in events] == ["pending", "stop", "stop"]


def test_observer_runtime_error_does_not_change_command_behavior(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a successful command and a failing optional observer.
    observed = 0

    def fail_observer(_event: TransportAttemptEvent) -> None:
        nonlocal observed
        observed += 1
        raise ObserverProbeError("observer failed")

    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        successful_response,
    )
    manager = MigrationSessionManager(
        auto_detect_agent=False,
        transport_observer=fail_observer,
    )

    # When the public command is sent with retries disabled.
    result = manager.send_command("ses-1", "do work", retries=0)

    # Then observer failures are isolated and both lifecycle calls are attempted.
    assert result == "complete"
    assert observed == 2


def test_todo_nudge_emits_lifecycle_for_each_physical_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert_todo_nudge_lifecycle(monkeypatch)


def test_empty_accepted_response_timeout_terminalizes_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert_empty_response_timeout_terminalizes(monkeypatch)


def test_failed_create_session_initial_prompt_terminalizes_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert_failed_initial_prompt_terminalizes(monkeypatch)


def test_custom_exception_from_completed_observer_is_isolated(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    assert_plain_observer_exception_isolated(monkeypatch, caplog)


def test_keyboard_interrupt_from_observer_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert_control_signal_propagates(monkeypatch, KeyboardInterrupt())


def test_system_exit_from_observer_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert_control_signal_propagates(monkeypatch, SystemExit(7))
