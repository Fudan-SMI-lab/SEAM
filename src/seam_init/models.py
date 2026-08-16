"""Initializer domain contracts: statuses, categorized failures, exit codes.

READY needs provided auth + consented billable call; PENDING_AUTH defers it
after structural checks pass; FAILED carries a FailureKind whose int value is
its exit code (61-69). SafeDetail brands secret-free strings; typed errors
accept no config objects or raw subprocess output.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, IntEnum, unique
from typing import Final, NewType, Protocol, final, runtime_checkable

from typing_extensions import Self, assert_never, override


@unique
class ExitCode(IntEnum):
    """Initializer-owned process exit codes (never raw diagnostic propagation)."""

    READY = 0
    PENDING_AUTH = 60


@unique
class FailureKind(IntEnum):
    """Categorized initializer failures; each value is its exit code in 61-69."""

    PYTHON_ENVIRONMENT = 61
    SEAM_INSTALL = 62
    OPENCODE_INSTALL = 63
    OPENCODE_CONFIG = 64
    OMO_INSTALL = 65
    OMO_CONFIG = 66
    OPENCODE_RUNTIME = 67
    OPENCODE_VALIDATION = 68
    OMO_VALIDATION = 69


@unique
class InitializerStatus(str, Enum):
    """Three terminal initializer states; no free-form status strings allowed."""

    READY = "ready"
    PENDING_AUTH = "pending_auth"
    FAILED = "failed"


@unique
class AuthState(str, Enum):
    """Whether an API key was supplied or deliberately skipped."""

    PROVIDED = "provided"
    SKIPPED = "skipped"


@unique
class BillableCallConsent(str, Enum):
    """Explicit consent for a real (billable) provider validation call."""

    GIVEN = "given"
    DECLINED = "declined"


@unique
class StageKind(str, Enum):
    """The ordered initializer steps referenced by every later todo."""

    PYTHON_ENVIRONMENT = "python_environment"
    SEAM_INSTALL = "seam_install"
    OPENCODE_INSTALL = "opencode_install"
    OPENCODE_CONFIG = "opencode_config"
    OMO_INSTALL = "omo_install"
    OMO_CONFIG = "omo_config"
    OPENCODE_RUNTIME = "opencode_runtime"
    OPENCODE_VALIDATION = "opencode_validation"
    OMO_VALIDATION = "omo_validation"


@unique
class StageStatus(str, Enum):
    """Per-stage state machine values."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    SUCCEEDED = "succeeded"
    SKIPPED = "skipped"
    FAILED = "failed"


@unique
class EnvironmentKind(str, Enum):
    """Selected Python environment class."""

    BASE = "base"
    EXISTING_VENV = "existing_venv"
    NEW_VENV = "new_venv"


ProviderId = NewType("ProviderId", str)
ModelId = NewType("ModelId", str)
SafeDetail = NewType("SafeDetail", str)

_EMPTY_DETAIL: Final[SafeDetail] = SafeDetail("")


