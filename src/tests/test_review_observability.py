from __future__ import annotations

import socket
import time
import urllib.request
from pathlib import Path
from typing import NoReturn

import pytest

from core.telemetry_bridge import TelemetryBridge
from core.run_outcome import ReviewOutcome, ReviewRound, ReviewVerdict, TerminalOutcome
from core.runtime_observability_models import (
    CommandCorrelation,
    ImprovementStatus,
    ReviewCompletion,
    ReviewScope,
)
from harness.run import FinalizationHooks, RunArtifactUpdate, finalize_run
from harness.session.events import TransportAttemptEvent
from harness.session.manager import MigrationSessionManager, SessionTransportError
from tests.e2e.e2e_observer import TelemetryObserver

from .run_finalizer_test_support import FinalizerScenario, finalization_request
from .review_observability_cases import (
    test_duplicate_and_out_of_order_timeout_events_are_dropped,
    test_post_acceptance_timeout_reports_no_repost_policy,
    test_request_timeout_attempts_agree_across_console_telemetry_and_artifact,
)
from .review_observability_edge_cases import (
    test_concise_artifact_does_not_duplicate_agent_payloads,
    test_missing_correlation_and_observer_failure_are_outcome_neutral,
    test_review_transition_publishes_round_two_with_improvement,
    test_unknown_prose_and_malformed_correlation_fail_closed,
)
from .review_observability_property_cases import (
    test_run_id_property_control_signal_propagates,
    test_run_id_property_exception_is_outcome_neutral_and_logged,
)
from .review_observability_v3_cases import (
    test_concise_telemetry_never_contains_raw_or_hostile_text,
    test_finalizer_persists_observability_records_not_only_paths,
)
from .session_event_test_support import FakeResponse, no_sleep, request_identity
from .test_agent_io_logger import FakeSessionManager

__all__ = (
    "test_duplicate_and_out_of_order_timeout_events_are_dropped",
    "test_concise_artifact_does_not_duplicate_agent_payloads",
    "test_post_acceptance_timeout_reports_no_repost_policy",
    "test_request_timeout_attempts_agree_across_console_telemetry_and_artifact",
    "test_missing_correlation_and_observer_failure_are_outcome_neutral",
    "test_review_transition_publishes_round_two_with_improvement",
    "test_unknown_prose_and_malformed_correlation_fail_closed",
    "test_run_id_property_control_signal_propagates",
    "test_run_id_property_exception_is_outcome_neutral_and_logged",
    "test_concise_telemetry_never_contains_raw_or_hostile_text",
    "test_finalizer_persists_observability_records_not_only_paths",
)


def test_characterization_timeout_stops_same_session_reposts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a request timeout with retries=2 configured.
    posts: list[tuple[str, str]] = []
    events: list[TransportAttemptEvent] = []

    def time_out(
        _request: urllib.request.Request,
        timeout: float | None = None,
    ) -> NoReturn:
        del timeout
        identity = request_identity(_request)
        if identity[0] == "POST":
            posts.append(identity)
        raise socket.timeout("timed out")

    monkeypatch.setattr(urllib.request, "urlopen", time_out)
    monkeypatch.setattr(time, "sleep", no_sleep)
    manager = MigrationSessionManager(
        auto_detect_agent=False,
        transport_observer=events.append,
    )

    # When the public command cannot determine whether OpenCode accepted the POST.
    response = manager.send_command("session-review", "review", timeout=17, retries=2)

    # Then it must preserve the error classification without duplicating the turn.
    assert posts == [("POST", "/session/session-review/message")]
    assert response == (
        '{"ok": false, "error": '
        '"POST /session/session-review/message failed: timed out"}'
    )
    assert [event.attempt for event in events if event.phase == "timeout"] == [1]


