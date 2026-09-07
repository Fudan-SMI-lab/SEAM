from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from core.review_gate import ReviewGate
from core.agent_io_logger import AgentIOLogger
from core.review_observability import (
    ReviewCommandReceipt,
    ReviewCompletionObserver,
    ReviewTransition,
    publish_review_transition,
)
from core.run_outcome import ReviewVerdict
from core.runtime_observability_models import (
    CommandCorrelation,
    ImprovementStatus,
    ObservabilityContractError,
    ReviewCompletion,
)
from tests.e2e.e2e_observer import TelemetryObserver

from .test_agent_io_logger import FakeSessionManager


@dataclass(frozen=True, slots=True)
class PlainObserverFailure(Exception):
    detail: str

    def __str__(self) -> str:
        return self.detail


class FailingReviewObserver:
    @property
    def run_id(self) -> str:
        return "run-9"

    def record_review_completion(self, completion: ReviewCompletion) -> bool:
        raise PlainObserverFailure("observer failed")


@dataclass(frozen=True, slots=True)
class ControlFlowReviewObserver:
    signal: KeyboardInterrupt | SystemExit

    @property
    def run_id(self) -> str:
        return "run-9"

    def record_review_completion(self, completion: ReviewCompletion) -> bool:
        raise self.signal


def test_review_transition_publishes_round_two_with_improvement(
    tmp_path: Path,
) -> None:
    # Given one prior rejection followed by an accepted second judgment.
    gate = ReviewGate().record_judgment(ReviewVerdict.REJECT)
    gate = gate.record_judgment(ReviewVerdict.ACCEPT)
    observer = TelemetryObserver(FakeSessionManager(), tmp_path)
    observer.set_metadata("run_id", "run-9")
    transition = ReviewTransition(
        phase_id="phase_5_validation",
        phase5_iteration=4,
        previous_round_count=1,
        gate=gate,
        receipt=ReviewCommandReceipt(
            session_id="reviewer-session",
            command_id="reviewer-session:review_result:round-2:attempt-1",
            reviewer_agent="reviewer",
            sub_phase="review_result",
            duration_seconds=8.75,
        ),
        improvement_status=ImprovementStatus.APPLIED,
    )

    # When the authoritative merge publishes the transition twice.
    assert publish_review_transition(observer, transition) is True
    assert publish_review_transition(observer, transition) is False
    paths = observer.save_metrics()

    # Then stale reuse is deduplicated and all required review fields remain.
    artifact = Path(paths["phase_observability_json"]).read_text(encoding="utf-8")
    assert '"review_count": 1' in artifact
    assert '"logical_round": 2' in artifact
    assert '"remaining_rounds": 1' in artifact
    assert '"phase5_iteration": 4' in artifact
    assert '"improvement_status": "applied"' in artifact
    assert '"duration_seconds": 8.75' in artifact
    assert '"session_id": "reviewer-session"' in artifact
    assert (
        '"command_id": "reviewer-session:review_result:round-2:attempt-1"' in artifact
    )


def test_missing_correlation_and_observer_failure_are_outcome_neutral(
    tmp_path: Path,
) -> None:
    # Given a valid gate transition but missing run metadata or a failed observer.
    gate = ReviewGate().record_judgment(ReviewVerdict.ACCEPT)
    transition = ReviewTransition(
        phase_id="phase_5_validation",
        phase5_iteration=1,
        previous_round_count=0,
        gate=gate,
        receipt=ReviewCommandReceipt(
            "reviewer-session",
            "review-command-1",
            "reviewer",
            "review_result",
            1.0,
        ),
        improvement_status=ImprovementStatus.NOT_REQUIRED,
    )
    observer = TelemetryObserver(FakeSessionManager(), tmp_path)

    # When publication encounters each observational failure.
    assert publish_review_transition(observer, transition) is False
    failing: ReviewCompletionObserver = FailingReviewObserver()
    assert publish_review_transition(failing, transition) is False

    # Then no artifact is claimed and the review gate remains accepted.
    assert "phase_observability_json" not in observer.save_metrics()
    assert gate.outcome is not None
    assert gate.outcome.value == "accepted"


@pytest.mark.parametrize("signal", [KeyboardInterrupt(), SystemExit(7)])
def test_review_observer_control_flow_signals_propagate(
    signal: KeyboardInterrupt | SystemExit,
) -> None:
    # Given an accepted review transition and a control-flow observer signal.
    gate = ReviewGate().record_judgment(ReviewVerdict.ACCEPT)
    transition = ReviewTransition(
        "phase_5_validation",
        1,
        0,
        gate,
        ReviewCommandReceipt("session", "command", "reviewer", "review", 1.0),
        ImprovementStatus.NOT_REQUIRED,
    )

    # When review publication reaches the observer boundary.
    with pytest.raises(type(signal)):
        _ = publish_review_transition(ControlFlowReviewObserver(signal), transition)

    # Then the authoritative review result remains unchanged.
    assert gate.outcome is not None
    assert gate.outcome.value == "accepted"


def test_unknown_prose_and_malformed_correlation_fail_closed() -> None:
    # Given misleading prose and an empty command correlation identifier.
    verdict = ReviewVerdict.from_raw("looks good, verdict: accept")

    # When the verdict and correlation cross their typed boundaries.
    with pytest.raises(ObservabilityContractError):
        _ = CommandCorrelation("run-9", "reviewer-session", "")

    # Then prose is never promoted to acceptance.
    assert verdict is ReviewVerdict.UNKNOWN


def test_concise_artifact_does_not_duplicate_agent_payloads(tmp_path: Path) -> None:
    # Given opted-in raw Agent I/O and a concise accepted review record.
    raw_logger = AgentIOLogger(tmp_path, "run-9", enabled=True, redact=False)
    observer = TelemetryObserver(
        FakeSessionManager(), tmp_path, agent_io_logger=raw_logger
    )
    observer.set_metadata("run_id", "run-9")
    session_id = observer.get_or_create("reviewer")
    response = observer.send_command(
        session_id,
        "secret prompt OPENAI_API_KEY=sk-abcdefghijklmnopqrstuvwxyz",
    )
    gate = ReviewGate().record_judgment(ReviewVerdict.ACCEPT)
    transition = ReviewTransition(
        "phase_5_validation",
        1,
        0,
        gate,
        ReviewCommandReceipt(
            session_id, "review-command-1", "reviewer", "review_result", 1.5
        ),
        ImprovementStatus.NOT_REQUIRED,
    )
    assert publish_review_transition(observer, transition) is True

    # When raw and concise channels are serialized.
    paths = observer.save_metrics()
    raw_prompt = (tmp_path / "agent_io" / "payloads" / "000001_prompt.txt").read_text(
        encoding="utf-8"
    )
    concise = Path(paths["phase_observability_json"]).read_text(encoding="utf-8")

    # Then raw content remains only in the existing Agent I/O surface.
    assert response == "full response body"
    assert "sk-abcdefghijklmnopqrstuvwxyz" in raw_prompt
    assert "secret prompt" not in concise
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in concise
    assert "full response body" not in concise
