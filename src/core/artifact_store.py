import json
import os
from typing import Any

from core.phase5_artifact_store import Phase5ArtifactStore
from core.phase5_attempt_receipt import (
    Phase5AttemptAuthority,
    Phase5AttemptReceipt,
    Phase5AttemptReservation,
    ShellAttemptExecution,
)


class ArtifactStore:
    base_dir: str
    run_id: str
    artifact_dir: str
    raw_dir: str
    validated_dir: str
    journal_path: str
    checkpoint_path: str
    _phase5_store: Phase5ArtifactStore

    def __init__(self, base_dir: str, run_id: str) -> None:
        self.base_dir = base_dir
        self.run_id = run_id
        self.artifact_dir = os.path.join(base_dir, ".sm-artifacts", run_id)
        self.raw_dir = os.path.join(self.artifact_dir, "raw")
        self.validated_dir = os.path.join(self.artifact_dir, "validated")
        self.journal_path = os.path.join(self.artifact_dir, "execution_journal.jsonl")
        self.checkpoint_path = os.path.join(self.artifact_dir, "state.json")
        self._phase5_store = Phase5ArtifactStore(self.artifact_dir, self.run_id)

        os.makedirs(self.raw_dir, exist_ok=True)
        os.makedirs(self.validated_dir, exist_ok=True)

    def reserve_phase5_attempt(self) -> Phase5AttemptReservation:
        return self._phase5_store.reserve_attempt()

    def phase5_attempt_authority(
        self, receipt_path: str
    ) -> Phase5AttemptAuthority | None:
        return self._phase5_store.authority_for(receipt_path)

    def record_finalized_phase5_authority(
        self, receipt_path: str, receipt: Phase5AttemptReceipt
    ) -> None:
        self._phase5_store.record_finalized_authority(receipt_path, receipt)

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
            execution=execution,
        )

    def save_phase_output(
        self, phase_id: str, data: dict[str, Any], attempt: int = 0
    ) -> str:
        key = phase_id.removeprefix("phase_")
        filename = f"phase_{key}_attempt{attempt}.json"
        filepath = os.path.join(self.raw_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        return filepath

    def load_phase_output(self, phase_id: str) -> dict[str, Any] | None:
        key = phase_id.removeprefix("phase_")
        filename = f"phase_{key}_canonical.json"
        filepath = os.path.join(self.validated_dir, filename)
        if not os.path.exists(filepath):
            return None
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)

    def mark_validated(self, phase_id: str, data: dict[str, Any]) -> str:
        key = phase_id.removeprefix("phase_")
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
