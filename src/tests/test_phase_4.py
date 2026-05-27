import json
import sys
from pathlib import Path
from typing import cast

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.artifact_store import ArtifactStore
from core.phase_runner import PhaseRunner
from core.prompt_loader import PromptLoader
from core.validator_engine import ValidatorEngine
from migrator.rule_based import RuleBasedMigrator


class NoopSessionManager:
    def get_or_create(self, role: str, lifecycle: str, agent: str = "") -> str:
        return f"{role}-{lifecycle}"

    def send_command(self, session_id: str, command: str, timeout: int | None = 600, retries: int | None = None) -> str:
        raise AssertionError(f"Unexpected send_command for {session_id}: {command} ({timeout})")


def build_runner(base_dir):
    artifact_store = ArtifactStore(str(base_dir), "testrun")
    runner = PhaseRunner(
        session_mgr=NoopSessionManager(),
        artifact_store=artifact_store,
        prompt_loader=PromptLoader(),
        validator=ValidatorEngine(),
    )
    return runner, artifact_store


CUDA_SAMPLE = """\
import torch
import torch.nn as nn

device = "cuda"
model = nn.Linear(10, 5).cuda()
x = torch.randn(32, 10, device="cuda")

with torch.cuda.amp.autocast():
    y = model(x)

loss = y.sum()
loss.backward()

torch.distributed.init_process_group(backend="nccl")
"""

NPU_EXPECTED_IMPORTS = [
    "import torch_npu",
]


def test_run_phase_4_migrates_cuda_project(tmp_path):
    project_dir = tmp_path / "cuda_project"
    project_dir.mkdir()
    src_file = project_dir / "train.py"
    src_file.write_text(CUDA_SAMPLE)

    runner, artifact_store = build_runner(tmp_path)

    phase_3_output = {
        "entry_script_path": str(src_file),
        "run_command": f"python {src_file}",
        "project_dir": str(project_dir),
    }
    artifact_store.mark_validated("phase_3_entry_script", phase_3_output)

    migrator = RuleBasedMigrator(strategy="cuda_to_npu")
    report = runner.run_phase_4(artifact_store, migrator)

    assert cast(int, report["files_migrated"]) >= 1
    assert isinstance(report["files_skipped"], int)
    assert isinstance(report["replacement_counts"], dict)
    assert cast(int, report["total_replacements"]) > 0

    migrated_code = src_file.read_text()
    assert "torch.cuda" not in migrated_code
    assert ".cuda(" not in migrated_code
    assert "torch.npu" in migrated_code
    assert ".npu(" in migrated_code
    assert "import torch_npu" in migrated_code

    saved = artifact_store.load_phase_output("phase_4_rule_migration")
    assert saved is not None
    assert cast(int, saved["files_migrated"]) >= 1
    assert cast(int, saved["total_replacements"]) > 0


def test_run_phase_4_fails_without_phase_3(tmp_path):
    runner, artifact_store = build_runner(tmp_path)
    migrator = RuleBasedMigrator(strategy="cuda_to_npu")

    with pytest.raises(ValueError, match="Phase 3 output"):
        runner.run_phase_4(artifact_store, migrator)


def test_run_phase_4_empty_project(tmp_path):
    project_dir = tmp_path / "empty_project"
    project_dir.mkdir()

    runner, artifact_store = build_runner(tmp_path)

    phase_3_output = {
        "entry_script_path": str(project_dir / "dummy.py"),
        "project_dir": str(project_dir),
    }
    artifact_store.mark_validated("phase_3_entry_script", phase_3_output)

    migrator = RuleBasedMigrator(strategy="cuda_to_npu")
    report = runner.run_phase_4(artifact_store, migrator)

    assert report["files_migrated"] == 0
    assert report["total_replacements"] == 0
    assert report["replacement_counts"] == {}


def test_run_phase_4_fails_missing_project_dir(tmp_path):
    runner, artifact_store = build_runner(tmp_path)

    artifact_store.mark_validated("phase_3_entry_script", {
        "entry_script_path": "/some/path.py",
    })

    migrator = RuleBasedMigrator(strategy="cuda_to_npu")
    with pytest.raises(ValueError, match="project_dir not found"):
        runner.run_phase_4(artifact_store, migrator)
