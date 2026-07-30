from __future__ import annotations

import shlex
from dataclasses import dataclass
from enum import Enum, unique
from pathlib import Path

from typing_extensions import assert_never

from core.phase5_attempt_receipt import (
    ArtifactFileReceipt,
    AttemptReceiptError,
    AttemptReceiptErrorKind,
    BackendKind,
    Phase5AttemptAuthority,
    Phase5AttemptReceipt,
    load_attempt_receipt,
    receipt_matches_authority,
    sha256_file,
)
from core.run_outcome import RunOutcome, TerminalOutcome
from core.secret_redaction import contains_redaction_marker

NONDETERMINISM_NOTICE = (
    "Display only; this invocation reconstructs recorded inputs and does not guarantee "
    "deterministic output. SEAM never executes replay automatically."
)


@unique
class ReplayUnavailableReason(str, Enum):
    RESOURCE_MANIFEST_UNAVAILABLE = "resource_manifest_unavailable"
    RUN_OUTCOME_UNAVAILABLE = "run_outcome_unavailable"
    RUN_NOT_SUCCESSFUL = "run_not_successful"
    ATTEMPT_NOT_ACCEPTED = "attempt_not_accepted"
    INCOMPLETE_RECEIPT = "incomplete_receipt"
    ACCEPTED_ATTEMPT_MISMATCH = "accepted_attempt_mismatch"
    ARTIFACT_MISSING = "artifact_missing"
    ARTIFACT_HASH_MISMATCH = "artifact_hash_mismatch"
    CONTAINER_NOT_RETAINED = "container_not_retained"
    CONTAINER_STATUS_UNKNOWN = "container_status_unknown"
    CONTAINER_UNAVAILABLE = "container_unavailable"
    CONTAINER_IDENTITY_MISMATCH = "container_identity_mismatch"
    RECEIPT_MISSING = "receipt_missing"
    RECEIPT_MALFORMED = "receipt_malformed"
    RUN_ID_MISMATCH = "run_id_mismatch"
    INVOCATION_SANITIZED = "invocation_sanitized"


@dataclass(frozen=True)
class ContainerObservation:
    runtime: str
    container_id: str
    running: bool


@dataclass(frozen=True)
class ReplayReceipt:
    available: bool
    reason: ReplayUnavailableReason | None
    display_command: str
    argv: tuple[str, ...]
    environment_delta: tuple[tuple[str, str], ...]
    cwd: str
    nondeterminism_notice: str = NONDETERMINISM_NOTICE
    auto_execute: bool = False


def _unavailable(reason: ReplayUnavailableReason) -> ReplayReceipt:
    return ReplayReceipt(
        available=False,
        reason=reason,
        display_command="",
        argv=(),
        environment_delta=(),
        cwd="",
    )


def _verify_artifact(
    artifact: ArtifactFileReceipt,
) -> ReplayUnavailableReason | None:
    path = Path(artifact.path)
    if not artifact.complete or not path.is_file():
        return ReplayUnavailableReason.ARTIFACT_MISSING
    try:
        matches = (
            path.stat().st_size == artifact.size_bytes
            and sha256_file(path) == artifact.sha256
        )
    except (OSError, AttemptReceiptError):
        return ReplayUnavailableReason.ARTIFACT_HASH_MISMATCH
    if not matches:
        return ReplayUnavailableReason.ARTIFACT_HASH_MISMATCH
    return None


def _artifact_failure(
    receipt: Phase5AttemptReceipt,
) -> ReplayUnavailableReason | None:
    for artifact in (
        receipt.artifacts.stdout,
        receipt.artifacts.stderr,
        receipt.artifacts.metadata,
        receipt.custom_op_gate.report,
    ):
        if artifact is None:
            continue
        failure = _verify_artifact(artifact)
        if failure is not None:
            return failure
    return None


def _render_local(receipt: Phase5AttemptReceipt) -> str:
    environment = tuple(
        f"{variable.name}={variable.value}"
        for variable in receipt.invocation.environment_delta
    )
    invocation = ("env", *environment, *receipt.invocation.argv)
    return (
        f"cd -- {shlex.quote(receipt.backend.backend_cwd)} && {shlex.join(invocation)}"
    )


