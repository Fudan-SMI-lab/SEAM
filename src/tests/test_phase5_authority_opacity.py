from __future__ import annotations

import copy
import inspect
import pickle
from pathlib import Path

import pytest

from core.artifact_store import ArtifactStore
from core import phase5_attempt_authority, phase5_attempt_receipt
from core.phase5_attempt_authority import Phase5AttemptAuthority
from core.phase5_attempt_receipt import (
    AttemptReceiptError,
    AttemptReceiptErrorKind,
    CustomOpGateEvidence,
    CustomOpGateStatus,
    accept_attempt_receipt,
    finalize_attempt_receipt,
)
from core.run_outcome import ReviewOutcome
from tests.phase5_receipt_test_support import authority, review, save_attempt


def _finalized_authority(tmp_path: Path) -> Phase5AttemptAuthority:
    store = ArtifactStore(str(tmp_path), "run-opacity")
    receipt_path = save_attempt(store, tmp_path, exit_code=0)
    _ = finalize_attempt_receipt(
        receipt_path,
        custom_op_gate=CustomOpGateEvidence(status=CustomOpGateStatus.INACTIVE),
        review=review(ReviewOutcome.DISABLED),
    )
    return authority(store, receipt_path)


def test_phase5_authority_constructor_is_unusable() -> None:
    assert tuple(inspect.signature(Phase5AttemptAuthority).parameters) == ()
    with pytest.raises(TypeError, match="issued by Phase5ArtifactStore"):
        _ = Phase5AttemptAuthority()


def test_receipt_api_does_not_expose_authority_mint() -> None:
    assert not hasattr(phase5_attempt_receipt, "phase5_attempt_authority")


@pytest.mark.parametrize(
    "name",
    [
        "_AuthorityMint",
        "_AUTHORITY_MINT",
        "_phase5_attempt_authority",
        "advance_finalized_authority",
    ],
)
def test_phase5_module_exposes_no_mint_primitive(name: str) -> None:
    assert not hasattr(phase5_attempt_authority, name)


def test_reconstructed_phase5_authority_cannot_accept_receipt(
    tmp_path: Path,
) -> None:
    store = ArtifactStore(str(tmp_path), "run-opacity")
    receipt_path = save_attempt(store, tmp_path, exit_code=0)
    _ = finalize_attempt_receipt(
        receipt_path,
        custom_op_gate=CustomOpGateEvidence(status=CustomOpGateStatus.INACTIVE),
        review=review(ReviewOutcome.DISABLED),
    )
    original = authority(store, receipt_path)
    store.record_finalized_phase5_authority(
        str(receipt_path), phase5_attempt_receipt.load_attempt_receipt(receipt_path)
    )
    original = authority(store, receipt_path)
    reconstructed = object.__new__(Phase5AttemptAuthority)
    for slot in Phase5AttemptAuthority.__slots__:
        object.__setattr__(reconstructed, slot, getattr(original, slot))

    with pytest.raises(AttemptReceiptError) as rejected:
        _ = accept_attempt_receipt(receipt_path, reconstructed)

    assert rejected.value.kind is AttemptReceiptErrorKind.IDENTITY_MISMATCH


def test_phase5_authority_cannot_be_shallow_copied(tmp_path: Path) -> None:
    capability = _finalized_authority(tmp_path)

    with pytest.raises(TypeError):
        _ = copy.copy(capability)


def test_phase5_authority_cannot_be_deep_copied(tmp_path: Path) -> None:
    capability = _finalized_authority(tmp_path)

    with pytest.raises(TypeError):
        _ = copy.deepcopy(capability)


def test_phase5_authority_cannot_be_pickled(tmp_path: Path) -> None:
    capability = _finalized_authority(tmp_path)

    with pytest.raises(TypeError):
        _ = pickle.dumps(capability)
