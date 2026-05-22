from pathlib import Path
import subprocess
import sys


SRC_ROOT = Path(__file__).resolve().parents[2]
EXECUTION_ROOT = SRC_ROOT.parent


def test_root_module_entrypoint_shows_v2_help() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "tests.e2e.e2e_test_v2", "--help"],
        cwd=EXECUTION_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "Run YAML-driven src E2E migration workflow" in completed.stdout
    assert "--project-dir" in completed.stdout


def test_src_module_entrypoint_still_shows_v2_help() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "tests.e2e.e2e_test_v2", "--help"],
        cwd=SRC_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "Run YAML-driven src E2E migration workflow" in completed.stdout
    assert "--output_dir" in completed.stdout
