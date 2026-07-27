from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from typing_extensions import assert_never, override

from core.run_outcome import ReviewOutcome, ReviewRound, ReviewVerdict

DEFAULT_MAX_REVIEW_ROUNDS: Final = 3
REVIEW_GATE_STATE_KEY: Final = "_review_gate"


@dataclass(frozen=True, slots=True)
class ReviewGateConfigurationError(Exception):
    max_rounds: int

    @override
    def __str__(self) -> str:
        return f"max review rounds must be positive, got {self.max_rounds}"


@dataclass(frozen=True, slots=True)
class ReviewGateClosedError(Exception):
    outcome: ReviewOutcome

    @override
    def __str__(self) -> str:
        return f"review gate is closed with outcome {self.outcome.value}"


@dataclass(frozen=True, slots=True)
class ReviewGateTransitionError(Exception):
    reason: str

    @override
    def __str__(self) -> str:
        return self.reason


@dataclass(frozen=True, slots=True)
class ImprovementApplied:
    selected_phase: str


@dataclass(frozen=True, slots=True)
class ImprovementFailed:
    reason: str


ImprovementResult = ImprovementApplied | ImprovementFailed


@dataclass(frozen=True, slots=True)
class ReviewGate:
    """Immutable logical review-round state machine."""

    max_rounds: int = DEFAULT_MAX_REVIEW_ROUNDS
    rounds: tuple[ReviewRound, ...] = ()

    def __post_init__(self) -> None:
        if self.max_rounds < 1:
            raise ReviewGateConfigurationError(max_rounds=self.max_rounds)

    @property
    def outcome(self) -> ReviewOutcome | None:
        return self.rounds[-1].outcome if self.rounds else None

    @property
    def remaining_rounds(self) -> int:
        return self.max_rounds - len(self.rounds)

    def record_judgment(self, verdict: ReviewVerdict) -> ReviewGate:
        self._ensure_open()
        round_number = len(self.rounds) + 1
        match verdict:
            case ReviewVerdict.ACCEPT:
                outcome = ReviewOutcome.ACCEPTED
            case ReviewVerdict.REJECT:
                outcome = (
                    ReviewOutcome.REJECT_EXHAUSTED
                    if round_number == self.max_rounds
                    else ReviewOutcome.REJECTED
                )
            case ReviewVerdict.UNKNOWN:
                outcome = ReviewOutcome.UNKNOWN
            case _:
                assert_never(verdict)
        review_round = ReviewRound(
            round_number=round_number,
            max_rounds=self.max_rounds,
            verdict=verdict,
            outcome=outcome,
        )
        return ReviewGate(
            max_rounds=self.max_rounds, rounds=(*self.rounds, review_round)
        )

    def record_session_error(self) -> ReviewGate:
        self._ensure_open()
        review_round = ReviewRound(
            round_number=len(self.rounds) + 1,
            max_rounds=self.max_rounds,
            verdict=ReviewVerdict.UNKNOWN,
            outcome=ReviewOutcome.SESSION_ERROR,
        )
        return ReviewGate(
            max_rounds=self.max_rounds, rounds=(*self.rounds, review_round)
        )

    def record_improvement_error(self) -> ReviewGate:
        if self.outcome is not ReviewOutcome.REJECTED:
            raise ReviewGateTransitionError(
                reason="improvement error requires an intermediate rejected round"
            )
        rejected_round = self.rounds[-1]
        failed_round = ReviewRound(
            round_number=rejected_round.round_number,
            max_rounds=self.max_rounds,
            verdict=ReviewVerdict.REJECT,
            outcome=ReviewOutcome.IMPROVEMENT_ERROR,
        )
        return ReviewGate(
            max_rounds=self.max_rounds, rounds=(*self.rounds[:-1], failed_round)
        )

    def _ensure_open(self) -> None:
        outcome = self.outcome
        if outcome is not None and outcome is not ReviewOutcome.REJECTED:
            raise ReviewGateClosedError(outcome=outcome)
