from __future__ import annotations

from pathlib import Path
from typing import cast

from core.artifact_store import ArtifactStore
from core.phase_runner import PhaseRunner
from core.prompt_loader import PromptLoader
from core.validator_engine import ValidatorEngine
from migrator.rule_based import RuleBasedMigrator


class NoopSessionManager:
    def get_or_create(self, role: str, lifecycle: str) -> str:
        return f"{role}-{lifecycle}"

    def send_command(self, session_id: str, command: str, timeout: int = 600) -> str:
        raise AssertionError(f"Unexpected send_command for {session_id}: {command} ({timeout})")


CUDA_TRAIN_SCRIPT = """import torch
import torch.nn as nn

device = \"cuda\"
model = nn.Linear(4, 2).cuda()
batch = torch.randn(8, 4, device=\"cuda\")

with torch.cuda.amp.autocast():
    result = model(batch)

torch.distributed.init_process_group(backend=\"nccl\")
"""

CUDA_HELPER_SCRIPT = """import torch


def move_tensor(tensor):
    return tensor.cuda()
"""


def test_phase_4_migrates_real_cuda_project(tmp_path: Path) -> None:
    project_dir = tmp_path / "cuda_project"
    package_dir = project_dir / "pkg"
    package_dir.mkdir(parents=True)

    train_path = project_dir / "train.py"
    helper_path = package_dir / "helpers.py"
    notes_path = project_dir / "notes.txt"

    _ = train_path.write_text(CUDA_TRAIN_SCRIPT, encoding="utf-8")
    _ = helper_path.write_text(CUDA_HELPER_SCRIPT, encoding="utf-8")
    _ = notes_path.write_text(
        "torch.cuda stays here because txt files are ignored\n", encoding="utf-8"
    )

    artifact_store = ArtifactStore(str(tmp_path), "phase4-run")
    _ = artifact_store.mark_validated(
        "phase_3_entry_script",
        {
            "entry_script_path": str(train_path),
            "run_command": "python train.py",
            "project_dir": str(project_dir),
        },
    )

    runner = PhaseRunner(
        session_mgr=NoopSessionManager(),
        artifact_store=artifact_store,
        prompt_loader=PromptLoader(),
        validator=ValidatorEngine(),
    )

    report = runner.run_phase_4(artifact_store, RuleBasedMigrator())
    replacement_counts = cast(dict[str, int], report["replacement_counts"])

    assert report["files_migrated"] == 2
    assert report["files_skipped"] == 0
    assert cast(int, report["total_replacements"]) >= 5
    assert report["project_dir"] == str(project_dir)
    assert replacement_counts["inject_torch_npu"] == 2

    train_code = train_path.read_text(encoding="utf-8")
    helper_code = helper_path.read_text(encoding="utf-8")
    assert "torch.cuda" not in train_code
    assert ".cuda(" not in helper_code
    assert "torch.npu" in train_code
    assert ".npu(" in helper_code
    assert "import torch_npu" in train_code
    assert "import torch_npu" in helper_code
    assert (
        notes_path.read_text(encoding="utf-8")
        == "torch.cuda stays here because txt files are ignored\n"
    )

    saved = artifact_store.load_phase_output("phase_4_rule_migration")
    journal = artifact_store.get_journal()
    assert saved == report
    assert len(journal) == 1
    assert journal[0]["phase_id"] == "phase_4_rule_migration"
    assert journal[0]["status"] == "succeeded"
