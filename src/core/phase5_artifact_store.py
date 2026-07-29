from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path
from typing import final
from pydantic import ValidationError

from core.phase5_artifact_io import (
    copy_file_exclusive,
    rollback_created,
    write_text_exclusive,
)
from core.agent_io_logger import redact_sensitive_text
from core.phase5_artifact_metadata import complete_metadata, sanitized_invocation
from core.phase5_authority_registry import Phase5AuthorityRegistry
from core.phase5_attempt_receipt import (
    AttemptReceiptError,
    AttemptReceiptErrorKind,
    CustomOpGateEvidence,
    CustomOpGateStatus,
    Phase5AttemptId,
    Phase5AttemptAuthority,
    Phase5AttemptReceipt,
    Phase5AttemptReservation,
    ReviewAcceptanceEvidence,
    ShellArtifactsReceipt,
    ShellAttemptExecution,
    sha256_file,
    artifact_file_receipt,
    write_attempt_receipt,
)
from core.run_outcome import ReviewOutcome
from harness.session.opencode_contract import JsonObject


@final
class Phase5ArtifactStore:
    artifact_dir: str
    run_id: str

    def __init__(self, artifact_dir: str, run_id: str) -> None:
        self.artifact_dir = artifact_dir
        self.run_id = run_id
        self._authority_registry = Phase5AuthorityRegistry()

    def _register_authority(
        self,
        path: Path,
        receipt: Phase5AttemptReceipt,
    ) -> Phase5AttemptAuthority:
        return self._authority_registry.register(path, receipt)

    def reserve_attempt(self) -> Phase5AttemptReservation:
        artifact_dir = Path(self.artifact_dir).resolve() / "shell_attempts"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        attempt_number = 1
        while True:
            attempt_id = Phase5AttemptId(f"phase_5_validation-attempt-{attempt_number}")
            reservation_nonce = secrets.token_hex(16)
            marker = artifact_dir / f".{attempt_id}.reserved"
            try:
                descriptor = os.open(
                    marker,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o600,
                )
            except FileExistsError:
                attempt_number += 1
                continue
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                _ = handle.write(
                    json.dumps(
                        {
                            "run_id": self.run_id,
                            "attempt_id": attempt_id,
                            "reservation_nonce": reservation_nonce,
                        },
                        sort_keys=True,
                    )
                )
            prefix = artifact_dir / f"run_entry_script_attempt{attempt_number:04d}"
            return Phase5AttemptReservation(
                run_id=self.run_id,
                reservation_nonce=reservation_nonce,
                attempt_id=attempt_id,
                attempt_number=attempt_number,
                prefix=str(prefix),
                marker_path=str(marker),
                receipt_path=f"{prefix}.receipt.json",
            )

    def save_attempt(
        self,
        phase_id: str,
        *,
        command: str,
        cwd: str | None,
        backend_workdir: str | None,
        exit_code: int,
        duration: float,
        stdout: str | None = None,
        stderr: str | None = None,
        stdout_source_path: str | None = None,
        stderr_source_path: str | None = None,
        execution: ShellAttemptExecution | None = None,
    ) -> JsonObject:
        artifact_dir = os.path.abspath(
            os.path.join(self.artifact_dir, "shell_attempts")
        )
        os.makedirs(artifact_dir, exist_ok=True)
        if execution is not None:
            reservation = execution.reservation
            expected_root = Path(artifact_dir).resolve()
            prefix = Path(reservation.prefix).resolve()
            expected_marker = expected_root / f".{reservation.attempt_id}.reserved"
            if (
                reservation.run_id != self.run_id
                or prefix.parent != expected_root
                or Path(reservation.receipt_path).resolve()
                != Path(f"{prefix}.receipt.json")
                or Path(reservation.marker_path).resolve() != expected_marker
            ):
                raise AttemptReceiptError(
                    AttemptReceiptErrorKind.IDENTITY_MISMATCH,
                    str(reservation.attempt_id),
                )
        if execution is None:
            safe_phase = "".join(
                character if character.isalnum() or character in {"_", "-"} else "_"
                for character in phase_id
            )
            existing = [
                name
                for name in os.listdir(artifact_dir)
                if name.startswith(f"{safe_phase}_attempt")
                and name.endswith(".meta.json")
            ]
            attempt = len(existing) + 1
            prefix = os.path.join(artifact_dir, f"{safe_phase}_attempt{attempt:04d}")
        else:
            attempt = execution.reservation.attempt_number
            prefix = execution.reservation.prefix
        stdout_path = os.path.abspath(prefix + ".stdout.log")
        stderr_path = os.path.abspath(prefix + ".stderr.log")
        meta_path = os.path.abspath(prefix + ".meta.json")

        created: list[Path] = []
        try:
            if stdout_source_path:
                copy_file_exclusive(Path(stdout_source_path), Path(stdout_path))
            else:
                write_text_exclusive(Path(stdout_path), stdout or "")
            created.append(Path(stdout_path))
            if stderr_source_path:
                copy_file_exclusive(Path(stderr_source_path), Path(stderr_path))
            else:
                write_text_exclusive(Path(stderr_path), stderr or "")
            created.append(Path(stderr_path))
        except (OSError, AttemptReceiptError):
            rollback_created(created)
            raise

        try:
            stdout_bytes = os.path.getsize(stdout_path)
            stderr_bytes = os.path.getsize(stderr_path)
            stdout_sha256 = sha256_file(Path(stdout_path))
            stderr_sha256 = sha256_file(Path(stderr_path))
        except (OSError, AttemptReceiptError):
            rollback_created(created)
            raise
        durable_invocation = sanitized_invocation(execution)
        base_metadata: JsonObject = {
            "phase_id": phase_id,
            "attempt": attempt,
            "command": redact_sensitive_text(command),
            "cwd": cwd or "",
            "backend_workdir": backend_workdir or "",
            "exit_code": exit_code,
            "duration": duration,
            "stdout_path": stdout_path,
            "stderr_path": stderr_path,
            "meta_path": meta_path,
            "stdout_bytes": stdout_bytes,
            "stderr_bytes": stderr_bytes,
            "stdout_sha256": stdout_sha256,
            "stderr_sha256": stderr_sha256,
            "stdout_complete": True,
            "stderr_complete": True,
            "complete": True,
            "timestamp": time.time(),
        }
        metadata = complete_metadata(base_metadata, execution, durable_invocation)
        try:
            write_text_exclusive(Path(meta_path), json.dumps(metadata, indent=2))
            created.append(Path(meta_path))
        except (OSError, AttemptReceiptError):
            rollback_created(created)
            raise
        if execution is None:
            return metadata
        assert durable_invocation is not None
        receipt_path = Path(execution.reservation.receipt_path)
        try:
            artifacts = ShellArtifactsReceipt(
                stdout=artifact_file_receipt(Path(stdout_path)),
                stderr=artifact_file_receipt(Path(stderr_path)),
                metadata=artifact_file_receipt(Path(meta_path)),
            )
            draft = Phase5AttemptReceipt(
                run_id=execution.reservation.run_id,
                reservation_nonce=execution.reservation.reservation_nonce,
                attempt_id=execution.reservation.attempt_id,
                attempt_number=execution.reservation.attempt_number,
                invocation=durable_invocation,
                backend=execution.backend,
                artifacts=artifacts,
                shell_exit_code=exit_code,
                custom_op_gate=CustomOpGateEvidence(status=CustomOpGateStatus.NOT_RUN),
                review=ReviewAcceptanceEvidence(
                    enabled=True,
                    outcome=ReviewOutcome.UNKNOWN,
                ),
                complete=False,
                accepted=False,
            )
            write_attempt_receipt(receipt_path, draft)
            created.append(receipt_path)
        except (OSError, AttemptReceiptError, ValidationError):
            receipt_path.unlink(missing_ok=True)
            rollback_created(created)
            raise
        _ = self._register_authority(Path(execution.reservation.receipt_path), draft)
        return metadata

    def authority_for(self, receipt_path: str) -> Phase5AttemptAuthority | None:
        return self._authority_registry.authority_for(receipt_path)

    def authority_for_attempt(
        self,
        attempt_id: str,
    ) -> Phase5AttemptAuthority | None:
        return self._authority_registry.authority_for_attempt(attempt_id)

    def record_finalized_authority(
        self, receipt_path: str, receipt: Phase5AttemptReceipt
    ) -> None:
        self._authority_registry.finalize(receipt_path, receipt)
