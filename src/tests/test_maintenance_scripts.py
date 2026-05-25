import json
import subprocess
import sys
from pathlib import Path
from typing import cast

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.paths import execution_root
from scripts.e2e_smoke_test import MockSessionManager
from validators.validate_entry_static import validate as validate_entry_static
from validators.validate_env_detect import validate as validate_env_detect


def test_verify_improvements_accepts_seam_execution_root(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            "bash",
            str(PROJECT_ROOT / "scripts" / "verify_improvements.sh"),
            "--output-dir",
            str(tmp_path),
            "--repo-root",
            str(execution_root()),
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Results: 4 passed, 0 failed" in result.stdout


def test_e2e_smoke_help_does_not_run_smoke() -> None:
    result = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts" / "e2e_smoke_test.py"), "--help"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    combined_output = result.stdout + result.stderr
    assert result.returncode == 0, combined_output
    assert "usage:" in result.stdout.lower()
    assert "E2E FAILED" not in combined_output


def test_e2e_smoke_mock_outputs_match_current_validators(tmp_path: Path) -> None:
    manager = MockSessionManager(tmp_path)

    phase_0_payload = cast(
        dict[str, object],
        json.loads(manager.send_command("session", "# Phase 0 - Environment Detection")),
    )
    phase_0_validation = validate_env_detect(phase_0_payload)
    assert phase_0_validation["passed"], phase_0_validation["errors"]

    phase_35_payload = cast(
        dict[str, object],
        json.loads(manager.send_command("session", "# Phase 3.5 - Static Compliance Check")),
    )
    phase_35_validation = validate_entry_static(phase_35_payload)
    assert phase_35_validation["passed"], phase_35_validation["errors"]


def test_e2e_smoke_phase6_mock_not_shadowed_by_phase35_context(tmp_path: Path) -> None:
    manager = MockSessionManager(tmp_path)
    report_dir = tmp_path / "reports"
    command = (
        "# Phase 6 - Final Report Generation\n"
        "prior phase key: phase_35_static_validate\n"
        "prior prompt heading: # Phase 0 - Environment Detection\n"
        f"write reports into `{report_dir}`"
    )

    payload = cast(dict[str, object], json.loads(manager.send_command("session", command)))

    report_paths = cast(list[object] | None, payload.get("report_paths"))
    assert isinstance(report_paths, list)
    assert len(report_paths) == 5
