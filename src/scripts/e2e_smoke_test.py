from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path
from pathlib import Path
from typing import cast

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# pylint: disable=wrong-import-position
from core.artifact_store import ArtifactStore
from core.phase_runner import PhaseRunner
from core.prompt_loader import PromptLoader
from core.validator_engine import ValidatorEngine
from migrator.rule_based import RuleBasedMigrator
from validators.validate_validation_final import validate as validate_validation_final

BASE_URL = "http://127.0.0.1:4096"
RUN_ID = "e2e-smoke"
REPORT_NAMES = [
    "API_KEY_REPORT.md",
    "OPENCODE_OPERATIONS_LOG.md",
    "TOOLS_EXECUTION_REPORT.md",
    "SUMMARY_REPORT.md",
    "LOCAL_TOOL_OPTIMIZATION_REPORT.md",
]
CUDA_SAMPLE = """\
import torch
import torch.nn as nn

device = \"cuda\"
model = nn.Linear(4, 2).cuda()
x = torch.randn(2, 4, device=\"cuda\")

with torch.cuda.amp.autocast():
    y = model(x)

loss = y.sum()
loss.backward()

torch.distributed.init_process_group(backend=\"nccl\")
"""


class SmokeTestError(RuntimeError):
    pass


class MockSessionManager:
    project_dir: Path

    def __init__(self, project_dir: Path) -> None:
        self.project_dir = project_dir
        self.get_or_create_calls: list[tuple[str, str]] = []
        self.send_command_calls: list[tuple[str, str, int | None]] = []
        self._sessions: dict[tuple[str, str], str] = {}

    def get_or_create(self, role: str, lifecycle: str) -> str:
        self.get_or_create_calls.append((role, lifecycle))
        key = (role, lifecycle)
        if key not in self._sessions:
            self._sessions[key] = f"{role}-{lifecycle}-session"
        return self._sessions[key]

    def send_command(
        self,
        session_id: str,
        command: str,
        timeout: int | None = None,
        **kwargs: object,
    ) -> str:
        self.send_command_calls.append((session_id, command, timeout))
        if command.startswith(
            "# Phase 0 - Environment Detection"
        ) or command.startswith("Your previous response for phase_0_env_detect"):
            return json.dumps(
            {
                "platform": "cuda",
                "npu_detected": False,
                "python_version": (
                    f"{sys.version_info.major}.{sys.version_info.minor}."
                    f"{sys.version_info.micro}"
                ),
                "cann_version": "n/a",
                "ascendc_available": False,
                "driver_version": "not_found",
            }
            )
        if command.startswith("# Phase 1 - Project Analysis") or command.startswith(
            "Your previous response for phase_1_project_analysis"
        ):
            return json.dumps({
                "project_dir": str(self.project_dir),
                "dependencies": ["torch"],
                "cuda_detected": True,
                "entry_script": "train.py",
            })
        if command.startswith("# Phase 2 - Virtual Environment Creation") or command.startswith(
            "Your previous response for phase_2_venv_create"
        ):
            return json.dumps({
                "venv_path": str(self.project_dir / ".venv"),
                "python_path": str(self.project_dir / ".venv" / "bin" / "python"),
                "installed_packages": ["torch", "torch_npu"],
            })
        if command.startswith("# Phase 3 - Entry Script Confirmation") or command.startswith(
            "Your previous response for phase_3_entry_script"
        ):
            return json.dumps({
                "entry_script_path": str(self.project_dir / "train.py"),
                "run_command": "python train.py",
                "project_dir": str(self.project_dir),
            })
        if command.startswith("# Phase 3.5 - Static Compliance Check") or command.startswith(
            "Your previous response for phase_35_static_validate"
        ):
            return json.dumps({
                "validation_passed": True,
                "issues": [],
                "fix_plan": "No issues found. Script is headless-compliant.",
            })
        if command.startswith("# Phase 6 - Final Report Generation"):
            report_dir = self._extract_report_dir(command)
            report_paths = self._write_reports(report_dir)
            return json.dumps({
                "report_paths": report_paths,
                "migration_summary": {"files_migrated": 1, "files_skipped": 0},
            })
        raise AssertionError(f"Unexpected prompt for mock session: {command[:120]}")

    @staticmethod
    def _extract_report_dir(command: str) -> Path:
        match = re.search(r"write reports into `([^`]+)`", command)
        if match is None:
            raise SmokeTestError("Could not extract report directory from Phase 6 prompt")
        return Path(match.group(1))

    @staticmethod
    def _write_reports(report_dir: Path) -> list[str]:
        report_dir.mkdir(parents=True, exist_ok=True)
        report_paths: list[str] = []
        for report_name in REPORT_NAMES:
            report_path = report_dir / report_name
            _ = report_path.write_text(
                f"# {report_name}\n\nGenerated by the smoke test.\n",
                encoding="utf-8",
            )
            report_paths.append(str(report_path))
        return report_paths


