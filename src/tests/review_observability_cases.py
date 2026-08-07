from __future__ import annotations

import logging
import socket
import time
import urllib.request
from pathlib import Path

import pytest

from core.runtime_observability import RuntimeObservability
from core.runtime_observability_models import TimeoutScope
from harness.session.events import (
    RetryDecision,
    TransportAttemptsExhausted,
    TransportAttemptTimedOut,
    TransportEventReason,
    TransportInvocationId,
)
from harness.session.events import TransportObserver
from harness.session.manager import MigrationSessionManager
from tests.e2e.e2e_observer import TelemetryObserver, TelemetryObserverConfig

from .session_event_test_support import FakeResponse, no_sleep, request_identity


def _request_timeout(attempt: int, *, final: bool) -> TransportAttemptTimedOut:
    return TransportAttemptTimedOut(
        session_id="reviewer-session",
        method="POST",
        path="/session/reviewer-session/message",
        invocation_id=TransportInvocationId("transport-000001"),
        attempt=attempt,
        max_attempts=3,
        timeout_s=987654.0,
        elapsed_s=float(attempt),
        retry_decision=(
            RetryDecision.STOP if final else RetryDecision.RETRY_SAME_SESSION
        ),
        reason=TransportEventReason.REQUEST_TIMEOUT,
        exhausted=final,
    )


def _exhausted(
    decision: RetryDecision,
    reason: TransportEventReason,
) -> TransportAttemptsExhausted:
    return TransportAttemptsExhausted(
        session_id="reviewer-session",
        method="POST",
        path="/session/reviewer-session/message",
        invocation_id=TransportInvocationId("transport-000001"),
        attempt=3 if decision is RetryDecision.STOP else 1,
        max_attempts=3 if decision is RetryDecision.STOP else 1,
        timeout_s=987654.0,
        elapsed_s=3.0,
        retry_decision=decision,
        reason=reason,
        exhausted=True,
    )


def _manager_factory(observer: TransportObserver) -> MigrationSessionManager:
    return MigrationSessionManager(
        auto_detect_agent=False,
        transport_observer=observer,
    )


def test_request_timeout_attempts_agree_across_console_telemetry_and_artifact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Given the active observer boundary and a real request that times out three times.
    posts: list[tuple[str, str]] = []

    def respond(
        request: urllib.request.Request,
        timeout: float | None = None,
    ) -> FakeResponse:
        del timeout
        identity = request_identity(request)
        if identity == ("POST", "/session"):
            return FakeResponse('{"id": "reviewer-session"}')
        if identity == ("GET", "/session/reviewer-session/message"):
            return FakeResponse("[]")
        if identity == ("POST", "/session/reviewer-session/message"):
            posts.append(identity)
            raise socket.timeout("timed out")
        raise AssertionError(identity)

    monkeypatch.setattr(urllib.request, "urlopen", respond)
    monkeypatch.setattr(time, "sleep", no_sleep)
    caplog.set_level(logging.WARNING, logger="core.runtime_observability")
    runtime = TelemetryObserver.create_observed_session(
        _manager_factory,
        TelemetryObserverConfig(tmp_path, "run-9", None),
    )
    runtime.observer.set_active_phase("review_result")
    session_id = runtime.observer.get_or_create("reviewer")

    # When the public SessionManager boundary exhausts retries=2.
    response = runtime.observer.send_command(
        session_id,
        "secret prompt",
        timeout=987654,
        retries=2,
    )
    paths = runtime.observer.save_metrics()

    # Then three physical POSTs and every concise channel agree on exhaustion.
    assert posts == [("POST", "/session/reviewer-session/message")] * 3
    assert "timed out" in response
    assert runtime.transport_observer.is_bound is True
    telemetry = Path(paths["telemetry_json"]).read_text(encoding="utf-8")
    artifact = Path(paths["phase_observability_json"]).read_text(encoding="utf-8")
    for output in (telemetry, artifact):
        assert '"timeout_count": 3' in output
        assert '"exhaustion_count": 1' in output
        assert '"attempt": 3' in output
        assert '"configured_timeout_seconds": 987654.0' in output
        assert '"retry_decision": "stop"' in output
        assert '"reason": "retries_exhausted"' in output
        assert '"run_id": "run-9"' in output
        assert '"phase_execution_id": "run-9:phase:review_result"' in output
        assert '"framework_invocation_id": "framework-000001"' in output
        assert '"transport_invocation_id": "transport-000001"' in output
        assert '"transport_attempt_id": "transport-000001:attempt-3"' in output
        assert "secret prompt" not in output
    assert "attempt=3/3" in caplog.text
    assert "exhausted=true" in caplog.text


