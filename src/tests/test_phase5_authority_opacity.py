from __future__ import annotations

import copy
import gc
import inspect
import os
import pickle
import subprocess
import sys
from pathlib import Path

import pytest

from core import phase5_attempt_authority, phase5_attempt_receipt
from core.artifact_store import ArtifactStore
from core.phase5_attempt_authority import Phase5AttemptAuthority
from core.phase5_attempt_receipt import (
    AttemptReceiptError,
    AttemptReceiptErrorKind,
    CustomOpGateEvidence,
    CustomOpGateStatus,
    accept_attempt_receipt,
    finalize_attempt_receipt,
)
from core.phase5_authority_registry import Phase5AuthorityRegistry
from core.run_outcome import ReviewOutcome
from tests.authority_boundary_attack_support import reassign_phase5_receipt_owner
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


def test_phase5_registration_does_not_accept_owner_selection() -> None:
    assert tuple(inspect.signature(Phase5AuthorityRegistry).parameters) == ()
    assert tuple(inspect.signature(Phase5AuthorityRegistry.register).parameters) == (
        "self",
        "path",
        "receipt",
    )


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


def test_foreign_registry_cannot_mint_authority_accepted_by_real_sink(
    tmp_path: Path,
) -> None:
    external_module = tmp_path / "foreign_phase5_registry.py"
    _ = external_module.write_text(
        """from pathlib import Path
import sys

from core.artifact_store import ArtifactStore
from core.phase5_authority_registry import Phase5AuthorityRegistry
from core.phase5_attempt_receipt import (
    AttemptReceiptError,
    CustomOpGateEvidence,
    CustomOpGateStatus,
    finalize_attempt_receipt,
    load_attempt_receipt,
)
from core.run_outcome import ReviewOutcome
from tests.phase5_receipt_test_support import review, save_attempt

root = Path(sys.argv[1])
store = ArtifactStore(str(root), "run-foreign-registry")
receipt_path = save_attempt(store, root, exit_code=0)
finalize_attempt_receipt(
    receipt_path,
    custom_op_gate=CustomOpGateEvidence(status=CustomOpGateStatus.INACTIVE),
    review=review(ReviewOutcome.DISABLED),
)
foreign_registry = Phase5AuthorityRegistry()
foreign_authority = foreign_registry.register(
    receipt_path,
    load_attempt_receipt(receipt_path),
)
try:
    store.accept_phase5_attempt_receipt(receipt_path, foreign_authority)
except AttemptReceiptError:
    raise SystemExit(0)
raise SystemExit(1)
""",
        encoding="utf-8",
    )

    completed = subprocess.run(
        [sys.executable, str(external_module), str(tmp_path / "receipt-root")],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        env={
            **os.environ,
            "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
        },
        text=True,
    )

    assert completed.returncode == 0, completed.stderr


def test_foreign_registry_cannot_bypass_sink_through_receipt_api(
    tmp_path: Path,
) -> None:
    # Given
    store = ArtifactStore(str(tmp_path), "run-free-function-bypass")
    receipt_path = save_attempt(store, tmp_path, exit_code=0)
    finalized = finalize_attempt_receipt(
        receipt_path,
        custom_op_gate=CustomOpGateEvidence(status=CustomOpGateStatus.INACTIVE),
        review=review(ReviewOutcome.DISABLED),
    )
    foreign_authority = Phase5AuthorityRegistry().register(receipt_path, finalized)

    # When
    with pytest.raises(AttemptReceiptError) as rejected:
        _ = accept_attempt_receipt(receipt_path, foreign_authority)

    # Then
    assert rejected.value.kind is AttemptReceiptErrorKind.IDENTITY_MISMATCH


def test_foreign_registry_cannot_replace_released_store_issuer(
    tmp_path: Path,
) -> None:
    # Given
    store = ArtifactStore(str(tmp_path), "run-released-issuer")
    receipt_path = save_attempt(store, tmp_path, exit_code=0)
    finalized = finalize_attempt_receipt(
        receipt_path,
        custom_op_gate=CustomOpGateEvidence(status=CustomOpGateStatus.INACTIVE),
        review=review(ReviewOutcome.DISABLED),
    )
    del store
    _ = gc.collect()
    foreign_authority = Phase5AuthorityRegistry().register(receipt_path, finalized)

    # When
    with pytest.raises(AttemptReceiptError) as rejected:
        _ = accept_attempt_receipt(receipt_path, foreign_authority)

    # Then
    assert rejected.value.kind is AttemptReceiptErrorKind.IDENTITY_MISMATCH


def test_finalize_preserves_store_issuer_through_receipt_acceptance(
    tmp_path: Path,
) -> None:
    # Given
    store = ArtifactStore(str(tmp_path), "run-issuer-preservation")
    receipt_path = save_attempt(store, tmp_path, exit_code=0)
    finalized = finalize_attempt_receipt(
        receipt_path,
        custom_op_gate=CustomOpGateEvidence(status=CustomOpGateStatus.INACTIVE),
        review=review(ReviewOutcome.DISABLED),
    )

    # When
    store.record_finalized_phase5_authority(str(receipt_path), finalized)
    finalized_authority = authority(store, receipt_path)
    accepted = accept_attempt_receipt(receipt_path, finalized_authority)

    # Then
    assert accepted.accepted is True


def test_imported_phase5_owner_state_cannot_reassign_receipt_issuer(
    tmp_path: Path,
) -> None:
    # Given a finalized receipt and a foreign registry authority for its path.
    store = ArtifactStore(str(tmp_path), "run-owner-reassignment")
    receipt_path = save_attempt(store, tmp_path, exit_code=0)
    finalized = finalize_attempt_receipt(
        receipt_path,
        custom_op_gate=CustomOpGateEvidence(status=CustomOpGateStatus.INACTIVE),
        review=review(ReviewOutcome.DISABLED),
    )
    foreign_registry = Phase5AuthorityRegistry()
    foreign_authority = foreign_registry.register(receipt_path, finalized)
    reassign_phase5_receipt_owner(receipt_path, foreign_registry)

    # When the reassigned authority reaches the real receipt boundary.
    with pytest.raises(AttemptReceiptError) as rejected:
        _ = accept_attempt_receipt(receipt_path, foreign_authority)

    # Then caller-addressable owner state grants no acceptance authority.
    assert rejected.value.kind is AttemptReceiptErrorKind.IDENTITY_MISMATCH
