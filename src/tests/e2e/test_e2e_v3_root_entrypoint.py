from pathlib import Path
import subprocess
import sys


MIGRATION_UTILS_ROOT = Path(__file__).resolve().parents[2]
EXECUTION_ROOT = MIGRATION_UTILS_ROOT.parent


def test_root_module_v3_entrypoint_shows_help() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "tests.e2e.e2e_test_v3", "--help"],
        cwd=EXECUTION_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "--workflow-path" in completed.stdout
    assert "--project-dir" in completed.stdout


def test_migration_utils_v3_module_shows_help() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "tests.e2e.e2e_test_v3", "--help"],
        cwd=MIGRATION_UTILS_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "--workflow-path" in completed.stdout
    assert "V3" in completed.stdout or "custom workflow path" in completed.stdout


def test_v3_parser_accepts_workflow_path() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "tests.e2e.e2e_test_v3", "--help"],
        cwd=EXECUTION_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "--workflow-path" in completed.stdout


# ── new: --server-no-auto-start and parser regressions ──

def test_v3_parser_accepts_server_no_auto_start() -> None:
    """Verify V3 (e2e_test_v3.py) --server-no-auto-start is a recognized flag."""
    completed = subprocess.run(
        [sys.executable, "-m", "tests.e2e.e2e_test_v3", "--server-no-auto-start", "--help"],
        cwd=EXECUTION_ROOT,
        capture_output=True, text=True, check=False,
    )
    assert completed.returncode == 0, f"stderr: {completed.stderr}"


def test_v3_parser_server_url_default_is_none() -> None:
    code = (
        "import sys; sys.path.insert(0, 'src'); "
        "from tests.e2e.e2e_test_v3 import build_parser; "
        "p = build_parser(); "
        "defaults = {a.dest: a.default for a in p._actions}; "
        "print(defaults.get('server_url'))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=EXECUTION_ROOT,
        capture_output=True, text=True, check=False,
    )
    assert completed.returncode == 0, f"stderr: {completed.stderr}"
    assert completed.stdout.strip() == "None", f"Expected None, got {completed.stdout.strip()!r}"
