"""Typed outcome-neutral models for direct-run root-manifest sealing.

The direct-run ``--seal-manifest`` path is an *optional side channel*: its
result is independently observable via the ``manifest-sealing.v1.json``
sidecar, but a sealing failure never mutates ``RunOutcome``, the E2E
PASS/FAIL headline, or the pre-sealing exit code.

The models below are pure value types. No I/O lives here; the focused
service in :mod:`core.manifest_sealing` owns all publication and cleanup.
Every result is immutable and every failure detail is redacted and length
bounded at construction so callers cannot leak secrets or unbounded text
through the sidecar boundary.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum, unique
from pathlib import Path
from typing import Final

from core.compat import SLOTS_KWARG, assert_never
from core.secret_redaction import redact_sensitive_text

MANIFEST_SEALING_SCHEMA: Final = "seam.manifest-sealing"
MANIFEST_SEALING_SCHEMA_VERSION: Final = 1
MANIFEST_SEALING_FILENAME: Final = "manifest-sealing.v1.json"

_MAX_ERROR_DETAIL_CHARS: Final = 1024


def _noop() -> None:
    return None


@dataclass(frozen=True, **SLOTS_KWARG)
class ManifestSealingFaultHooks:
    """Optional crash-safety injection points for the focused test suite.

    Production callers pass nothing; the defaults are no-ops. Each hook is
    invoked at the named publication boundary so tests can simulate an
    ``OSError`` without constructing impossible filesystem state.
    """

    before_evidence_publish: Callable[[], None] = field(default=_noop)
    before_manifest_commit: Callable[[], None] = field(default=_noop)
    before_sidecar_publish: Callable[[], None] = field(default=_noop)


class ManifestSealingContractError(ValueError):
    """Raised when manifest sealing result fields describe an impossible state."""


@unique
class ManifestSealingStatus(str, Enum):
    """The three direct-run sealing dispositions."""

    NOT_REQUESTED = "not_requested"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@unique
class ManifestSealingErrorKind(str, Enum):
    """Typed publication-fault categories attached to a failed result."""

    AUTHORITY_ALREADY_PRESENT = "authority_already_present"
    STAGING_FAILED = "staging_failed"
    EVIDENCE_PUBLISH_FAILED = "evidence_publish_failed"
    MANIFEST_PUBLISH_FAILED = "manifest_publish_failed"
    VERIFICATION_FAILED = "verification_failed"
    SIDECAR_PUBLISH_FAILED = "sidecar_publish_failed"
    SEALED_INPUTS_UNAVAILABLE = "sealed_inputs_unavailable"


@dataclass(frozen=True, **SLOTS_KWARG)
class ManifestSealingError:
    """A bounded, redacted publication-fault diagnostic.

    The detail is passed through :func:`redact_sensitive_text` and truncated
    in ``__post_init__`` so the invariant holds regardless of where the
    error is constructed; no caller can smuggle a secret or an unbounded
    traceback into the sidecar.
    """

    kind: ManifestSealingErrorKind
    detail: str

    def __post_init__(self) -> None:
        redacted = redact_sensitive_text(self.detail)
        if len(redacted) > _MAX_ERROR_DETAIL_CHARS:
            redacted = redacted[:_MAX_ERROR_DETAIL_CHARS] + "[truncated]"
        object.__setattr__(self, "detail", redacted)


def _optional_path(value: Path | None) -> str | None:
    return None if value is None else str(value)


@dataclass(frozen=True, **SLOTS_KWARG)
class ManifestSealingResult:
    """The immutable outcome-neutral sealing result.

    On ``succeeded`` the manifest and sealed-evidence directory paths are
    present and the run is continuation-eligible. On ``failed`` the error
    is required and the run is never eligible; the manifest/evidence paths
    may still be present *only* when the authority was fully published and
    verified before a sidecar write failed (the manifest never claims
    incomplete evidence). On ``not_requested`` no paths or error are
    carried and the run is not eligible.
    """

    status: ManifestSealingStatus
    requested: bool
    continuation_eligible: bool
    run_id: str
    sidecar_path: Path
    manifest_path: Path | None = None
    evidence_dir_path: Path | None = None
    error: ManifestSealingError | None = None

    def __post_init__(self) -> None:
        status = self.status
        if status is ManifestSealingStatus.SUCCEEDED:
            self._require_paths_present()
            self._require_no_error()
            if not self.continuation_eligible:
                raise ManifestSealingContractError(
                    "succeeded result must be continuation eligible"
                )
        elif status is ManifestSealingStatus.FAILED:
            if self.error is None:
                raise ManifestSealingContractError("failed result requires an error")
            if self.continuation_eligible:
                raise ManifestSealingContractError(
                    "failed result cannot be continuation eligible"
                )
        elif status is ManifestSealingStatus.NOT_REQUESTED:
            self._require_no_paths()
            self._require_no_error()
            if self.continuation_eligible:
                raise ManifestSealingContractError(
                    "not_requested result cannot be continuation eligible"
                )
            if self.requested:
                raise ManifestSealingContractError(
                    "not_requested result cannot have been requested"
                )
        else:
            assert_never(status)

    def _require_paths_present(self) -> None:
        if self.manifest_path is None or self.evidence_dir_path is None:
            raise ManifestSealingContractError(
                "succeeded result requires manifest and evidence paths"
            )

    def _require_no_paths(self) -> None:
        if self.manifest_path is not None or self.evidence_dir_path is not None:
            raise ManifestSealingContractError(
                "this result cannot claim published paths"
            )

    def _require_no_error(self) -> None:
        if self.error is not None:
            raise ManifestSealingContractError("this result cannot carry an error")

    def to_sidecar_json(self) -> str:
        """Serialize this result to the bounded ``manifest-sealing.v1.json`` body."""
        error_payload: dict[str, str] | None
        if self.error is None:
            error_payload = None
        else:
            error_payload = {"kind": self.error.kind.value, "detail": self.error.detail}
        payload: dict[str, object] = {
            "schema": MANIFEST_SEALING_SCHEMA,
            "schema_version": MANIFEST_SEALING_SCHEMA_VERSION,
            "status": self.status.value,
            "requested": self.requested,
            "continuation_eligible": self.continuation_eligible,
            "run_id": self.run_id,
            "manifest_path": _optional_path(self.manifest_path),
            "evidence_dir_path": _optional_path(self.evidence_dir_path),
            "error": error_payload,
        }
        return json.dumps(payload, indent=2) + "\n"
