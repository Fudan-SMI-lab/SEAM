from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum, unique
from typing import ClassVar, final

from pydantic import GetCoreSchemaHandler, JsonValue
from pydantic_core import CoreSchema, core_schema
from core.compat import SLOTS_KWARG, Self, assert_never, override


class _SafeIdentifier(str):
    _error_reason: ClassVar[str]

    def __new__(cls, raw: str) -> Self:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", raw):
            raise OutcomeContractError(reason=cls._error_reason)
        return str.__new__(cls, raw)

    @classmethod
    def __get_pydantic_core_schema__(
        cls,
        _source_type: type[str],
        handler: GetCoreSchemaHandler,
    ) -> CoreSchema:
        return core_schema.no_info_after_validator_function(cls, handler(str))


@final
class PhaseId(_SafeIdentifier):
    _error_reason = "phase_id is not a valid identifier"


@final
class AcceptedAttemptId(_SafeIdentifier):
    _error_reason = "accepted attempt is not a valid identifier"


@final
class WorkflowTerminal(_SafeIdentifier):
    _error_reason = "workflow terminal is not a valid identifier"


@unique
class ReviewVerdict(str, Enum):
    """A reviewer token parsed at the response boundary."""

    ACCEPT = "accept"
    REJECT = "reject"
    UNKNOWN = "unknown"

    @classmethod
    def from_raw(cls, raw: JsonValue) -> ReviewVerdict:
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
    def from_raw(cls, raw: JsonValue) -> ReviewOutcome:
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


class OutcomeContractError(Exception):
    """Raised when domain facts describe an impossible outcome state."""

    reason: str

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason

    @override
    def __str__(self) -> str:
        return self.reason


def _expected_verdict(outcome: ReviewOutcome) -> ReviewVerdict | None:
    if outcome is ReviewOutcome.DISABLED:
        return None
    if outcome is ReviewOutcome.ACCEPTED:
        return ReviewVerdict.ACCEPT
    if outcome is ReviewOutcome.REJECTED or outcome is ReviewOutcome.REJECT_EXHAUSTED:
        return ReviewVerdict.REJECT
    if outcome is ReviewOutcome.UNKNOWN or outcome is ReviewOutcome.SESSION_ERROR:
        return ReviewVerdict.UNKNOWN
    if outcome is ReviewOutcome.IMPROVEMENT_ERROR:
        return ReviewVerdict.REJECT
    assert_never(outcome)


@dataclass(frozen=True, **SLOTS_KWARG)
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

        outcome = self.outcome
        if outcome is ReviewOutcome.REJECTED and self.round_number >= self.max_rounds:
            raise OutcomeContractError(
                reason="rejected is intermediate and requires a remaining round"
            )
        elif outcome is ReviewOutcome.REJECT_EXHAUSTED and self.round_number != self.max_rounds:
            raise OutcomeContractError(
                reason="reject_exhausted requires the final review round"
            )
        elif outcome in (
            ReviewOutcome.ACCEPTED,
            ReviewOutcome.REJECTED,
            ReviewOutcome.REJECT_EXHAUSTED,
            ReviewOutcome.UNKNOWN,
            ReviewOutcome.SESSION_ERROR,
            ReviewOutcome.IMPROVEMENT_ERROR,
        ):
            pass
        elif outcome is ReviewOutcome.DISABLED:
            raise OutcomeContractError(
                reason="disabled review cannot create a round"
            )
        else:
            assert_never(outcome)


@dataclass(frozen=True, **SLOTS_KWARG)
class TerminalAnchor:
    """The phase from which a terminal run may be diagnosed or continued."""

    phase_id: PhaseId

    def __post_init__(self) -> None:
        if not self.phase_id.strip():
            raise OutcomeContractError(
                reason="terminal anchor phase_id cannot be empty"
            )


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
        if review_round.outcome is not ReviewOutcome.REJECTED:
            raise OutcomeContractError(
                reason="only rejected review rounds may precede another round"
            )


@dataclass(frozen=True, **SLOTS_KWARG)
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
        if self.review_outcome is ReviewOutcome.DISABLED:
            if self.review_rounds:
                raise OutcomeContractError(
                    reason="disabled review cannot retain review rounds"
                )
        else:
            if not self.review_rounds:
                raise OutcomeContractError(
                    reason="enabled review requires at least one review round"
                )
            if self.review_rounds[-1].outcome is not self.review_outcome:
                raise OutcomeContractError(
                    reason="final review round must match the review disposition"
                )

        _validate_review_history(self.review_rounds)

        review_outcome = self.review_outcome
        if review_outcome in (ReviewOutcome.DISABLED, ReviewOutcome.ACCEPTED):
            pass
        elif review_outcome in (
            ReviewOutcome.REJECTED,
            ReviewOutcome.REJECT_EXHAUSTED,
            ReviewOutcome.UNKNOWN,
            ReviewOutcome.SESSION_ERROR,
            ReviewOutcome.IMPROVEMENT_ERROR,
        ):
            if self.accepted_attempt_id is not None:
                raise OutcomeContractError(
                    reason="non-accepting review cannot retain an accepted attempt"
                )
        else:
            assert_never(review_outcome)

        if (
            self.validation_succeeded
            and self.review_outcome is ReviewOutcome.ACCEPTED
            and self.accepted_attempt_id is None
        ):
            raise OutcomeContractError(
                reason="accepted review requires an accepted attempt"
            )

        if not self.validation_succeeded or not self.executed_phases:
            terminal_outcome = TerminalOutcome.FAILED
        else:
            if (
                self.review_outcome is ReviewOutcome.DISABLED
                or self.review_outcome is ReviewOutcome.ACCEPTED
            ):
                terminal_outcome = TerminalOutcome.PASSED
            elif self.review_outcome is ReviewOutcome.REJECT_EXHAUSTED:
                terminal_outcome = (
                    TerminalOutcome.FAILED
                    if self.review_fail_closed
                    else TerminalOutcome.PASSED_WITH_REVIEWS
                )
            else:
                terminal_outcome = TerminalOutcome.FAILED

        if terminal_outcome is TerminalOutcome.PASSED:
            if self.accepted_attempt_id is None:
                raise OutcomeContractError(
                    reason="passed outcome requires an accepted attempt"
                )
        elif terminal_outcome in (
            TerminalOutcome.PASSED_WITH_REVIEWS,
            TerminalOutcome.FAILED,
        ):
            pass
        else:
            assert_never(terminal_outcome)

        object.__setattr__(self, "terminal_outcome", terminal_outcome)
