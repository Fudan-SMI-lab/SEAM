from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path
from typing import Any, BinaryIO

from core.phase5_artifact_store import Phase5ArtifactStore
from core.phase5_attempt_receipt import (
    Phase5AttemptAuthority,
    Phase5AttemptReceipt,
    Phase5AttemptReservation,
    ShellAttemptExecution,
)
from core.phase5_transaction import Phase5Transaction

_SAFE_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


def _phase_key(phase_id: str) -> str:
    return phase_id[6:] if phase_id.startswith("phase_") else phase_id


class ArtifactStore:
    base_dir: str
    run_id: str
    artifact_dir: str
    raw_dir: str
    validated_dir: str
    journal_path: str
    checkpoint_path: str
    _phase5_store: Phase5ArtifactStore
    _phase5_transaction: Phase5Transaction

    def __init__(self, base_dir: str, run_id: str) -> None:
        self.base_dir = base_dir
        self.run_id = run_id
        self.artifact_dir = os.path.join(base_dir, ".sm-artifacts", run_id)
        self.raw_dir = os.path.join(self.artifact_dir, "raw")
        self.validated_dir = os.path.join(self.artifact_dir, "validated")
        self.journal_path = os.path.join(self.artifact_dir, "execution_journal.jsonl")
        self.checkpoint_path = os.path.join(self.artifact_dir, "state.json")
        self._phase5_transaction = Phase5Transaction()
        self._phase5_store = Phase5ArtifactStore(
            self.artifact_dir,
            self.run_id,
            self._phase5_transaction,
        )

        os.makedirs(self.raw_dir, exist_ok=True)
        os.makedirs(self.validated_dir, exist_ok=True)

    @classmethod
    def create_exclusive(cls, base_dir: str, run_id: str) -> "ArtifactStore":
        if _SAFE_RUN_ID.fullmatch(run_id) is None:
            message = f"run_id is not a safe artifact namespace: {run_id!r}"
            raise ValueError(message)
        project_dir = os.path.abspath(base_dir)
        project_metadata = os.lstat(project_dir)
        project_attributes = getattr(project_metadata, "st_file_attributes", 0)
        linked_project = stat.S_ISLNK(project_metadata.st_mode) or bool(
            project_attributes & 0x400
        )
        if linked_project or not stat.S_ISDIR(project_metadata.st_mode):
            raise OSError("project directory must not be a link or reparse point")
        artifacts_dir = os.path.join(project_dir, ".sm-artifacts")
        os.makedirs(artifacts_dir, exist_ok=True)
        artifact_metadata = os.lstat(artifacts_dir)
        artifact_attributes = getattr(artifact_metadata, "st_file_attributes", 0)
        linked = stat.S_ISLNK(artifact_metadata.st_mode) or bool(
            artifact_attributes & 0x400
        )
        if linked or not stat.S_ISDIR(artifact_metadata.st_mode):
            raise OSError("artifact root must be a real directory")
        os.mkdir(os.path.join(artifacts_dir, run_id))
        return cls(project_dir, run_id)

    def reserve_phase5_attempt(self) -> Phase5AttemptReservation:
        return self._phase5_store.reserve_attempt()

    def phase5_attempt_authority(
        self, receipt_path: str
    ) -> Phase5AttemptAuthority | None:
        return self._phase5_store.authority_for(receipt_path)

    def phase5_attempt_authority_by_id(
        self,
        attempt_id: str,
    ) -> Phase5AttemptAuthority | None:
        return self._phase5_store.authority_for_attempt(attempt_id)

    def accepted_phase5_receipt_paths(self) -> tuple[str, ...]:
        return self._phase5_store.accepted_receipt_paths()

    def phase5_transaction(self) -> Phase5Transaction:
        return self._phase5_transaction

    def record_finalized_phase5_authority(
        self, receipt_path: str, receipt: Phase5AttemptReceipt
    ) -> None:
        self._phase5_store.record_finalized_authority(receipt_path, receipt)

    def accept_phase5_attempt_receipt(
        self,
        receipt_path: str | os.PathLike[str],
        authority: Phase5AttemptAuthority,
    ) -> Phase5AttemptReceipt:
        return self._phase5_store.accept_attempt_receipt(Path(receipt_path), authority)

    def save_shell_attempt_artifacts(
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
    ) -> dict[str, Any]:
        return self._phase5_store.save_attempt(
            phase_id,
            command=command,
            cwd=cwd,
            backend_workdir=backend_workdir,
            exit_code=exit_code,
            duration=duration,
            stdout=stdout,
            stderr=stderr,
            stdout_source_path=stdout_source_path,
            stderr_source_path=stderr_source_path,
            stdout_source=stdout_source,
            stderr_source=stderr_source,
            execution=execution,
        )

    def save_phase_output(
        self, phase_id: str, data: dict[str, Any], attempt: int = 0
    ) -> str:
        key = _phase_key(phase_id)
        filename = f"phase_{key}_attempt{attempt}.json"
        filepath = os.path.join(self.raw_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        return filepath

    def load_phase_output(self, phase_id: str) -> dict[str, Any] | None:
        key = _phase_key(phase_id)
        filename = f"phase_{key}_canonical.json"
        filepath = os.path.join(self.validated_dir, filename)
        if not os.path.exists(filepath):
            return None
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)

    def mark_validated(self, phase_id: str, data: dict[str, Any]) -> str:
        key = _phase_key(phase_id)
        filename = f"phase_{key}_canonical.json"
        filepath = os.path.join(self.validated_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        return filepath

    @staticmethod
    def get_latest_run_id(base_dir: str) -> str | None:
        artifacts_dir = os.path.join(base_dir, ".sm-artifacts")
        if not os.path.isdir(artifacts_dir):
            return None

        run_ids: list[tuple[float, str]] = []
        for entry in os.listdir(artifacts_dir):
            entry_path = os.path.join(artifacts_dir, entry)
            if os.path.isdir(entry_path):
                stat = os.stat(entry_path)
                run_ids.append((stat.st_mtime, entry))

        if not run_ids:
            return None
        run_ids.sort(key=lambda x: x[0], reverse=True)
        return run_ids[0][1]

    def write_journal(self, entry: dict[str, Any]) -> str:
        with open(self.journal_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        return self.journal_path

    def get_journal(self) -> list[dict[str, Any]]:
        if not os.path.exists(self.journal_path):
            return []
        entries: list[dict[str, Any]] = []
        with open(self.journal_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
        return entries

    def save_checkpoint(self, state: dict[str, Any]) -> str:
        with open(self.checkpoint_path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        return self.checkpoint_path

    def load_checkpoint(self) -> dict[str, Any] | None:
        if not os.path.exists(self.checkpoint_path):
            return None
        with open(self.checkpoint_path, "r", encoding="utf-8") as f:
            return json.load(f)
