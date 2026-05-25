from pathlib import Path
import subprocess
import sys


MIGRATION_UTILS_ROOT = Path(__file__).resolve().parents[2]
EXECUTION_ROOT = MIGRATION_UTILS_ROOT.parent


def test_root_module_entrypoint_shows_v2_help() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "tests.e2e.e2e_test_v2", "--help"],
        cwd=EXECUTION_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "Run YAML-driven migration_utils E2E migration workflow" in completed.stdout
    assert "--project-dir" in completed.stdout


def test_migration_utils_module_entrypoint_still_shows_v2_help() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "tests.e2e.e2e_test_v2", "--help"],
        cwd=MIGRATION_UTILS_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "Run YAML-driven migration_utils E2E migration workflow" in completed.stdout
    assert "--output-dir" in completed.stdout
