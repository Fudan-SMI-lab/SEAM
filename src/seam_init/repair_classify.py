"""Pure exhaustive classification of repair validation snapshots.

Maps every ``ValidationFact`` and ``OmoValidationFact`` member into one of
four repair categories. Terminal non-config facts take priority so a
transport/server/timeout failure is never hidden behind a repairable or
deferred outcome. The frozensets partition all failure facts exhaustively;
the companion test suite fails loudly if either enum grows a new member.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, unique
from pathlib import Path
from typing import Final, Protocol, final, runtime_checkable

from core.compat import assert_never
from seam_init.models import FailureKind, SafeDetail
from seam_init.opencode_config import OpencodeConfigResult
from seam_init.opencode_selection import PromptPort
from seam_init.opencode_validation import ValidationFact, ValidationOutcome
from seam_init.omo_config import OmoConfigResult
from seam_init.omo_validation import OmoValidationFact, OmoValidationOutcome

__all__ = [
    "MAX_ROUNDS", "OmoEditPort", "OmoRevalidatePort", "OpencodeEditPort",
    "OpencodeRevalidatePort", "RepairCategory", "RepairOutcome",
    "RepairRequest", "RepairStatus", "RepairValidation", "RepairableDomain",
    "classify_repair", "omo_repairable_fact", "omo_terminal_fact",
    "opencode_repairable_fact", "opencode_terminal_fact",
    "repairable_domain_for",
]

MAX_ROUNDS: Final[int] = 2


@unique
class RepairCategory(str, Enum):
    """Four mutually exclusive classifications of a validation snapshot."""

    SUCCESS = "success"
    PENDING_AUTH = "pending_auth"
    REPAIRABLE = "repairable"
    TERMINAL = "terminal"


@unique
class RepairableDomain(str, Enum):
    """Which config domain a repairable failure belongs to."""

    OPENCODE = "opencode"
    OMO = "omo"


@final
@dataclass(frozen=True, slots=True)
class RepairValidation:
    """Immutable snapshot of both validator outcomes at one point in time."""

    opencode: ValidationOutcome
    omo: OmoValidationOutcome


@unique
class RepairStatus(str, Enum):
    READY = "ready"
    PENDING_AUTH = "pending_auth"
    STOPPED = "stopped"
    EXHAUSTED = "exhausted"
    TERMINAL = "terminal"


@final
@dataclass(frozen=True, slots=True)
class RepairOutcome:
    """Frozen repair outcome with legal-state invariants."""

    status: RepairStatus
    rounds_used: int
    edits: int
    restorations: int
    final: RepairValidation = field(repr=False)
    failure_kind: FailureKind | None
    exhausted: bool
    safe_detail: SafeDetail

    def __post_init__(self) -> None:
        if not 0 <= self.rounds_used <= MAX_ROUNDS:
            raise ValueError(f"rounds_used must be [0,{MAX_ROUNDS}]")
        if self.edits < 0 or self.restorations < 0:
            raise ValueError("edits/restorations must be nonnegative")
        if self.edits + self.restorations > self.rounds_used:
            raise ValueError("edits+restorations cannot exceed rounds_used")
        if self.exhausted != (self.status is RepairStatus.EXHAUSTED):
            raise ValueError("exhausted flag must match EXHAUSTED status")
        if self.status is RepairStatus.EXHAUSTED and self.rounds_used != MAX_ROUNDS:
            raise ValueError("EXHAUSTED requires rounds_used == MAX_ROUNDS")
        no_failure = self.failure_kind is None
        match self.status:
            case RepairStatus.READY | RepairStatus.PENDING_AUTH:
                if not no_failure:
                    raise ValueError(
                        f"{self.status} must not carry failure_kind")
            case RepairStatus.STOPPED | RepairStatus.EXHAUSTED | RepairStatus.TERMINAL:
                if no_failure:
                    raise ValueError(
                        f"{self.status} requires failure_kind")
            case unreachable:
                assert_never(unreachable)
        category = classify_repair(self.final)
        match self.status:
            case RepairStatus.READY:
                if category is not RepairCategory.SUCCESS:
                    raise ValueError("READY requires SUCCESS classification")
            case RepairStatus.PENDING_AUTH:
                if category is not RepairCategory.PENDING_AUTH:
                    raise ValueError(
                        "PENDING_AUTH requires PENDING_AUTH classification")
            case RepairStatus.STOPPED | RepairStatus.EXHAUSTED:
                if category is not RepairCategory.REPAIRABLE:
                    raise ValueError(
                        f"{self.status} requires REPAIRABLE classification")
            case RepairStatus.TERMINAL:
                pass
            case unreachable:
                assert_never(unreachable)


@runtime_checkable
class OpencodeRevalidatePort(Protocol):
    def __call__(self) -> ValidationOutcome: ...


@runtime_checkable
class OmoRevalidatePort(Protocol):
    def __call__(self) -> OmoValidationOutcome: ...


@runtime_checkable
class OpencodeEditPort(Protocol):
    def __call__(self) -> OpencodeConfigResult: ...


@runtime_checkable
class OmoEditPort(Protocol):
    def __call__(self) -> OmoConfigResult: ...


@final
@dataclass(frozen=True, slots=True)
class RepairRequest:
    """All repair-loop inputs as one immutable value.

    Prompt and callback fields are ``repr=False`` because their runtime
    objects may hold scripted answers, environment maps, or credentials.
    """

    project_root: Path
    opencode_target: Path
    omo_target: Path
    prompt: PromptPort = field(repr=False)
    initial: RepairValidation
    revalidate_opencode: OpencodeRevalidatePort = field(repr=False)
    revalidate_omo: OmoRevalidatePort = field(repr=False)
    edit_opencode: OpencodeEditPort = field(repr=False)
    edit_omo: OmoEditPort = field(repr=False)


_OPENCODE_REPAIRABLE: Final[frozenset[ValidationFact]] = frozenset({
    ValidationFact.AUTH_FAILURE,
    ValidationFact.MODEL_NOT_FOUND,
    ValidationFact.CONFIG_FAILURE,
})

_OPENCODE_TERMINAL: Final[frozenset[ValidationFact]] = frozenset({
    ValidationFact.TRANSPORT_FAILURE,
    ValidationFact.SERVER_FAILURE,
    ValidationFact.MARKER_MISSING,
    ValidationFact.MARKER_MALFORMED,
    ValidationFact.TIMEOUT_FAILURE,
    ValidationFact.VERSION_FAILURE,
    ValidationFact.CLEANUP_FAILURE,
    ValidationFact.INVALID_ARGUMENT,
    ValidationFact.UNKNOWN,
})

_OMO_REPAIRABLE: Final[frozenset[OmoValidationFact]] = frozenset({
    OmoValidationFact.DOCTOR_CONFIG_INVALID,
})

_OMO_TERMINAL: Final[frozenset[OmoValidationFact]] = frozenset({
    OmoValidationFact.DOCTOR_FAILURE,
    OmoValidationFact.DOCTOR_MALFORMED,
    OmoValidationFact.DOCTOR_MISSING_CHECK,
    OmoValidationFact.DOCTOR_TIMEOUT,
    OmoValidationFact.RUN_FAILURE,
    OmoValidationFact.RUN_MALFORMED,
    OmoValidationFact.RUN_FALSE_SUCCESS,
    OmoValidationFact.RUN_MARKER_MISSING,
    OmoValidationFact.RUN_FIELD_INVALID,
    OmoValidationFact.RUN_TIMEOUT,
})


def opencode_repairable_fact(fact: ValidationFact) -> bool:
    return fact in _OPENCODE_REPAIRABLE


def opencode_terminal_fact(fact: ValidationFact) -> bool:
    return fact in _OPENCODE_TERMINAL


def omo_repairable_fact(fact: OmoValidationFact) -> bool:
    return fact in _OMO_REPAIRABLE


def omo_terminal_fact(fact: OmoValidationFact) -> bool:
    return fact in _OMO_TERMINAL


def repairable_domain_for(
    validation: RepairValidation,
) -> RepairableDomain | None:
    """Return the domain whose fact is repairable, or None.

    OpenCode is checked first so that when both domains are repairable the
    loop edits OpenCode in round 1 and OMO in round 2.
    """
    if validation.opencode.fact in _OPENCODE_REPAIRABLE:
        return RepairableDomain.OPENCODE
    if validation.omo.fact in _OMO_REPAIRABLE:
        return RepairableDomain.OMO
    return None


def classify_repair(validation: RepairValidation) -> RepairCategory:
    """Exhaustively classify a validation snapshot into one repair category.

    Priority: terminal > success > repairable > pending-auth. A terminal
    non-config failure in either domain terminates immediately so it is
    never hidden behind a repairable or deferred outcome.
    """
    oc = validation.opencode.fact
    omo = validation.omo.fact
    if oc in _OPENCODE_TERMINAL or omo in _OMO_TERMINAL:
        return RepairCategory.TERMINAL
    if oc is ValidationFact.MESSAGE_READY and omo is OmoValidationFact.VALIDATED:
        return RepairCategory.SUCCESS
    if oc in _OPENCODE_REPAIRABLE or omo in _OMO_REPAIRABLE:
        return RepairCategory.REPAIRABLE
    return RepairCategory.PENDING_AUTH
