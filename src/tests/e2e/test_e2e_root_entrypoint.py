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


# ── new: --server-no-auto-start and parser regressions ──

def test_v1_e2e_test_accepts_server_no_auto_start() -> None:
    completed = subprocess.run(
        [sys.executable, "src/tests/e2e/e2e_test.py", "--server-no-auto-start", "--help"],
        cwd=EXECUTION_ROOT,
        capture_output=True, text=True, check=False,
    )
    assert completed.returncode == 0, f"stderr: {completed.stderr}"


def test_v1_parser_server_url_default_is_none() -> None:
    code = (
        "import sys; sys.path.insert(0, 'src'); "
        "from tests.e2e.e2e_test import build_parser; "
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


def test_v2_parser_accepts_server_no_auto_start() -> None:
    """Verify V2 (e2e_test_v2.py) --server-no-auto-start is accepted."""
    completed = subprocess.run(
        [sys.executable, "-m", "tests.e2e.e2e_test_v2", "--server-no-auto-start", "--help"],
        cwd=EXECUTION_ROOT,
        capture_output=True, text=True, check=False,
    )
    assert completed.returncode == 0, f"stderr: {completed.stderr}"


def test_v2_parser_server_url_default_is_none() -> None:
    code = (
        "import sys; sys.path.insert(0, 'src'); "
        "from tests.e2e.e2e_test_v2 import build_parser; "
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
