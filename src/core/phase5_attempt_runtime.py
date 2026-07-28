from __future__ import annotations

import logging
import shlex
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

from typing_extensions import TypeAlias

from core.artifact_store import ArtifactStore
from core.phase5_attempt_receipt import (
    AttemptReceiptError,
    CustomOpGateEvidence,
    CustomOpGateStatus,
    EnvironmentVariable,
    ReviewAcceptanceEvidence,
    ShellInvocation,
    accept_attempt_receipt,
    artifact_file_receipt,
    finalize_attempt_receipt,
)
from core.review_gate import REVIEW_GATE_STATE_KEY, ReviewGate
from core.run_outcome import ReviewOutcome
from core.v3_outcome_mapping import Phase5Decision, PhaseDisposition

logger = logging.getLogger(__name__)

ReceiptRuntimeValue: TypeAlias = "str | int | float | bool | None | ReviewGate | ReviewOutcome | Mapping[str, ReceiptRuntimeValue] | list[ReceiptRuntimeValue]"


def build_shell_invocation(
    command: str | list[str], environment: Mapping[str, str] | None
) -> ShellInvocation:
    argv = tuple(command) if isinstance(command, list) else tuple(shlex.split(command))
    environment_delta = tuple(
        EnvironmentVariable(name=name, value=value)
        for name, value in (environment or {}).items()
    )
    return ShellInvocation(argv=argv, environment_delta=environment_delta)


def _custom_op_evidence(
    loop_state: Mapping[str, ReceiptRuntimeValue],
    state: Mapping[str, ReceiptRuntimeValue],
) -> CustomOpGateEvidence:
    contract = state.get("phase_3_entry_script")
    custom_op_active = isinstance(contract, Mapping) and any(
        field in contract
        for field in (
            "entry_script_kind",
            "reports_dir",
            "required_report_paths",
            "required_checks",
        )
    )
    gate_value = loop_state.get("custom_op_final_gate")
    gate = gate_value if isinstance(gate_value, Mapping) else {}
    if not custom_op_active:
        status = CustomOpGateStatus.INACTIVE
    elif gate.get("passed") is True:
        status = CustomOpGateStatus.PASSED
    elif gate:
        status = CustomOpGateStatus.FAILED
    else:
        status = CustomOpGateStatus.NOT_RUN
    errors_value = gate.get("errors")
    errors = (
        tuple(str(error) for error in errors_value)
        if isinstance(errors_value, list)
        else ()
    )
    report_path_value = gate.get("path")
    report_path = (
        Path(report_path_value) if isinstance(report_path_value, str) else None
    )
    report = None
    if status is CustomOpGateStatus.PASSED and report_path is not None:
        try:
            report = artifact_file_receipt(report_path)
        except OSError as exc:
            status = CustomOpGateStatus.FAILED
            errors = (*errors, f"custom-op gate report unavailable: {exc}")
    return CustomOpGateEvidence(
        status=status,
        report=report,
        errors=errors,
    )


def _review_evidence(
    loop_state: Mapping[str, ReceiptRuntimeValue],
) -> ReviewAcceptanceEvidence:
    enabled = loop_state.get("review_gate_enabled") is True
    gate_value = loop_state.get(REVIEW_GATE_STATE_KEY)
    gate = gate_value if isinstance(gate_value, ReviewGate) else None
    outcome = (
        gate.outcome
        if enabled and gate is not None and gate.outcome is not None
        else ReviewOutcome.UNKNOWN
        if enabled
        else ReviewOutcome.DISABLED
    )
    return ReviewAcceptanceEvidence(enabled=enabled, outcome=outcome)


def finalize_latest_phase5_receipt(
    loop_state: Mapping[str, ReceiptRuntimeValue],
    state: Mapping[str, ReceiptRuntimeValue],
    artifact_store: ArtifactStore,
) -> None:
    metadata_value = loop_state.get("latest_shell_attempt_artifacts")
    metadata = metadata_value if isinstance(metadata_value, Mapping) else {}
    receipt_path = metadata.get("receipt_path")
    if not isinstance(receipt_path, str) or not receipt_path:
        return
    try:
        finalized = finalize_attempt_receipt(
            Path(receipt_path),
            custom_op_gate=_custom_op_evidence(loop_state, state),
            review=_review_evidence(loop_state),
        )
        artifact_store.record_finalized_phase5_authority(receipt_path, finalized)
    except (AttemptReceiptError, OSError) as exc:
        logger.warning("Phase 5 attempt receipt finalization failed: %s", exc)


def accept_phase5_receipt(
    output: Mapping[str, ReceiptRuntimeValue],
    decision: Phase5Decision,
    artifact_store: ArtifactStore,
) -> Phase5Decision:
    if decision.accepted_attempt_id is None:
        if not decision.validation_succeeded:
            return decision
        if (
            decision.review_outcome is ReviewOutcome.REJECT_EXHAUSTED
            and not decision.review_fail_closed
        ):
            loop_state_value = output.get("loop_state")
            loop_state = (
                loop_state_value if isinstance(loop_state_value, Mapping) else {}
            )
            metadata_value = loop_state.get("latest_shell_attempt_artifacts")
            metadata = metadata_value if isinstance(metadata_value, Mapping) else {}
            receipt_path = metadata.get("receipt_path")
            authority = (
                artifact_store.phase5_attempt_authority(receipt_path)
                if isinstance(receipt_path, str) and receipt_path
                else None
            )
            if authority is not None and authority.finalized_digest is not None:
                return decision
            logger.warning(
                "Compatibility Phase 5 success has no finalized attempt receipt"
            )
            return replace(
                decision,
                validation_succeeded=False,
                parent_disposition=PhaseDisposition.FAILED,
            )
        logger.warning("Successful Phase 5 decision has no accepted attempt receipt")
        return replace(
            decision,
            validation_succeeded=False,
            parent_disposition=PhaseDisposition.FAILED,
        )
    loop_state_value = output.get("loop_state")
    loop_state = loop_state_value if isinstance(loop_state_value, Mapping) else {}
    metadata_value = loop_state.get("latest_shell_attempt_artifacts")
    metadata = metadata_value if isinstance(metadata_value, Mapping) else {}
    receipt_path = metadata.get("receipt_path")
    if not isinstance(receipt_path, str) or not receipt_path:
        logger.warning("Phase 5 attempt acceptance receipt is missing")
        return replace(
            decision,
            validation_succeeded=False,
            accepted_attempt_id=None,
            parent_disposition=PhaseDisposition.FAILED,
        )
    authority = artifact_store.phase5_attempt_authority(receipt_path)
    if authority is None or authority.attempt_id != decision.accepted_attempt_id:
        logger.warning("Phase 5 attempt acceptance authority is missing")
        return replace(
            decision,
            validation_succeeded=False,
            accepted_attempt_id=None,
            parent_disposition=PhaseDisposition.FAILED,
        )
    try:
        _ = accept_attempt_receipt(Path(receipt_path), authority)
    except (AttemptReceiptError, OSError) as exc:
        logger.warning("Phase 5 attempt acceptance receipt failed: %s", exc)
        return replace(
            decision,
            validation_succeeded=False,
            accepted_attempt_id=None,
            parent_disposition=PhaseDisposition.FAILED,
        )
    return decision
