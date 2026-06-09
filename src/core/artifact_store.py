import json
import os
import shutil
import time
from typing import Any


class ArtifactStore:

    base_dir: str
    run_id: str
    artifact_dir: str
    raw_dir: str
    validated_dir: str
    journal_path: str
    checkpoint_path: str

    def __init__(self, base_dir: str, run_id: str) -> None:
        self.base_dir = base_dir
        self.run_id = run_id
        self.artifact_dir = os.path.join(base_dir, ".sm-artifacts", run_id)
        self.raw_dir = os.path.join(self.artifact_dir, "raw")
        self.validated_dir = os.path.join(self.artifact_dir, "validated")
        self.journal_path = os.path.join(self.artifact_dir, "execution_journal.jsonl")
        self.checkpoint_path = os.path.join(self.artifact_dir, "state.json")

        os.makedirs(self.raw_dir, exist_ok=True)
        os.makedirs(self.validated_dir, exist_ok=True)


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
    ) -> dict[str, Any]:
        """Persist complete stdout/stderr and metadata for a shell attempt."""
        artifact_dir = os.path.abspath(os.path.join(self.artifact_dir, "shell_attempts"))
        os.makedirs(artifact_dir, exist_ok=True)
        safe_phase = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in phase_id)
        existing = [
            name for name in os.listdir(artifact_dir)
            if name.startswith(f"{safe_phase}_attempt") and name.endswith(".meta.json")
        ]
        attempt = len(existing) + 1
        prefix = os.path.join(artifact_dir, f"{safe_phase}_attempt{attempt:04d}")
        stdout_path = os.path.abspath(prefix + ".stdout.log")
        stderr_path = os.path.abspath(prefix + ".stderr.log")
        meta_path = os.path.abspath(prefix + ".meta.json")

        if stdout_source_path:
            shutil.copyfile(stdout_source_path, stdout_path)
        else:
            with open(stdout_path, "w", encoding="utf-8") as handle:
                handle.write(stdout or "")
        if stderr_source_path:
            shutil.copyfile(stderr_source_path, stderr_path)
        else:
            with open(stderr_path, "w", encoding="utf-8") as handle:
                handle.write(stderr or "")

        metadata: dict[str, Any] = {
            "phase_id": phase_id,
            "attempt": attempt,
            "command": command,
            "cwd": cwd or "",
            "backend_workdir": backend_workdir or "",
            "exit_code": exit_code,
            "duration": duration,
            "stdout_path": stdout_path,
            "stderr_path": stderr_path,
            "meta_path": meta_path,
            "stdout_bytes": os.path.getsize(stdout_path),
            "stderr_bytes": os.path.getsize(stderr_path),
            "stdout_complete": True,
            "stderr_complete": True,
            "complete": True,
            "timestamp": time.time(),
        }
        with open(meta_path, "w", encoding="utf-8") as handle:
            json.dump(metadata, handle, indent=2)
        return metadata

    def save_phase_output(self, phase_id: str, data: dict[str, Any], attempt: int = 0) -> str:
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
