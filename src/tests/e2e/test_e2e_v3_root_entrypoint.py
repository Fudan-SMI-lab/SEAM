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
    assert "--opencode-readiness" in completed.stdout
    assert "--opencode-message-timeout" in completed.stdout


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


def test_v2_entrypoint_unaffected() -> None:
    """Verify V2 was NOT modified: no --workflow-path and --project-dir present.

    Checks source files directly since V2 --help subprocess fails due to
    pre-existing sqlite3 absence in this Python 3.10 build (conftest stub
    does not apply to subprocess invocations).
    """
    v2_inner = Path(__file__).resolve().parents[2] / "tests" / "e2e" / "e2e_test_v2.py"
    v2_core = v2_inner.read_text(encoding="utf-8")
    assert "--workflow-path" not in v2_core, "V2 must not have --workflow-path"
    assert "--project-dir" in v2_core, "V2 must still have --project-dir"

    v2_wrapper = Path(__file__).resolve().parents[3] / "tests" / "e2e" / "e2e_test_v2.py"
    assert v2_wrapper.exists(), "V2 root wrapper must exist"

    v2_shell = Path(__file__).resolve().parents[2] / "scripts" / "run_e2e_v2.sh"
    assert v2_shell.exists(), "V2 shell wrapper must exist"
    shell_content = v2_shell.read_text(encoding="utf-8")
    assert "--workflow" not in shell_content, "V2 shell must not have --workflow"


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
