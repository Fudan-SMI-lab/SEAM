from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from core.run_outcome import ReviewOutcome, ReviewRound, ReviewVerdict
from core.runtime_observability_models import (
    CommandCorrelation,
    ImprovementStatus,
    ReviewCompletion,
    ReviewScope,
)
from harness.run import FinalizationHooks, finalize_run
from tests.e2e.e2e_observer import TelemetryObserver

from .run_finalizer_test_support import FinalizerScenario, finalization_request
from .test_agent_io_logger import FakeSessionManager


def test_finalizer_persists_observability_records_not_only_paths(
    tmp_path: Path,
) -> None:
    # Given one typed review snapshot shared by both concise telemetry artifacts.
    observer = TelemetryObserver(FakeSessionManager(), tmp_path)
    observer.set_metadata("run_id", "run-summary")
    _ = observer.record_review_completion(
        ReviewCompletion(
            correlation=CommandCorrelation(
                "run-summary", "session-review", "command-1"
            ),
            scope=ReviewScope("phase_5_validation", 1, "reviewer", "review_result"),
            review_round=ReviewRound(
                1,
                3,
                ReviewVerdict.ACCEPT,
                ReviewOutcome.ACCEPTED,
            ),
            duration_seconds=2.5,
            improvement_status=ImprovementStatus.NOT_REQUIRED,
        )
    )
    snapshot = observer.observability_summary
    paths = observer.save_metrics()
    telemetry = json.loads(Path(paths["telemetry_json"]).read_text(encoding="utf-8"))
    artifact = json.loads(
        Path(paths["phase_observability_json"]).read_text(encoding="utf-8")
    )
    request = finalization_request(
        tmp_path,
        FinalizerScenario(hooks=FinalizationHooks.empty()),
    )

    # When the confirmed Task 5 request carries the typed snapshot.
    result = finalize_run(replace(request, observability=snapshot))
    persisted = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))

    # Then summary JSON directly owns the aggregate rather than only a path.
    assert result.summary.review_timeout_observability == snapshot
    assert persisted["review_timeout_observability"]["review_count"] == 1
    assert persisted["review_timeout_observability"]["reviews"][0]["command_id"] == (
        "command-1"
    )
    assert persisted["review_timeout_observability"]["timeouts"] == []
    assert telemetry["metadata"]["review_timeout_observability"] == artifact
    assert artifact == persisted["review_timeout_observability"]


@pytest.mark.parametrize("agent_io_enabled", [False, True])
def test_concise_telemetry_never_contains_raw_or_hostile_text(
    tmp_path: Path,
    agent_io_enabled: bool,
) -> None:
    # Given hostile prompt/response content with either Agent I/O policy.
    logger = None
    if agent_io_enabled:
        from core.agent_io_logger import AgentIOLogger

        logger = AgentIOLogger(tmp_path, "run-leak", enabled=True, redact=False)
    observer = TelemetryObserver(FakeSessionManager(), tmp_path, agent_io_logger=logger)
    session_id = observer.get_or_create("reviewer")

    # When the command and a hostile event are serialized to concise telemetry.
    _ = observer.send_command(
        session_id,
        "hostile prompt OPENAI_API_KEY=sk-abcdefghijklmnopqrstuvwxyz",
    )
    observer.record_event(
        "selector_response_received",
        response_preview="hostile response Bearer raw-secret-token",
    )
    telemetry = Path(observer.save_metrics()["telemetry_json"]).read_text(
        encoding="utf-8"
    )

    # Then no raw prompt, response, preview, or credential is retained.
    for forbidden in (
        "hostile prompt",
        "full response body",
        "sk-abcdefghijklmnopqrstuvwxyz",
        "hostile response",
        "raw-secret-token",
        "command_preview",
        "response_preview",
    ):
        assert forbidden not in telemetry
