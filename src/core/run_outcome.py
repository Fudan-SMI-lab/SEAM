from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, unique
from typing import NewType

from typing_extensions import assert_never, override

PhaseId = NewType("PhaseId", str)
AcceptedAttemptId = NewType("AcceptedAttemptId", str)
WorkflowTerminal = NewType("WorkflowTerminal", str)


@unique
class ReviewVerdict(str, Enum):
    """A reviewer token parsed at the response boundary."""

    ACCEPT = "accept"
    REJECT = "reject"
    UNKNOWN = "unknown"

    @classmethod
    def from_raw(cls, raw: object) -> ReviewVerdict:
        """Parse only a complete verdict token; prose is never authoritative."""
        if not isinstance(raw, str):
            return cls.UNKNOWN
        try:
            return cls(raw.strip().lower())
        except ValueError:
            return cls.UNKNOWN


@unique
class ReviewOutcome(str, Enum):
    """The review gate disposition retained independently from run status."""

    DISABLED = "disabled"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    REJECT_EXHAUSTED = "reject_exhausted"
    UNKNOWN = "unknown"
    SESSION_ERROR = "session_error"
    IMPROVEMENT_ERROR = "improvement_error"

    @classmethod
    def from_raw(cls, raw: object) -> ReviewOutcome:
        """Parse a complete disposition token and fail closed to unknown."""
        if not isinstance(raw, str):
            return cls.UNKNOWN
        try:
            return cls(raw.strip().lower())
        except ValueError:
            return cls.UNKNOWN


@unique
class TerminalOutcome(str, Enum):
    """The frozen migration result used by V3 summary and exit mapping."""

    PASSED = "passed"
    PASSED_WITH_REVIEWS = "passed_with_reviews"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class OutcomeContractError(Exception):
    """Raised when domain facts describe an impossible outcome state."""

    reason: str

    @override
    def __str__(self) -> str:
        return self.reason


def _expected_verdict(outcome: ReviewOutcome) -> ReviewVerdict | None:
    match outcome:
        case ReviewOutcome.DISABLED:
            return None
        case ReviewOutcome.ACCEPTED:
            return ReviewVerdict.ACCEPT
        case ReviewOutcome.REJECTED | ReviewOutcome.REJECT_EXHAUSTED:
            return ReviewVerdict.REJECT
        case ReviewOutcome.UNKNOWN | ReviewOutcome.SESSION_ERROR:
            return ReviewVerdict.UNKNOWN
        case ReviewOutcome.IMPROVEMENT_ERROR:
            return ReviewVerdict.REJECT
        case _:
            assert_never(outcome)


@dataclass(frozen=True, slots=True)
class ReviewRound:
    """One immutable logical reviewer judgment within the review budget."""

    round_number: int
    max_rounds: int
    verdict: ReviewVerdict
    outcome: ReviewOutcome

    def __post_init__(self) -> None:
        if self.max_rounds < 1:
            raise OutcomeContractError(reason="max_rounds must be positive")
        if not 1 <= self.round_number <= self.max_rounds:
            raise OutcomeContractError(reason="round_number must be within max_rounds")

        expected_verdict = _expected_verdict(self.outcome)
        if expected_verdict is None:
            raise OutcomeContractError(reason="disabled review cannot create a round")
        if self.verdict is not expected_verdict:
            raise OutcomeContractError(reason="review outcome conflicts with verdict")

        match self.outcome:
            case ReviewOutcome.REJECTED:
                if self.round_number >= self.max_rounds:
                    raise OutcomeContractError(
                        reason="rejected is intermediate and requires a remaining round"
                    )
            case ReviewOutcome.REJECT_EXHAUSTED:
                if self.round_number != self.max_rounds:
                    raise OutcomeContractError(
                        reason="reject_exhausted requires the final review round"
                    )
            case (
                ReviewOutcome.DISABLED
                | ReviewOutcome.ACCEPTED
                | ReviewOutcome.UNKNOWN
                | ReviewOutcome.SESSION_ERROR
                | ReviewOutcome.IMPROVEMENT_ERROR
            ):
                pass
            case _:
                assert_never(self.outcome)


