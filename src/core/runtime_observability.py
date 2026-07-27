from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path

from typing_extensions import assert_never

from core.runtime_observability_models import (
    ObservabilityAggregate,
    ObservabilitySummary,
    ReviewCompletion,
    ReviewDetails,
    TimeoutDetails,
    TimeoutScope,
)
from harness.session.events import (
    TransportAttemptCompleted,
    TransportAttemptErrored,
    TransportAttemptEvent,
    TransportAttemptStarted,
    TransportAttemptsExhausted,
    TransportAttemptTimedOut,
)

logger = logging.getLogger("core.runtime_observability")


class RuntimeObservability:
    """Mutable run-scoped accumulator for concise review and timeout records."""

    def __init__(self, output_dir: str | Path) -> None:
        self._output_path: Path = Path(output_dir) / "phase_observability.json"
        self._reviews: list[ReviewDetails] = []
        self._timeouts: list[TimeoutDetails] = []
        self._record_ids: set[str] = set()
        self._timeout_attempts: set[tuple[str, int]] = set()
        self._dropped_event_count: int = 0

    def add_review(self, completion: ReviewCompletion) -> ReviewDetails | None:
        if completion.record_id in self._record_ids:
            self._dropped_event_count += 1
            return None
        review_round = completion.review_round
        details = ReviewDetails(
            record_id=completion.record_id,
            phase_id=completion.scope.phase_id,
            phase5_iteration=completion.scope.phase5_iteration,
            logical_round=review_round.round_number,
            max_rounds=review_round.max_rounds,
            remaining_rounds=review_round.max_rounds - review_round.round_number,
            verdict=review_round.verdict.value,
            outcome=review_round.outcome.value,
            duration_seconds=round(completion.duration_seconds, 3),
            improvement_status=completion.improvement_status.value,
            session_id=completion.correlation.session_id,
            command_id=completion.correlation.command_id,
            reviewer_agent=completion.scope.reviewer_agent,
            sub_phase=completion.scope.sub_phase,
        )
        self._record_ids.add(completion.record_id)
        self._reviews.append(details)
        logger.info(
            "review_complete id=%s phase5_iteration=%d round=%d/%d remaining=%d "
            + "verdict=%s outcome=%s duration_s=%.3f improvement=%s session=%s command=%s",
            details["record_id"],
            details["phase5_iteration"],
            details["logical_round"],
            details["max_rounds"],
            details["remaining_rounds"],
            details["verdict"],
            details["outcome"],
            details["duration_seconds"],
            details["improvement_status"],
            details["session_id"],
            details["command_id"],
        )
        return details

    def add_transport(
        self,
        scope: TimeoutScope,
        event: TransportAttemptEvent,
    ) -> TimeoutDetails | None:
        match event:
            case TransportAttemptTimedOut():
                self._timeout_attempts.add((str(event.invocation_id), event.attempt))
            case TransportAttemptsExhausted():
                key = (str(event.invocation_id), event.attempt)
                if key not in self._timeout_attempts:
                    self._dropped_event_count += 1
                    return None
            case (
                TransportAttemptStarted()
                | TransportAttemptCompleted()
                | TransportAttemptErrored()
            ):
                return None
            case _:
                assert_never(event)
        record_id = (
            f"{scope.run_id}:transport:{event.invocation_id}:"
            f"{event.attempt}:{event.phase.value}"
        )
        if record_id in self._record_ids:
            self._dropped_event_count += 1
            return None
        details = TimeoutDetails(
            record_id=record_id,
            event_phase=event.phase.value,
            agent=scope.agent,
            sub_phase=scope.sub_phase,
            session_id=event.session_id,
            command_id=str(event.invocation_id),
            attempt=event.attempt,
            max_attempts=event.max_attempts,
            configured_timeout_seconds=event.timeout_s,
            elapsed_seconds=round(event.elapsed_s, 3),
            retry_decision=event.retry_decision.value,
            reason=event.reason.value,
            exhausted=event.exhausted,
        )
        self._record_ids.add(record_id)
        self._timeouts.append(details)
        logger.warning(
            "transport_timeout id=%s agent=%s sub_phase=%s session=%s attempt=%d/%d "
            + "timeout_s=%.3f elapsed_s=%.3f retry=%s reason=%s exhausted=%s",
            details["record_id"],
            details["agent"],
            details["sub_phase"],
            details["session_id"],
            details["attempt"],
            details["max_attempts"],
            details["configured_timeout_seconds"],
            details["elapsed_seconds"],
            details["retry_decision"],
            details["reason"],
            str(details["exhausted"]).lower(),
        )
        return details

    def aggregate(self) -> ObservabilityAggregate:
        return ObservabilityAggregate(
            review_count=len(self._reviews),
            timeout_count=sum(
                record["event_phase"] == "timeout" for record in self._timeouts
            ),
            exhaustion_count=sum(
                record["event_phase"] == "exhausted" for record in self._timeouts
            ),
            dropped_event_count=self._dropped_event_count,
            review_duration_seconds=round(
                sum(record["duration_seconds"] for record in self._reviews), 3
            ),
            timeout_elapsed_seconds=round(
                sum(
                    record["elapsed_seconds"]
                    for record in self._timeouts
                    if record["event_phase"] == "timeout"
                ),
                3,
            ),
        )

    def summary(self) -> ObservabilitySummary:
        aggregate = self.aggregate()
        return ObservabilitySummary(
            review_count=aggregate["review_count"],
            reviews=tuple(self._reviews),
            timeout_count=aggregate["timeout_count"],
            timeouts=tuple(self._timeouts),
            exhaustion_count=aggregate["exhaustion_count"],
            dropped_event_count=aggregate["dropped_event_count"],
            review_duration_seconds=aggregate["review_duration_seconds"],
            timeout_elapsed_seconds=aggregate["timeout_elapsed_seconds"],
        )

    def write_artifact(self, summary: ObservabilitySummary | None = None) -> str | None:
        if not self._reviews and not self._timeouts:
            return None
        payload = asdict(summary or self.summary())
        temporary_path = self._output_path.with_suffix(".json.tmp")
        _ = temporary_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8"
        )
        _ = temporary_path.replace(self._output_path)
        return str(self._output_path)
