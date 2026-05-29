import json
import os
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