class InitializerContractError(Exception):
    """Raised when initializer facts describe an impossible outcome state."""

    reason: str

    def __init__(self, *, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason

    @override
    def __str__(self) -> str:
        return self.reason


@final
class InitializerFailure(Exception):
    """Categorized failure carrying only secret-free SafeDetail context."""

    __slots__ = ("kind", "safe_detail")

    kind: FailureKind
    safe_detail: SafeDetail

    def __init__(self, *, kind: FailureKind, safe_detail: SafeDetail) -> None:
        super().__init__(str(safe_detail))
        self.kind = kind
        self.safe_detail = safe_detail

    @override
    def __str__(self) -> str:
        return f"{self.kind.name}({int(self.kind)}): {self.safe_detail}"


@dataclass(frozen=True, slots=True)
class EnvironmentChoice:
    """The selected Python interpreter and its environment class."""

    kind: EnvironmentKind
    python_executable: str
    python_version: str

    def __post_init__(self) -> None:
        if not self.python_executable.strip():
            raise InitializerContractError(reason="python_executable must not be empty")
        if not self.python_version.strip():
            raise InitializerContractError(reason="python_version must not be empty")


@dataclass(frozen=True, slots=True)
class ProviderSelection:
    """A chosen OpenCode provider/model pair plus its auth state."""

    provider_id: ProviderId
    model_id: ModelId
    base_url: str | None = None
    auth_state: AuthState = AuthState.SKIPPED

    def __post_init__(self) -> None:
        if not str(self.provider_id).strip():
            raise InitializerContractError(reason="provider_id must not be empty")
        if not str(self.model_id).strip():
            raise InitializerContractError(reason="model_id must not be empty")


@dataclass(frozen=True, slots=True)
class StageRecord:
    """One immutable step observation in the initializer stage ledger."""

    kind: StageKind
    status: StageStatus


def _exit_code_for(
    status: InitializerStatus,
    failure_kind: FailureKind | None,
) -> int:
    match status:
        case InitializerStatus.READY:
            return int(ExitCode.READY)
        case InitializerStatus.PENDING_AUTH:
            return int(ExitCode.PENDING_AUTH)
        case InitializerStatus.FAILED:
            if failure_kind is None:
                raise InitializerContractError(reason="FAILED requires a failure kind")
            return int(failure_kind)
        case unreachable:
            assert_never(unreachable)


@dataclass(frozen=True, slots=True)
class InitializerOutcome:
    """Frozen initializer terminal result; exit_code is derived from status."""

    status: InitializerStatus
    auth_state: AuthState
    billable_consent: BillableCallConsent
    stages: tuple[StageRecord, ...]
    failure_kind: FailureKind | None = None
    safe_detail: SafeDetail = _EMPTY_DETAIL
    exit_code: int = field(init=False)

    def __post_init__(self) -> None:
        status = self.status
        if status is InitializerStatus.READY:
            if self.auth_state is AuthState.SKIPPED:
                raise InitializerContractError(
                    reason="READY requires provided auth; skipped auth yields PENDING_AUTH",
                )
            if self.billable_consent is BillableCallConsent.DECLINED:
                raise InitializerContractError(
                    reason="READY requires consented billable call; declined yields PENDING_AUTH",
                )
            if self.failure_kind is not None:
                raise InitializerContractError(
                    reason="READY cannot coexist with a failure kind",
                )
        elif status is InitializerStatus.PENDING_AUTH:
            if self.failure_kind is not None:
                raise InitializerContractError(
                    reason="PENDING_AUTH cannot coexist with a failure kind",
                )
            if (
                self.auth_state is AuthState.PROVIDED
                and self.billable_consent is BillableCallConsent.GIVEN
            ):
                raise InitializerContractError(
                    reason="PENDING_AUTH requires deferred auth or declined consent",
                )
        elif status is InitializerStatus.FAILED:
            if self.failure_kind is None:
                raise InitializerContractError(reason="FAILED requires a failure kind")
        else:
            assert_never(status)

        object.__setattr__(
            self,
            "exit_code",
            _exit_code_for(self.status, self.failure_kind),
        )

    @classmethod
    def ready(
        cls,
        *,
        stages: tuple[StageRecord, ...] = (),
        safe_detail: SafeDetail = _EMPTY_DETAIL,
    ) -> Self:
        return cls(
            status=InitializerStatus.READY,
            auth_state=AuthState.PROVIDED,
            billable_consent=BillableCallConsent.GIVEN,
            stages=stages,
            failure_kind=None,
            safe_detail=safe_detail,
        )

    @classmethod
    def pending_auth(
        cls,
        *,
        stages: tuple[StageRecord, ...] = (),
        auth_state: AuthState = AuthState.SKIPPED,
        billable_consent: BillableCallConsent = BillableCallConsent.DECLINED,
        safe_detail: SafeDetail = _EMPTY_DETAIL,
    ) -> Self:
        return cls(
            status=InitializerStatus.PENDING_AUTH,
            auth_state=auth_state,
            billable_consent=billable_consent,
            stages=stages,
            failure_kind=None,
            safe_detail=safe_detail,
        )

    @classmethod
    def failed(
        cls,
        *,
        failure_kind: FailureKind,
        stages: tuple[StageRecord, ...] = (),
        auth_state: AuthState = AuthState.PROVIDED,
        billable_consent: BillableCallConsent = BillableCallConsent.DECLINED,
        safe_detail: SafeDetail = _EMPTY_DETAIL,
    ) -> Self:
        return cls(
            status=InitializerStatus.FAILED,
            auth_state=auth_state,
            billable_consent=billable_consent,
            stages=stages,
            failure_kind=failure_kind,
            safe_detail=safe_detail,
        )


@runtime_checkable
class DiagnosticClassifier(Protocol):
    """Classify raw SEAM diagnostic exit codes into typed FailureKind values."""

    def classify(self, raw_exit_code: int) -> FailureKind | None: ...


__all__ = [
    "AuthState", "BillableCallConsent", "DiagnosticClassifier",
    "EnvironmentChoice", "EnvironmentKind", "ExitCode", "FailureKind",
    "InitializerContractError", "InitializerFailure", "InitializerOutcome",
    "InitializerStatus", "ModelId", "ProviderId", "ProviderSelection",
    "SafeDetail", "StageKind", "StageRecord", "StageStatus",
]