def test_post_acceptance_timeout_reports_no_repost_policy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Given one real accepted POST whose idle convergence remains running.
    posts: list[tuple[str, str]] = []
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
        if identity == ("POST", "/session"):
            return FakeResponse('{"id": "reviewer-session"}')
        if identity == ("GET", "/session/reviewer-session/message"):
            return FakeResponse("[]")
        if identity == ("POST", "/session/reviewer-session/message"):
            posts.append(identity)
            return FakeResponse(
                '{"info":{"finish":"stop"},"parts":[{"type":"text","text":"accepted"}]}'
            )
        if identity == ("GET", "/session/status"):
            return FakeResponse('{"reviewer-session":{"type":"running"}}')
        raise AssertionError(identity)

    monkeypatch.setattr(urllib.request, "urlopen", respond)
    monkeypatch.setattr(time, "time", advance_time)
    monkeypatch.setattr(time, "sleep", no_sleep)
    runtime = TelemetryObserver.create_observed_session(
        _manager_factory,
        TelemetryObserverConfig(tmp_path, "run-9", None),
    )
    runtime.observer.set_active_phase("review_result")
    session_id = runtime.observer.get_or_create("reviewer")

    # When retries=2 reaches post-acceptance idle timeout.
    response = runtime.observer.send_command(session_id, "review", timeout=1, retries=2)
    paths = runtime.observer.save_metrics()

    # Then one POST remains attempt 1/3 and no repost is observable in both channels.
    assert '"ok": false' in response
    assert "Session still running or has incomplete todos" in response
    assert posts == [("POST", "/session/reviewer-session/message")]
    for key in ("telemetry_json", "phase_observability_json"):
        output = Path(paths[key]).read_text(encoding="utf-8")
        assert '"timeout_count": 1' in output
        assert '"exhaustion_count": 1' in output
        assert '"attempt": 1' in output
        assert '"max_attempts": 3' in output
        assert '"retry_decision": "no_repost"' in output
        assert '"reason": "post_acceptance_timeout"' in output


def test_duplicate_and_out_of_order_timeout_events_are_dropped(tmp_path: Path) -> None:
    # Given an accumulator and an exhaustion delivered before its timeout.
    observability = RuntimeObservability(tmp_path)
    scope = TimeoutScope("run-9", "reviewer", "review_result")
    exhausted = _exhausted(RetryDecision.STOP, TransportEventReason.RETRIES_EXHAUSTED)

    # When the stale exhaustion and a duplicate timeout are delivered.
    assert observability.add_transport(scope, exhausted) is None
    timeout = _request_timeout(3, final=True)
    assert observability.add_transport(scope, timeout) is not None
    assert observability.add_transport(scope, timeout) is None
    assert observability.add_transport(scope, exhausted) is not None

    # Then accepted records are counted once and malformed order is explicit.
    assert observability.aggregate() == {
        "review_count": 0,
        "timeout_count": 1,
        "exhaustion_count": 1,
        "dropped_event_count": 2,
        "review_duration_seconds": 0,
        "timeout_elapsed_seconds": 3.0,
    }
