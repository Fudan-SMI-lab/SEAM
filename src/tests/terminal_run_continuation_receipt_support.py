from __future__ import annotations

from pathlib import Path

from core.artifact_store import ArtifactStore
from core.phase5_attempt_receipt import (
    CustomOpGateEvidence,
    CustomOpGateStatus,
    ReviewAcceptanceEvidence,
    finalize_attempt_receipt,
)
from core.run_outcome import ReviewOutcome
from tests.phase5_receipt_test_support import authority, save_attempt


def record_accepted_phase5_receipt(working: ArtifactStore, project_dir: Path) -> None:
    receipt_path = save_attempt(working, project_dir, exit_code=0)
    finalized = finalize_attempt_receipt(
        receipt_path,
        custom_op_gate=CustomOpGateEvidence(status=CustomOpGateStatus.INACTIVE),
        review=ReviewAcceptanceEvidence(enabled=False, outcome=ReviewOutcome.DISABLED),
    )
    working.record_finalized_phase5_authority(str(receipt_path), finalized)
    _ = working.accept_phase5_attempt_receipt(
        receipt_path,
        authority(working, receipt_path),
    )
