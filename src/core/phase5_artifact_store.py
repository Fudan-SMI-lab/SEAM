from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import BinaryIO, final

from pydantic import ValidationError

from core.phase5_artifact_io import (
    WrittenArtifact,
    artifact_receipt,
    read_bounded_descriptor,
    rollback_created,
    sha256_content as _sha256_content,
    write_text_exclusive,
)
from core.continuation_lock_identity import fsync_parent
from core.phase5_attempt_allocator import (
    Phase5AttemptAllocator,
    require_current_reservation,
)
from core.evidence_limits import MAX_EVIDENCE_FILE_BYTES
from core.phase5_artifact_metadata import complete_metadata, sanitized_invocation
from core.phase5_attempt_models import ShellArtifactsReceipt
from core.phase5_attempt_receipt import (
    AttemptReceiptError,
    AttemptReceiptErrorKind,
    Phase5AttemptAuthority,
    Phase5AttemptReceipt,
    Phase5AttemptReservation,
    ShellAttemptExecution,
    accept_attempt_receipt,
)
from core.phase5_attempt_receipt_persistence import (
    draft_attempt_receipt,
    write_attempt_receipt,
)
from core.phase5_authority_registry import Phase5AuthorityRegistry
from core.run_manifest_paths import read_real_file
from core.secret_redaction import redact_sensitive_text
from core.phase5_transaction import Phase5Transaction
from harness.session.opencode_contract import JsonObject


@final
class Phase5ArtifactStore:
    artifact_dir: str
    run_id: str

    def __init__(
        self,
        artifact_dir: str,
        run_id: str,
        transaction: Phase5Transaction,
    ) -> None:
        self.artifact_dir = artifact_dir
        self.run_id = run_id
        self._attempt_allocator = Phase5AttemptAllocator(artifact_dir, run_id)
        self._authority_registry = Phase5AuthorityRegistry()
        self._transaction = transaction

    def reserve_attempt(self) -> Phase5AttemptReservation:
        return self._attempt_allocator.reserve_attempt()

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
        stdout_source: BinaryIO | None = None,
        stderr_source: BinaryIO | None = None,
        execution: ShellAttemptExecution | None = None,
    ) -> JsonObject:
        artifact_dir = os.path.abspath(
            os.path.join(self.artifact_dir, "shell_attempts")
        )
        os.makedirs(artifact_dir, exist_ok=True)
        fsync_parent(Path(artifact_dir))
        if execution is not None:
            reservation = execution.reservation
            expected_root = Path(artifact_dir).resolve()
            require_current_reservation(reservation, self.run_id, expected_root)
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

        workspace_root = Path(self.artifact_dir).resolve(strict=True).parents[1]
        created: list[WrittenArtifact] = []
        try:
            if stdout_source is not None:
                stdout = read_bounded_descriptor(
                    stdout_source, MAX_EVIDENCE_FILE_BYTES
                ).decode("utf-8", errors="replace")
            elif stdout_source_path:
                _, stdout_bytes = read_real_file(
                    Path(stdout_source_path),
                    workspace_root,
                    MAX_EVIDENCE_FILE_BYTES,
                )
                stdout = stdout_bytes.decode("utf-8", errors="replace")
            stdout_artifact = write_text_exclusive(
                Path(stdout_path), redact_sensitive_text(stdout or "")
            )
            created.append(stdout_artifact)
            if stderr_source is not None:
                stderr = read_bounded_descriptor(
                    stderr_source, MAX_EVIDENCE_FILE_BYTES
                ).decode("utf-8", errors="replace")
            elif stderr_source_path:
                _, stderr_bytes = read_real_file(
                    Path(stderr_source_path),
                    workspace_root,
                    MAX_EVIDENCE_FILE_BYTES,
                )
                stderr = stderr_bytes.decode("utf-8", errors="replace")
            stderr_artifact = write_text_exclusive(
                Path(stderr_path), redact_sensitive_text(stderr or "")
            )
            created.append(stderr_artifact)
        except (OSError, AttemptReceiptError) as exc:
            _rollback_after_failure(created, exc)
            raise

        try:
            stdout_bytes = len(stdout_artifact.content)
            stderr_bytes = len(stderr_artifact.content)
            stdout_sha256 = _sha256_content(stdout_artifact.content)
            stderr_sha256 = _sha256_content(stderr_artifact.content)
        except OSError as exc:
            _rollback_after_failure(created, exc)
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
            metadata_artifact = write_text_exclusive(
                Path(meta_path), json.dumps(metadata, indent=2)
            )
            created.append(metadata_artifact)
        except (OSError, AttemptReceiptError) as exc:
            _rollback_after_failure(created, exc)
            raise
        if execution is None:
            try:
                fsync_parent(metadata_artifact.path)
            except OSError as exc:
                _rollback_after_failure(created, exc)
                raise
            return metadata
        assert durable_invocation is not None
        receipt_path = Path(execution.reservation.receipt_path)
        try:
            artifacts = ShellArtifactsReceipt(
                stdout=artifact_receipt(stdout_artifact),
                stderr=artifact_receipt(stderr_artifact),
                metadata=artifact_receipt(metadata_artifact),
            )
            draft = draft_attempt_receipt(
                execution,
                durable_invocation,
                artifacts,
                exit_code,
            )
            write_attempt_receipt(receipt_path, draft)
        except (OSError, AttemptReceiptError, ValidationError) as exc:
            _rollback_after_failure(created, exc)
            raise
        _ = self._authority_registry.register(
            Path(execution.reservation.receipt_path), draft
        )
        return metadata

    def accept_attempt_receipt(
        self,
        receipt_path: Path,
        authority: Phase5AttemptAuthority,
    ) -> Phase5AttemptReceipt:
        if not self._authority_registry.authority_is_registered(authority):
            raise AttemptReceiptError(
                AttemptReceiptErrorKind.IDENTITY_MISMATCH,
                str(authority.attempt_id),
            )
        with self._transaction:
            accepted = accept_attempt_receipt(receipt_path, authority)
            self._authority_registry.mark_accepted(receipt_path, accepted)
            return accepted

    def authority_for(self, receipt_path: str) -> Phase5AttemptAuthority | None:
        return self._authority_registry.authority_for(receipt_path)

    def authority_for_attempt(
        self,
        attempt_id: str,
    ) -> Phase5AttemptAuthority | None:
        return self._authority_registry.authority_for_attempt(attempt_id)

    def accepted_receipt_paths(self) -> tuple[str, ...]:
        return self._authority_registry.accepted_receipt_paths()

    def record_finalized_authority(
        self, receipt_path: str, receipt: Phase5AttemptReceipt
    ) -> None:
        self._authority_registry.finalize(receipt_path, receipt)


def _rollback_after_failure(
    created: list[WrittenArtifact],
    primary: OSError | AttemptReceiptError | ValidationError,
) -> None:
    cleanup_errors = rollback_created(created)
    if cleanup_errors:
        details = "; ".join(str(error) for error in cleanup_errors)
        raise OSError(f"{primary}; artifact rollback failed: {details}") from primary