def test_characterization_initial_prompt_timeout_keeps_transport_error_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given session creation succeeds before its initial prompt POST times out.
    def respond(
        request: urllib.request.Request,
        timeout: float | None = None,
    ) -> FakeResponse:
        del timeout
        method, path = request_identity(request)
        if (method, path) == ("POST", "/session"):
            return FakeResponse('{"id": "session-review"}')
        if (method, path) == ("GET", "/session/session-review/message"):
            return FakeResponse("[]")
        raise socket.timeout("timed out")

    monkeypatch.setattr(urllib.request, "urlopen", respond)
    manager = MigrationSessionManager(auto_detect_agent=False)

    # When the public initial-prompt boundary sends the accepted session command.
    with pytest.raises(SessionTransportError) as raised:
        _ = manager.create_session("reviewer", initial_prompt="review")

    # Then the existing typed timeout remains visible to callers.
    assert raised.value.timed_out is True


def test_characterization_telemetry_fields_remain_stable(tmp_path: Path) -> None:
    # Given the existing telemetry bridge schema.
    bridge = TelemetryBridge(str(tmp_path))

    # When metrics are serialized.
    path = Path(bridge.save_metrics()["telemetry_json"])
    payload = path.read_text(encoding="utf-8")

    # Then Task 9 may add events but cannot replace the established channels.
    for field in ("metadata", "phases", "sessions", "commands", "events"):
        assert f'"{field}"' in payload
    for field in (
        "run_started_at",
        "generated_at",
        "elapsed_seconds",
        "session_count",
        "command_count",
    ):
        assert f'"{field}"' in payload


def test_characterization_summary_fields_remain_stable(tmp_path: Path) -> None:
    # Given the extracted V3 finalizer's established request.
    request = finalization_request(tmp_path, FinalizerScenario())

    # When the passing run is finalized.
    summary = finalize_run(request).summary

    # Then Task 9 must preserve all pre-existing summary aggregates.
    assert summary.session_count == 2
    assert summary.command_count == 3
    assert summary.total_duration_seconds == 4.5
    assert summary.overall_status == "PASS"
    assert summary.telemetry_paths == {}


def test_review_event_is_published_to_telemetry_and_phase_artifact(
    tmp_path: Path,
) -> None:
    # Given one confirmed logical review completion with correlation metadata.
    observer = TelemetryObserver(FakeSessionManager(), tmp_path)
    observer.set_active_phase("phase_5_validation")
    _ = observer.record_review_completion(
        ReviewCompletion(
            correlation=CommandCorrelation(
                run_id="run-9",
                session_id="session-review",
                command_id="review-command-2",
            ),
            scope=ReviewScope(
                phase_id="phase_5_validation",
                phase5_iteration=3,
                reviewer_agent="reviewer",
                sub_phase="review_result",
            ),
            review_round=ReviewRound(
                round_number=2,
                max_rounds=3,
                verdict=ReviewVerdict.ACCEPT,
                outcome=ReviewOutcome.ACCEPTED,
            ),
            duration_seconds=4.25,
            improvement_status=ImprovementStatus.NOT_REQUIRED,
        )
    )

    # When the established observer serializes its output channels.
    paths: dict[str, str] = {}

    def evidence(_outcome: TerminalOutcome) -> RunArtifactUpdate:
        paths.update(observer.save_metrics())
        return RunArtifactUpdate(telemetry_paths=tuple(paths.items()))

    finalization = finalize_run(
        finalization_request(
            tmp_path,
            FinalizerScenario(hooks=FinalizationHooks(evidence_replay=evidence)),
        )
    )

    # Then telemetry and the phase artifact expose one correlated aggregate.
    assert set(paths) == {"telemetry_json", "phase_observability_json"}
    telemetry_text = Path(paths["telemetry_json"]).read_text(encoding="utf-8")
    artifact_text = Path(paths["phase_observability_json"]).read_text(encoding="utf-8")
    for output in (telemetry_text, artifact_text):
        assert '"record_id": "run-9:phase_5_validation:review:2"' in output
        assert '"review_count": 1' in output
        assert '"duration_seconds": 4.25' in output
        assert '"outcome": "accepted"' in output
        assert '"run_id": "run-9"' in output
        assert '"phase_execution_id": "run-9:phase:phase_5_validation"' in output
        assert '"review_round_id": "run-9:phase_5_validation:review:2"' in output
        assert '"framework_invocation_id": "review-command-2"' in output
    assert (
        finalization.summary.telemetry_paths["phase_observability_json"]
        == paths["phase_observability_json"]
    )
