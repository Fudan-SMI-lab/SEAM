from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum, unique
from pathlib import Path
from typing import ClassVar, Literal, NewType, final

from pydantic import BaseModel, ConfigDict, PositiveInt, StrictInt
from pydantic import field_validator, model_validator
from pydantic_core import PydanticCustomError
from typing_extensions import Self, assert_never, override

from core.run_outcome import ReviewOutcome

Phase5AttemptId = NewType("Phase5AttemptId", str)
Sha256Digest = NewType("Sha256Digest", str)
_ATTEMPT_ID = re.compile(r"phase_5_validation-attempt-[1-9][0-9]*\Z")
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_NONCE = re.compile(r"[0-9a-f]{32}\Z")


class _FrozenModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")


@unique
class BackendKind(str, Enum):
    LOCAL = "local"
    CONTAINER = "container"


@unique
class CustomOpGateStatus(str, Enum):
    NOT_RUN = "not_run"
    INACTIVE = "inactive"
    PASSED = "passed"
    FAILED = "failed"


@unique
class AttemptReceiptErrorKind(str, Enum):
    MISSING = "missing"
    MALFORMED = "malformed"
    IDENTITY_MISMATCH = "identity_mismatch"
    NOT_ACCEPTABLE = "not_acceptable"
    INTEGRITY_MISMATCH = "integrity_mismatch"
    STALE_TRANSITION = "stale_transition"
    UNSAFE_PATH = "unsafe_path"


@final
class AttemptReceiptError(Exception):
    __slots__: tuple[str, ...] = ("kind", "detail")
    kind: AttemptReceiptErrorKind
    detail: str

    def __init__(self, kind: AttemptReceiptErrorKind, detail: str) -> None:
        super().__init__(kind, detail)
        self.kind = kind
        self.detail = detail

    @override
    def __str__(self) -> str:
        return f"{self.kind.value}: {self.detail}"


class EnvironmentVariable(_FrozenModel):
    name: str
    value: str

    @field_validator("name")
    @classmethod
    def require_shell_name(cls, value: str) -> str:
        if _ENV_NAME.fullmatch(value) is None:
            raise PydanticCustomError("invalid_environment_name", "invalid name")
        return value


class ShellInvocation(_FrozenModel):
    argv: tuple[str, ...]
    environment_delta: tuple[EnvironmentVariable, ...] = ()

    @model_validator(mode="after")
    def require_complete_invocation(self) -> Self:
        if not self.argv or any(not token for token in self.argv):
            raise PydanticCustomError("invalid_argv", "argv requires non-empty tokens")
        names = tuple(variable.name for variable in self.environment_delta)
        if len(names) != len(set(names)):
            raise PydanticCustomError("duplicate_environment", "duplicate env name")
        return self


class BackendExecution(_FrozenModel):
    kind: BackendKind
    namespace: str
    host_cwd: str
    backend_cwd: str
    runtime: str | None = None
    container_id: str | None = None
    container_retained: bool = False

    @model_validator(mode="after")
    def require_exact_backend_identity(self) -> Self:
        match self.kind:
            case BackendKind.LOCAL:
                valid = (
                    self.namespace == "host"
                    and self.runtime is None
                    and self.container_id is None
                    and not self.container_retained
                )
            case BackendKind.CONTAINER:
                valid = (
                    bool(self.runtime)
                    and bool(self.container_id)
                    and self.namespace == f"container:{self.container_id}"
                )
            case unreachable:
                assert_never(unreachable)
        if not valid or not self.host_cwd or not self.backend_cwd:
            raise PydanticCustomError("invalid_backend", "incomplete backend identity")
        return self


class ArtifactFileReceipt(_FrozenModel):
    path: str
    sha256: Sha256Digest
    size_bytes: int
    complete: bool

    @model_validator(mode="after")
    def require_artifact_metadata(self) -> Self:
        if not Path(self.path).is_absolute() or self.size_bytes < 0:
            raise PydanticCustomError("invalid_artifact", "invalid artifact metadata")
        if re.fullmatch(r"[0-9a-f]{64}", self.sha256) is None:
            raise PydanticCustomError("invalid_digest", "sha256 must be lowercase hex")
        return self


class ShellArtifactsReceipt(_FrozenModel):
    stdout: ArtifactFileReceipt
    stderr: ArtifactFileReceipt
    metadata: ArtifactFileReceipt


class CustomOpGateEvidence(_FrozenModel):
    status: CustomOpGateStatus
    report: ArtifactFileReceipt | None = None
    errors: tuple[str, ...] = ()

    @model_validator(mode="after")
    def require_passed_report_authority(self) -> Self:
        if self.status is CustomOpGateStatus.PASSED and (
            self.report is None or not self.report.complete
        ):
            raise PydanticCustomError(
                "missing_gate_report", "passed custom-op gate requires hashed report"
            )
        if (
            self.status
            in {
                CustomOpGateStatus.INACTIVE,
                CustomOpGateStatus.NOT_RUN,
            }
            and self.report is not None
        ):
            raise PydanticCustomError(
                "unexpected_gate_report", "inactive gate cannot claim report"
            )
        return self


