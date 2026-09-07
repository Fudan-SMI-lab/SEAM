from __future__ import annotations

import logging
from dataclasses import dataclass

import pytest
from typing_extensions import override

from core.review_gate import ReviewGate
from core.review_observability import (
    ReviewCommandReceipt,
    ReviewTransition,
    publish_review_transition,
)
from core.run_outcome import ReviewVerdict
from core.runtime_observability_models import ImprovementStatus, ReviewCompletion


@dataclass(frozen=True, slots=True)
class RunIdPropertyFailure(Exception):
    detail: str

    @override
    def __str__(self) -> str:
        return self.detail


@dataclass(frozen=True, slots=True)
class FailingRunIdObserver:
    failure: Exception | KeyboardInterrupt | SystemExit

    @property
    def run_id(self) -> str:
        raise self.failure

    def record_review_completion(self, completion: ReviewCompletion) -> bool:
        del completion
        return True


def _accepted_transition() -> ReviewTransition:
    gate = ReviewGate().record_judgment(ReviewVerdict.ACCEPT)
    return ReviewTransition(
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


@pytest.mark.parametrize(
    "failure",
    [TypeError("invalid run id"), KeyError("run_id"), RunIdPropertyFailure("failed")],
)
def test_run_id_property_exception_is_outcome_neutral_and_logged(
    failure: Exception,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Given an accepted review and an observer whose run correlation property fails.
    transition = _accepted_transition()
    caplog.set_level(logging.WARNING, logger="core.review_observability")

    # When review observability reads the failing property.
    published = publish_review_transition(FailingRunIdObserver(failure), transition)

    # Then observation is skipped with a diagnostic and the review remains accepted.
    assert published is False
    assert type(failure).__name__ in caplog.text
    assert "Review observability observer failed" in caplog.text
    assert transition.gate.outcome is not None
    assert transition.gate.outcome.value == "accepted"


@pytest.mark.parametrize("signal", [KeyboardInterrupt(), SystemExit(9)])
def test_run_id_property_control_signal_propagates(
    signal: KeyboardInterrupt | SystemExit,
) -> None:
    # Given an accepted review and a run correlation property raising control flow.
    transition = _accepted_transition()

    # When review observability reads the property, then the signal propagates.
    with pytest.raises(type(signal)):
        _ = publish_review_transition(FailingRunIdObserver(signal), transition)

    # Then the authoritative review remains accepted.
    assert transition.gate.outcome is not None
    assert transition.gate.outcome.value == "accepted"
