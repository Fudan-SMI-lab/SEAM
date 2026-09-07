from __future__ import annotations

import json
import secrets
from pathlib import Path
from typing import final

from pydantic import ValidationError

from core.atomic_file import atomic_create_bytes
from core.continuation_lock_identity import fsync_parent, read_verified_bytes
from core.phase5_attempt_models import (
    AttemptReceiptError,
    AttemptReceiptErrorKind,
    Phase5AttemptId,
    Phase5AttemptReservation,
    Phase5ReservationMarker,
)


@final
class Phase5AttemptAllocator:
    def __init__(self, artifact_dir: str, run_id: str) -> None:
        self._artifact_dir = artifact_dir
        self._run_id = run_id

    def reserve_attempt(self) -> Phase5AttemptReservation:
        artifact_dir = Path(self._artifact_dir).resolve() / "shell_attempts"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        fsync_parent(artifact_dir)
        attempt_number = 1
        while True:
            attempt_id = Phase5AttemptId(f"phase_5_validation-attempt-{attempt_number}")
            reservation_nonce = secrets.token_hex(16)
            marker = artifact_dir / f".{attempt_id}.reserved"
            marker_content = json.dumps(
                {
                    "run_id": self._run_id,
                    "attempt_id": attempt_id,
                    "reservation_nonce": reservation_nonce,
                },
                sort_keys=True,
            ).encode("utf-8")
            try:
                atomic_create_bytes(marker, marker_content)
            except FileExistsError:
                attempt_number += 1
                continue
            prefix = artifact_dir / f"run_entry_script_attempt{attempt_number:04d}"
            return Phase5AttemptReservation(
                run_id=self._run_id,
                reservation_nonce=reservation_nonce,
                attempt_id=attempt_id,
                attempt_number=attempt_number,
                prefix=str(prefix),
                marker_path=str(marker),
                receipt_path=f"{prefix}.receipt.json",
            )


def require_current_reservation(
    reservation: Phase5AttemptReservation,
    run_id: str,
    expected_root: Path,
) -> None:
    prefix = Path(reservation.prefix).resolve()
    expected_marker = expected_root / f".{reservation.attempt_id}.reserved"
    if (
        reservation.run_id != run_id
        or prefix.parent != expected_root
        or Path(reservation.receipt_path).resolve() != Path(f"{prefix}.receipt.json")
        or Path(reservation.marker_path).resolve() != expected_marker
    ):
        raise AttemptReceiptError(
            AttemptReceiptErrorKind.IDENTITY_MISMATCH,
            str(reservation.attempt_id),
        )
    try:
        marker = Phase5ReservationMarker.model_validate_json(
            read_verified_bytes(expected_marker, 4096)
        )
    except (OSError, ValidationError) as exc:
        raise AttemptReceiptError(
            AttemptReceiptErrorKind.IDENTITY_MISMATCH,
            str(reservation.attempt_id),
        ) from exc
    if (
        marker.run_id != reservation.run_id
        or marker.attempt_id != reservation.attempt_id
        or marker.reservation_nonce != reservation.reservation_nonce
    ):
        raise AttemptReceiptError(
            AttemptReceiptErrorKind.IDENTITY_MISMATCH,
            str(reservation.attempt_id),
        )