def check_server_running(base_url: str = BASE_URL, timeout: float = 3.0) -> None:
    try:
        completed = subprocess.run(
            ["curl", "-fsS", "-o", "/dev/null", f"{base_url.rstrip('/')}/agent"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError) as exc:
        raise SmokeTestError(f"OpenCode server is not reachable at {base_url}: {exc}") from exc
    if completed.returncode != 0:
        detail = (
            completed.stderr.strip()
            or completed.stdout.strip()
            or f"curl exit code {completed.returncode}"
        )
        raise SmokeTestError(f"OpenCode server is not reachable at {base_url}: {detail}")


def create_test_project(temp_root: Path) -> tuple[Path, Path]:
    project_dir = temp_root / "cuda_project"
    project_dir.mkdir(parents=True, exist_ok=True)
    _ = (project_dir / "README.md").write_text(
        "# CUDA sample\n\nMinimal smoke-test project.\n", encoding="utf-8"
    )
    source_file = project_dir / "train.py"
    _ = source_file.write_text(CUDA_SAMPLE, encoding="utf-8")
    return project_dir, source_file


def write_phase_5_success(artifact_store: ArtifactStore) -> dict[str, object]:
    result: dict[str, object] = {
        "success": True,
        "status": "success",
        "iteration_count": 1,
        "errors": [],
    }
    validation = validate_validation_final(result)
    if not validation["passed"]:
        raise SmokeTestError("Mocked Phase 5 output did not pass validation")

    raw_path = artifact_store.save_phase_output("phase_5_validation", result, attempt=1)
    canonical_path = artifact_store.mark_validated("phase_5_validation", result)
    _ = artifact_store.write_journal({
        "phase_id": "phase_5_validation",
        "attempt": 1,
        "status": "succeeded",
        "session_id": "mock-phase-5",
        "raw_path": raw_path,
        "canonical_path": canonical_path,
        "errors": validation["errors"],
        "warnings": validation["warnings"],
    })
    return result


def sync_full_phase_artifacts(
    artifact_store: ArtifactStore,
    phase_outputs: dict[str, dict[str, object]],
) -> None:
    for phase_id, payload in phase_outputs.items():
        _ = artifact_store.save_phase_output(phase_id, payload, attempt=1)
        _ = artifact_store.mark_validated(phase_id, payload)


def verify_run(
    *,
    temp_root: Path,
    source_file: Path,
    artifact_store: ArtifactStore,
    phase_outputs: dict[str, dict[str, object]],
    phase_4_report: dict[str, object],
    phase_6_report: dict[str, object],
) -> None:
    artifacts_root = temp_root / ".sm-artifacts"
    if not artifacts_root.is_dir():
        raise SmokeTestError(".sm-artifacts directory was not created")
    if not Path(artifact_store.artifact_dir).is_dir():
        raise SmokeTestError("ArtifactStore run directory was not created")

    expected_phase_keys = [
        "phase_0_env_detect",
        "phase_1_project_analysis",
        "phase_2_venv_create",
        "phase_3_entry_script",
        "phase_35_static_validate",
    ]
    if list(phase_outputs) != expected_phase_keys:
        raise SmokeTestError(f"Unexpected phase 0-3 outputs: {list(phase_outputs)}")

    journal = artifact_store.get_journal()
    for phase_id in [*expected_phase_keys, "phase_4_rule_migration", "phase_5_validation"]:
        if not any(
            entry.get("phase_id") == phase_id and entry.get("status") == "succeeded"
            for entry in journal
        ):
            raise SmokeTestError(f"Journal does not show {phase_id} as succeeded")

    files_migrated = phase_4_report.get("files_migrated", 0)
    total_replacements = phase_4_report.get("total_replacements", 0)
    if not isinstance(files_migrated, int) or files_migrated < 1:
        raise SmokeTestError("Phase 4 did not migrate any files")
    if not isinstance(total_replacements, int) or total_replacements < 1:
        raise SmokeTestError("Phase 4 did not report replacements")

    report_paths = phase_6_report.get("report_paths")
    if not isinstance(report_paths, list):
        raise SmokeTestError("Phase 6 did not return the expected report bundle")
    typed_report_paths = cast(list[str], report_paths)
    if len(typed_report_paths) != len(REPORT_NAMES):
        raise SmokeTestError("Phase 6 did not return the expected report bundle")
    for report_path in typed_report_paths:
        if not Path(str(report_path)).is_file():
            raise SmokeTestError(f"Missing report file: {report_path}")

    migrated_code = source_file.read_text(encoding="utf-8")
    if "torch.cuda" in migrated_code:
        raise SmokeTestError("Migrated code still contains torch.cuda")
    if "torch.npu" not in migrated_code:
        raise SmokeTestError("Migrated code does not contain torch.npu")

    if artifact_store.load_phase_output("phase_4_rule_migration") is None:
        raise SmokeTestError("Phase 4 canonical artifact is missing")
    if artifact_store.load_phase_output("phase_5_validation") is None:
        raise SmokeTestError("Phase 5 canonical artifact is missing")
    if artifact_store.load_phase_output("phase_6_report") is None:
        raise SmokeTestError("Phase 6 canonical artifact is missing")


def run_smoke_test() -> None:
    check_server_running()
    temp_root = Path(tempfile.mkdtemp(prefix="migration-utils-e2e-"))
    try:
        project_dir, source_file = create_test_project(temp_root)
        artifact_store = ArtifactStore(str(temp_root), RUN_ID)
        prompt_loader = PromptLoader()
        validator = ValidatorEngine()
        session_mgr = MockSessionManager(project_dir)
        runner = PhaseRunner(
            session_mgr=session_mgr,
            artifact_store=artifact_store,
            prompt_loader=prompt_loader,
            validator=validator,
        )
        migrator = RuleBasedMigrator()

        phase_outputs = runner.run_phase_0_to_3(str(project_dir), session_mgr, artifact_store)
        sync_full_phase_artifacts(artifact_store, phase_outputs)
        phase_4_report = runner.run_phase_4(artifact_store, migrator)
        _ = write_phase_5_success(artifact_store)
        phase_6_report = runner.run_phase_6(str(project_dir), artifact_store, session_mgr)

        verify_run(
            temp_root=temp_root,
            source_file=source_file,
            artifact_store=artifact_store,
            phase_outputs=phase_outputs,
            phase_4_report=phase_4_report,
            phase_6_report=phase_6_report,
        )
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the local migration_utils E2E smoke test.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    _ = parse_args(argv)
    try:
        run_smoke_test()
    except Exception as exc:
        print(f"E2E FAILED: {exc}", file=sys.stderr)
        traceback.print_exc()
        print("E2E FAILED")
        return 1

    print("E2E PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