@dataclass(frozen=True, slots=True)
class TerminalAnchor:
    """The phase from which a terminal run may be diagnosed or continued."""

    phase_id: PhaseId

    def __post_init__(self) -> None:
        if not self.phase_id.strip():
            raise OutcomeContractError(reason="terminal anchor phase_id cannot be empty")


def _validate_review_history(review_rounds: tuple[ReviewRound, ...]) -> None:
    if not review_rounds:
        return

    max_rounds = review_rounds[0].max_rounds
    final_index = len(review_rounds)
    for expected_number, review_round in enumerate(review_rounds, start=1):
        if review_round.max_rounds != max_rounds:
            raise OutcomeContractError(reason="review rounds must share one max_rounds")
        if review_round.round_number != expected_number:
            raise OutcomeContractError(
                reason="review round numbers must be contiguous and start at one"
            )
        if expected_number == final_index:
            continue
        match review_round.outcome:
            case ReviewOutcome.REJECTED:
                pass
            case (
                ReviewOutcome.DISABLED
                | ReviewOutcome.ACCEPTED
                | ReviewOutcome.REJECT_EXHAUSTED
                | ReviewOutcome.UNKNOWN
                | ReviewOutcome.SESSION_ERROR
                | ReviewOutcome.IMPROVEMENT_ERROR
            ):
                raise OutcomeContractError(
                    reason="only rejected review rounds may precede another round"
                )
            case _:
                assert_never(review_round.outcome)


@dataclass(frozen=True, slots=True)
class RunOutcome:
    """Authoritative V3 result with terminal, workflow, attempt, and review facts."""

    validation_succeeded: bool
    review_outcome: ReviewOutcome
    review_fail_closed: bool
    workflow_terminal: WorkflowTerminal
    terminal_anchor: TerminalAnchor
    executed_phases: tuple[PhaseId, ...]
    accepted_attempt_id: AcceptedAttemptId | None
    review_rounds: tuple[ReviewRound, ...]
    terminal_outcome: TerminalOutcome = field(init=False)

    def __post_init__(self) -> None:
        match self.review_outcome:
            case ReviewOutcome.DISABLED:
                if self.review_rounds:
                    raise OutcomeContractError(
                        reason="disabled review cannot retain review rounds"
                    )
            case (
                ReviewOutcome.ACCEPTED
                | ReviewOutcome.REJECTED
                | ReviewOutcome.REJECT_EXHAUSTED
                | ReviewOutcome.UNKNOWN
                | ReviewOutcome.SESSION_ERROR
                | ReviewOutcome.IMPROVEMENT_ERROR
            ):
                if not self.review_rounds:
                    raise OutcomeContractError(
                        reason="enabled review requires at least one review round"
                    )
                if self.review_rounds[-1].outcome is not self.review_outcome:
                    raise OutcomeContractError(
                        reason="final review round must match the review disposition"
                    )
            case _:
                assert_never(self.review_outcome)

        _validate_review_history(self.review_rounds)

        if not self.validation_succeeded or not self.executed_phases:
            terminal_outcome = TerminalOutcome.FAILED
        else:
            match self.review_outcome:
                case ReviewOutcome.DISABLED | ReviewOutcome.ACCEPTED:
                    terminal_outcome = TerminalOutcome.PASSED
                case ReviewOutcome.REJECT_EXHAUSTED:
                    terminal_outcome = (
                        TerminalOutcome.FAILED
                        if self.review_fail_closed
                        else TerminalOutcome.PASSED_WITH_REVIEWS
                    )
                case (
                    ReviewOutcome.REJECTED
                    | ReviewOutcome.UNKNOWN
                    | ReviewOutcome.SESSION_ERROR
                    | ReviewOutcome.IMPROVEMENT_ERROR
                ):
                    terminal_outcome = TerminalOutcome.FAILED
                case _:
                    assert_never(self.review_outcome)

        object.__setattr__(self, "terminal_outcome", terminal_outcome)
