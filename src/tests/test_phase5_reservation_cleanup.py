from pathlib import Path

from core.artifact_store import ArtifactStore
from core.phase5_attempt_receipt import (
    CustomOpGateEvidence,
    CustomOpGateStatus,
    accept_attempt_receipt,
    finalize_attempt_receipt,
)
from core.run_outcome import ReviewOutcome
from tests.phase5_receipt_test_support import authority, review, save_attempt


def test_acceptance_removes_owned_reservation_marker(tmp_path: Path) -> None:
    store = ArtifactStore(str(tmp_path), "run-cleanup")
    receipt_path = save_attempt(store, tmp_path, exit_code=0)
    receipt = finalize_attempt_receipt(
        receipt_path,
        custom_op_gate=CustomOpGateEvidence(status=CustomOpGateStatus.INACTIVE),
        review=review(ReviewOutcome.DISABLED),
    )
    marker = receipt_path.parent / f".{receipt.attempt_id}.reserved"
    assert marker.is_file()

    _ = accept_attempt_receipt(receipt_path, authority(store, receipt_path))

    assert not marker.exists()
