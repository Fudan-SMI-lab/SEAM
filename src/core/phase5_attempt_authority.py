from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from pathlib import Path

from core.phase5_attempt_models import (
    Phase5AttemptId,
    Phase5AttemptReceipt,
    Sha256Digest,
)


@dataclass(frozen=True, slots=True)
class Phase5AttemptAuthority:
    run_id: str
    attempt_id: Phase5AttemptId
    reservation_nonce: str
    receipt_path: str
    immutable_digest: Sha256Digest
    finalized_digest: Sha256Digest | None = None


def immutable_receipt_digest(receipt: Phase5AttemptReceipt) -> Sha256Digest:
    payload = json.dumps(
        {
            "run_id": receipt.run_id,
            "reservation_nonce": receipt.reservation_nonce,
            "attempt_id": receipt.attempt_id,
            "attempt_number": receipt.attempt_number,
            "invocation": receipt.invocation.model_dump(mode="json"),
            "backend": receipt.backend.model_dump(mode="json"),
            "artifacts": receipt.artifacts.model_dump(mode="json"),
            "shell_exit_code": receipt.shell_exit_code,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return Sha256Digest(hashlib.sha256(payload).hexdigest())


def finalized_receipt_digest(receipt: Phase5AttemptReceipt) -> Sha256Digest:
    payload = json.dumps(
        {
            "immutable_digest": immutable_receipt_digest(receipt),
            "custom_op_gate": receipt.custom_op_gate.model_dump(mode="json"),
            "review": receipt.review.model_dump(mode="json"),
            "complete": receipt.complete,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return Sha256Digest(hashlib.sha256(payload).hexdigest())


def phase5_attempt_authority(
    path: Path, receipt: Phase5AttemptReceipt
) -> Phase5AttemptAuthority:
    return Phase5AttemptAuthority(
        run_id=receipt.run_id,
        attempt_id=receipt.attempt_id,
        reservation_nonce=receipt.reservation_nonce,
        receipt_path=str(path.resolve()),
        immutable_digest=immutable_receipt_digest(receipt),
        finalized_digest=(
            finalized_receipt_digest(receipt) if receipt.complete else None
        ),
    )


def receipt_matches_authority(
    receipt: Phase5AttemptReceipt, authority: Phase5AttemptAuthority
) -> bool:
    return (
        receipt.run_id == authority.run_id
        and receipt.attempt_id == authority.attempt_id
        and receipt.reservation_nonce == authority.reservation_nonce
        and immutable_receipt_digest(receipt) == authority.immutable_digest
        and authority.finalized_digest is not None
        and finalized_receipt_digest(receipt) == authority.finalized_digest
    )


def advance_finalized_authority(
    path: Path,
    receipt: Phase5AttemptReceipt,
    authority: Phase5AttemptAuthority | None,
) -> Phase5AttemptAuthority | None:
    finalized = phase5_attempt_authority(path, receipt)
    if (
        authority is None
        or finalized.immutable_digest != authority.immutable_digest
        or finalized.finalized_digest is None
    ):
        return None
    return replace(authority, finalized_digest=finalized.finalized_digest)