def _render_container(receipt: Phase5AttemptReceipt) -> str:
    runtime = receipt.backend.runtime
    container_id = receipt.backend.container_id
    if runtime is None or container_id is None:
        return ""
    command = [runtime, "exec", "-i", "-w", receipt.backend.backend_cwd]
    for variable in receipt.invocation.environment_delta:
        command.extend(["-e", f"{variable.name}={variable.value}"])
    command.extend([container_id, *receipt.invocation.argv])
    return shlex.join(command)


def render_replay(
    receipt: Phase5AttemptReceipt,
    outcome: RunOutcome,
    *,
    expected_run_id: str,
    authority: Phase5AttemptAuthority,
    container_observation: ContainerObservation | None = None,
) -> ReplayReceipt:
    if outcome.terminal_outcome is not TerminalOutcome.PASSED:
        return _unavailable(ReplayUnavailableReason.RUN_NOT_SUCCESSFUL)
    if not receipt.accepted:
        return _unavailable(ReplayUnavailableReason.ATTEMPT_NOT_ACCEPTED)
    if not receipt.complete:
        return _unavailable(ReplayUnavailableReason.INCOMPLETE_RECEIPT)
    if outcome.accepted_attempt_id != receipt.attempt_id:
        return _unavailable(ReplayUnavailableReason.ACCEPTED_ATTEMPT_MISMATCH)
    if receipt.run_id != expected_run_id:
        return _unavailable(ReplayUnavailableReason.RUN_ID_MISMATCH)
    if not receipt_matches_authority(receipt, authority):
        return _unavailable(ReplayUnavailableReason.RECEIPT_MALFORMED)
    if any(contains_redaction_marker(argument) for argument in receipt.invocation.argv):
        return _unavailable(ReplayUnavailableReason.INVOCATION_SANITIZED)
    if any(
        contains_redaction_marker(variable.value)
        for variable in receipt.invocation.environment_delta
    ):
        return _unavailable(ReplayUnavailableReason.INVOCATION_SANITIZED)
    artifact_failure = _artifact_failure(receipt)
    if artifact_failure is not None:
        return _unavailable(artifact_failure)

    if receipt.backend.kind is BackendKind.LOCAL:
        display_command = _render_local(receipt)
    elif receipt.backend.kind is BackendKind.CONTAINER:
        if not receipt.backend.container_retained:
            return _unavailable(ReplayUnavailableReason.CONTAINER_NOT_RETAINED)
        if container_observation is None:
            return _unavailable(ReplayUnavailableReason.CONTAINER_STATUS_UNKNOWN)
        if (
            container_observation.runtime != receipt.backend.runtime
            or container_observation.container_id != receipt.backend.container_id
        ):
            return _unavailable(ReplayUnavailableReason.CONTAINER_IDENTITY_MISMATCH)
        if not container_observation.running:
            return _unavailable(ReplayUnavailableReason.CONTAINER_UNAVAILABLE)
        display_command = _render_container(receipt)
    else:
        assert_never(receipt.backend.kind)

    return ReplayReceipt(
        available=True,
        reason=None,
        display_command=display_command,
        argv=receipt.invocation.argv,
        environment_delta=tuple(
            (variable.name, variable.value)
            for variable in receipt.invocation.environment_delta
        ),
        cwd=receipt.backend.backend_cwd,
    )


def render_replay_from_path(
    receipt_path: Path,
    outcome: RunOutcome,
    *,
    expected_run_id: str,
    authority: Phase5AttemptAuthority,
    container_observation: ContainerObservation | None = None,
) -> ReplayReceipt:
    if str(receipt_path.resolve()) != authority.receipt_path:
        return _unavailable(ReplayUnavailableReason.RECEIPT_MALFORMED)
    try:
        receipt = load_attempt_receipt(receipt_path)
    except AttemptReceiptError as exc:
        reason = (
            ReplayUnavailableReason.RECEIPT_MISSING
            if exc.kind is AttemptReceiptErrorKind.MISSING
            else ReplayUnavailableReason.RECEIPT_MALFORMED
        )
        return _unavailable(reason)
    return render_replay(
        receipt,
        outcome,
        expected_run_id=expected_run_id,
        authority=authority,
        container_observation=container_observation,
    )
