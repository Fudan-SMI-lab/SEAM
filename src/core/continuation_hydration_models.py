from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum, unique
from pathlib import Path
from typing import Final, final

from pydantic import JsonValue, TypeAdapter
from typing_extensions import TypeAlias, override

from core.continuation_models import ResolvedTerminalParent
from core.run_manifest import (
    CanonicalReference,
    EvidenceDigest,
    RunId,
)
from core.run_outcome import (
    AcceptedAttemptId,
    OutcomeContractError,
    PhaseId,
    ReviewOutcome,
    ReviewRound,
    RunOutcome,
    TerminalAnchor,
    WorkflowTerminal,
)

CanonicalJsonObject: TypeAlias = dict[str, JsonValue]
_CANONICAL_ADAPTER: Final = TypeAdapter(CanonicalJsonObject)


@unique
class ContinuationHydrationErrorKind(str, Enum):
    AUTHORITY_MISMATCH = "authority_mismatch"
    WORKFLOW_DIGEST_MISMATCH = "workflow_digest_mismatch"
    UNKNOWN_ANCHOR = "unknown_anchor"
    AMBIGUOUS_ANCHOR = "ambiguous_anchor"
    FAILED_CANONICAL_PREDECESSOR = "failed_canonical_predecessor"
    MISSING_CANONICAL_OUTPUT = "missing_canonical_output"
    CANONICAL_DIGEST_MISMATCH = "canonical_digest_mismatch"
    MALFORMED_CANONICAL_OUTPUT = "malformed_canonical_output"
    ACCEPTED_ATTEMPT_INVALID = "accepted_attempt_invalid"
    EMPTY_CHILD_EXECUTION = "empty_child_execution"


@final
class ContinuationHydrationError(Exception):
    __slots__ = ("kind", "detail")

    def __init__(self, kind: ContinuationHydrationErrorKind, detail: str) -> None:
        super().__init__(kind, detail)
        self.kind = kind
        self.detail = detail

    @override
    def __str__(self) -> str:
        return f"{self.kind.value}: {self.detail}"


@dataclass(frozen=True)
class ParentAcceptedAttemptReference:
    parent_run_id: RunId
    attempt_id: AcceptedAttemptId
    canonical_reference: CanonicalReference
    receipt_evidence: EvidenceDigest
    review_outcome: ReviewOutcome
    review_fail_closed: bool
    review_rounds: tuple[ReviewRound, ...]

    def __post_init__(self) -> None:
        if not str(self.attempt_id).strip():
            raise ContinuationHydrationError(
                ContinuationHydrationErrorKind.ACCEPTED_ATTEMPT_INVALID,
                "accepted attempt identifier is empty",
            )
        if self.canonical_reference.phase_id != PhaseId("phase_5_validation"):
            raise ContinuationHydrationError(
                ContinuationHydrationErrorKind.ACCEPTED_ATTEMPT_INVALID,
                "accepted attempt must reference Phase 5 canonical evidence",
            )
        try:
            _ = RunOutcome(
                validation_succeeded=True,
                review_outcome=self.review_outcome,
                review_fail_closed=self.review_fail_closed,
                workflow_terminal=WorkflowTerminal("complete"),
                terminal_anchor=TerminalAnchor(PhaseId("phase_5_validation")),
                executed_phases=(PhaseId("phase_5_validation"),),
                accepted_attempt_id=self.attempt_id,
                review_rounds=self.review_rounds,
            )
        except OutcomeContractError as exc:
            raise ContinuationHydrationError(
                ContinuationHydrationErrorKind.ACCEPTED_ATTEMPT_INVALID,
                "accepted attempt has no accepted review disposition",
            ) from exc
        if self.review_outcome not in {
            ReviewOutcome.DISABLED,
            ReviewOutcome.ACCEPTED,
        }:
            raise ContinuationHydrationError(
                ContinuationHydrationErrorKind.ACCEPTED_ATTEMPT_INVALID,
                "accepted attempt has no accepted review disposition",
            )


@dataclass(frozen=True)
class InheritedPhaseResult:
    phase_id: PhaseId
    state_key: str
    canonical_reference: CanonicalReference
    inherited: bool = True


@dataclass(frozen=True)
class InheritedStateValue:
    state_key: str
    canonical_json: bytes


@dataclass(frozen=True)
class ContinuationHydration:
    state_entries: tuple[InheritedStateValue, ...]
    phase_results: tuple[InheritedPhaseResult, ...]
    start_phase_id: PhaseId
    parent_accepted_attempt: ParentAcceptedAttemptReference | None

    def __post_init__(self) -> None:
        state_keys = tuple(entry.state_key for entry in self.state_entries)
        result_keys = tuple(result.state_key for result in self.phase_results)
        if (
            len(state_keys) != len(set(state_keys))
            or state_keys != result_keys
            or any(not result.inherited for result in self.phase_results)
        ):
            raise ContinuationHydrationError(
                ContinuationHydrationErrorKind.AUTHORITY_MISMATCH,
                "hydrated state and inherited result provenance do not agree",
            )
        for entry, result in zip(self.state_entries, self.phase_results, strict=True):
            _ = _CANONICAL_ADAPTER.validate_json(entry.canonical_json)
            if result.phase_id != result.canonical_reference.phase_id or hashlib.sha256(
                entry.canonical_json
            ).hexdigest() != str(result.canonical_reference.digest):
                raise ContinuationHydrationError(
                    ContinuationHydrationErrorKind.AUTHORITY_MISMATCH,
                    "hydrated canonical bytes and provenance digest do not agree",
                )
        if self.parent_accepted_attempt is not None:
            phase5 = next(
                (
                    result
                    for result in self.phase_results
                    if result.phase_id == PhaseId("phase_5_validation")
                ),
                None,
            )
            if (
                phase5 is None
                or phase5.canonical_reference
                != self.parent_accepted_attempt.canonical_reference
            ):
                raise ContinuationHydrationError(
                    ContinuationHydrationErrorKind.ACCEPTED_ATTEMPT_INVALID,
                    "accepted attempt is detached from inherited Phase 5",
                )

    @property
    def initial_state(self) -> dict[str, CanonicalJsonObject]:
        return {
            entry.state_key: _CANONICAL_ADAPTER.validate_json(entry.canonical_json)
            for entry in self.state_entries
        }


@dataclass(frozen=True)
class ContinuationHydrationRequest:
    parent: ResolvedTerminalParent
    summary_path: Path
    parent_accepted_attempt: ParentAcceptedAttemptReference | None = None


def require_executable_hydration(
    hydration: ContinuationHydration,
    phase_ids: tuple[str, ...],
    terminals: tuple[str, ...],
) -> None:
    start_phase = str(hydration.start_phase_id)
    if start_phase in terminals:
        raise ContinuationHydrationError(
            ContinuationHydrationErrorKind.EMPTY_CHILD_EXECUTION,
            f"hydrated start phase is terminal: {start_phase}",
        )
    start_count = phase_ids.count(start_phase)
    if start_count == 0:
        raise ContinuationHydrationError(
            ContinuationHydrationErrorKind.UNKNOWN_ANCHOR,
            f"hydrated start phase is absent: {start_phase}",
        )
    if start_count > 1:
        raise ContinuationHydrationError(
            ContinuationHydrationErrorKind.AMBIGUOUS_ANCHOR,
            f"hydrated start phase is ambiguous: {start_phase}",
        )