class ReviewAcceptanceEvidence(_FrozenModel):
    enabled: bool
    outcome: ReviewOutcome

    @model_validator(mode="after")
    def require_consistent_policy(self) -> Self:
        if self.enabled == (self.outcome is ReviewOutcome.DISABLED):
            raise PydanticCustomError("invalid_review_evidence", "policy mismatch")
        return self


class Phase5AttemptReceipt(_FrozenModel):
    schema_name: Literal["seam.phase5-attempt-receipt"] = "seam.phase5-attempt-receipt"
    schema_version: Literal[1] = 1
    run_id: str
    reservation_nonce: str
    attempt_id: Phase5AttemptId
    attempt_number: PositiveInt
    invocation: ShellInvocation
    backend: BackendExecution
    artifacts: ShellArtifactsReceipt
    shell_exit_code: StrictInt
    custom_op_gate: CustomOpGateEvidence
    review: ReviewAcceptanceEvidence
    complete: bool
    accepted: bool

    @model_validator(mode="after")
    def require_valid_authority(self) -> Self:
        if not self.run_id or len(self.run_id) > 128:
            raise PydanticCustomError("invalid_run_id", "invalid run ID")
        if _NONCE.fullmatch(self.reservation_nonce) is None:
            raise PydanticCustomError("invalid_nonce", "invalid reservation nonce")
        if _ATTEMPT_ID.fullmatch(self.attempt_id) is None:
            raise PydanticCustomError("invalid_attempt_id", "invalid attempt ID")
        suffix = int(self.attempt_id.rsplit("-", 1)[-1])
        if suffix != self.attempt_number:
            raise PydanticCustomError("attempt_mismatch", "attempt number mismatch")
        expected_prefix = f"run_entry_script_attempt{self.attempt_number:04d}"
        artifact_names = (
            Path(self.artifacts.stdout.path).name,
            Path(self.artifacts.stderr.path).name,
            Path(self.artifacts.metadata.path).name,
        )
        expected_names = (
            f"{expected_prefix}.stdout.log",
            f"{expected_prefix}.stderr.log",
            f"{expected_prefix}.meta.json",
        )
        if artifact_names != expected_names:
            raise PydanticCustomError(
                "artifact_identity_mismatch", "artifact names do not match attempt"
            )
        artifact_parents = {
            str(Path(self.artifacts.stdout.path).parent),
            str(Path(self.artifacts.stderr.path).parent),
            str(Path(self.artifacts.metadata.path).parent),
        }
        if len(artifact_parents) != 1:
            raise PydanticCustomError(
                "artifact_root_mismatch", "shell artifacts require one root"
            )
        if self.accepted and not is_attempt_acceptable(self):
            raise PydanticCustomError("invalid_acceptance", "acceptance gates failed")
        return self


class Phase5ReservationMarker(_FrozenModel):
    run_id: str
    attempt_id: Phase5AttemptId
    reservation_nonce: str

    @model_validator(mode="after")
    def require_valid_marker(self) -> Self:
        if not self.run_id or len(self.run_id) > 128:
            raise PydanticCustomError("invalid_run_id", "invalid run ID")
        if _ATTEMPT_ID.fullmatch(self.attempt_id) is None:
            raise PydanticCustomError("invalid_attempt_id", "invalid attempt ID")
        if _NONCE.fullmatch(self.reservation_nonce) is None:
            raise PydanticCustomError("invalid_nonce", "invalid reservation nonce")
        return self


@dataclass(frozen=True, slots=True)
class Phase5AttemptReservation:
    run_id: str
    reservation_nonce: str
    attempt_id: Phase5AttemptId
    attempt_number: int
    prefix: str
    marker_path: str
    receipt_path: str


@dataclass(frozen=True, slots=True)
class ShellAttemptExecution:
    reservation: Phase5AttemptReservation
    invocation: ShellInvocation
    backend: BackendExecution


def is_attempt_acceptable(receipt: Phase5AttemptReceipt) -> bool:
    gate_ok = receipt.custom_op_gate.status in {
        CustomOpGateStatus.INACTIVE,
        CustomOpGateStatus.PASSED,
    }
    review_ok = receipt.review.outcome in {
        ReviewOutcome.DISABLED,
        ReviewOutcome.ACCEPTED,
    }
    artifacts = receipt.artifacts
    artifacts_complete = all(
        item.complete
        for item in (artifacts.stdout, artifacts.stderr, artifacts.metadata)
    )
    return (
        receipt.complete
        and receipt.shell_exit_code == 0
        and gate_ok
        and review_ok
        and artifacts_complete
    )
