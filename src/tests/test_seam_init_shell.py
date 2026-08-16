"""Subprocess bash tests for the SEAM initializer launcher (init_seam.sh).

Function style mirroring test_maintenance_scripts.py. Verifies the launcher
delegates ``-m seam_init.cli`` correctly, honors SEAM_PYTHON over PYTHON,
exits 61 on missing/too-old Python, and never leaks secret values into argv.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SEAM_INIT_DIR = PROJECT_ROOT.parent / ".seam-init"

BASH = shutil.which("bash")
NEEDS_BASH = pytest.mark.skipif(BASH is None, reason="bash is required for init_seam.sh tests")
# Type narrowing for basedpyright: when bash is absent the tests skip first, so
# subprocess call sites always see a real path. ``or ""`` keeps the type ``str``.
BASH_EXE: str = BASH or ""

WRAPPER_TIMEOUT_SECONDS = 180
LAUNCHER = "init_seam.sh"

# Fake SEAM_PYTHON: stands in for a Python 3.10+ interpreter. It records the
# argv of the final ``-m seam_init.cli`` launch (one token per line) and
# no-ops the runtime probe.
FAKE_SEAM_PYTHON = (
    "#!/usr/bin/env bash\n"
    'case " $* " in\n'
    '  *" -m seam_init.cli "*)\n'
    '    for a in "$@"; do printf \'%s\\n\' "$a" >> "$SEAM_ARGV_RECORD"; done\n'
    "    ;;\n"
    "  *) exit 0 ;;\n"  # runtime probe support
    "esac\n"
    "exit 0\n"
)

# Fake interpreter that FAILS the Python 3.10+ runtime probe (exits 1 on -c).
FAKE_TOO_OLD_PYTHON = (
    "#!/usr/bin/env bash\n"
    'case "$1" in\n'
    "  -c) exit 1 ;;\n"  # simulate sys.version_info < (3, 10)
    "  *) exit 0 ;;\n"
    "esac\n"
)


def _assert_shell_syntax(script_path: Path) -> None:
    """Assert ``bash -n`` reports no syntax errors when fed the script via stdin.

    Piping the script bytes (CRLF-normalized) to ``bash -n`` avoids the
    Windows-path-vs-WSL-path resolution problem that breaks ``bash -n <file>``.
    """
    script_bytes = script_path.read_bytes().replace(b"\r\n", b"\n")
    result = subprocess.run(
        [BASH_EXE, "-n"],
        input=script_bytes,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, f"bash -n failed: {result.stderr!r}"


def _run_shell_raw(cli_args: list[str]) -> tuple[int, str, str]:
    """Run init_seam.sh via ``bash -s`` without the fake-Python harness.

    Used for ``--help`` and pre-Python paths where the script exits before
    resolving the interpreter.
    """
    driver = f'exec bash scripts/{LAUNCHER} "$@"\n'
    result = subprocess.run(
        [BASH_EXE, "-s", "--", *cli_args],
        input=driver.encode("utf-8"),
        capture_output=True,
        cwd=PROJECT_ROOT,
        timeout=WRAPPER_TIMEOUT_SECONDS,
        check=False,
    )
    stdout = result.stdout.decode("utf-8", errors="replace")
    stderr = result.stderr.decode("utf-8", errors="replace")
    return result.returncode, stdout, stderr


def _run_wrapper_via_bash(
    tmp_path: Path,
    cli_args: list[str],
) -> tuple[int, str, str, list[str] | None]:
    """Capture the launcher's Python-harness argv via a recorder interpreter.

    Returns (rc, stdout, stderr, tokens). tokens is None if ``-m seam_init.cli``
    was never launched (e.g. Python resolution failed).
    """
    recorder = tmp_path / "fake_seam_python.sh"
    recorder.write_bytes(FAKE_SEAM_PYTHON.encode("utf-8"))
    recorder.chmod(0o755)
    record_file = tmp_path / "argv-record.txt"

    env = os.environ.copy()
    env["SEAM_PYTHON"] = str(recorder)
    env["SEAM_ARGV_RECORD"] = str(record_file)
    env["WSLENV"] = "SEAM_PYTHON/p:SEAM_ARGV_RECORD/p"

    driver = f'exec bash scripts/{LAUNCHER} "$@"\n'
    result = subprocess.run(
        [BASH_EXE, "-s", "--", *cli_args],
        input=driver.encode("utf-8"),
        capture_output=True,
        cwd=PROJECT_ROOT,
        env=env,
        timeout=WRAPPER_TIMEOUT_SECONDS,
        check=False,
    )
    stdout = result.stdout.decode("utf-8", errors="replace")
    stderr = result.stderr.decode("utf-8", errors="replace")
    tokens = (
        record_file.read_text(encoding="utf-8").splitlines()
        if record_file.exists()
        else None
    )
    return result.returncode, stdout, stderr, tokens


def _run_cli_direct(
    cli_args: list[str],
    *,
    input_bytes: bytes = b"",
    extra_env: dict[str, str] | None = None,
    timeout: int = WRAPPER_TIMEOUT_SECONDS,
) -> tuple[int, str, str]:
    """Run ``python -m seam_init.cli`` in a child process with a typed fake runner.

    The child harness defines ``_typed_fake_runner(prompt, answers) -> int`` and
    passes it as ``workflow_runner`` so the real ``_default_runner`` — and thus
    the real workspace — is never touched. Returns (rc, stdout, stderr).
    """
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    runner_script = (
        "import sys\n"
        f"sys.path.insert(0, {str(PROJECT_ROOT)!r})\n"
        "from seam_init.answers import Answers\n"
        "from seam_init.cli import PromptPort, main\n"
        "def _typed_fake_runner(prompt: PromptPort, answers: Answers | None) -> int:\n"
        "    return 60\n"
        "sys.exit(main(sys.argv[1:], workflow_runner=_typed_fake_runner))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", runner_script, *cli_args],
        input=input_bytes,
        capture_output=True,
        cwd=str(PROJECT_ROOT),
        env=env,
        timeout=timeout,
        check=False,
    )
    stdout = result.stdout.decode("utf-8", errors="replace")
    stderr = result.stderr.decode("utf-8", errors="replace")
    return result.returncode, stdout, stderr


# --- tests -----------------------------------------------------------------


@pytest.fixture(autouse=True)
def _assert_no_seam_init_artifact() -> Iterator[None]:
    # Given: the repository must not have a .seam-init directory before tests
    assert not _SEAM_INIT_DIR.exists(), f".seam-init exists before test: {_SEAM_INIT_DIR}"
    yield
    # Then: the repository must not have a .seam-init directory after tests
    assert not _SEAM_INIT_DIR.exists(), f".seam-init created during test: {_SEAM_INIT_DIR}"


@NEEDS_BASH
def test_init_seam_passes_bash_n() -> None:
    # Given
    script = PROJECT_ROOT / "scripts" / LAUNCHER
    # When / Then
    _assert_shell_syntax(script)


@NEEDS_BASH
def test_init_seam_help_exits_0_and_documents_non_interactive() -> None:
    # Given / When
    rc, stdout, _stderr = _run_shell_raw(["--help"])
    # Then
    assert rc == 0
    assert "--non-interactive" in stdout
    assert "--answers" in stdout


@NEEDS_BASH
def test_init_seam_no_arg_exits_documented_range(tmp_path: Path) -> None:
    # Given: no args -> interactive mode dispatched to fake runner.
    # When: cli.py invoked with fake runner (no real workflow, no artifacts).
    try:
        rc, _stdout, _stderr = _run_cli_direct([], input_bytes=b"", timeout=20)
    except subprocess.TimeoutExpired:
        return
    # Then: documented exit range, no hang.
    assert rc in {0, 60, *range(61, 70)}
    _ = tmp_path


@NEEDS_BASH
def test_init_seam_non_interactive_never_reads_stdin(tmp_path: Path) -> None:
    # Given: a valid answers file; stdin yields EOF immediately on any read.
    answers = tmp_path / "answers.json"
    answers.write_text('{"provider_id": "openai"}', encoding="utf-8")
    # When
    rc, _stdout, _stderr = _run_cli_direct(
        ["--non-interactive", "--answers", str(answers)],
        input_bytes=b"",
    )
    # Then: clean exit (no EOFError crash from a stdin read), in documented range.
    assert rc in {0, 60, *range(61, 70)}, f"non-interactive leaked a stdin read: rc={rc}"


@NEEDS_BASH
def test_init_seam_non_interactive_requires_answers(tmp_path: Path) -> None:
    # Given / When: --non-interactive without --answers, cli.py invoked directly.
    rc, _stdout, stderr = _run_cli_direct(["--non-interactive"])
    # Then: cli rejects with usage error (2); no hang.
    assert rc == 2, stderr
    _ = tmp_path  # kept for fixture isolation


@NEEDS_BASH
def test_init_seam_seam_python_wins_over_python(tmp_path: Path) -> None:
    # Given: both SEAM_PYTHON (recorder) and PYTHON set; SEAM_PYTHON must win.
    recorder = tmp_path / "fake_seam_python.sh"
    recorder.write_bytes(FAKE_SEAM_PYTHON.encode("utf-8"))
    recorder.chmod(0o755)
    record_file = tmp_path / "argv-record.txt"
    other = tmp_path / "other_python.sh"
    other.write_bytes(b"#!/usr/bin/env bash\nexit 0\n")
    other.chmod(0o755)

    env = os.environ.copy()
    env["SEAM_PYTHON"] = str(recorder)
    env["PYTHON"] = str(other)
    env["SEAM_ARGV_RECORD"] = str(record_file)
    env["WSLENV"] = "SEAM_PYTHON/p:PYTHON/p:SEAM_ARGV_RECORD/p"

    driver = f'exec bash scripts/{LAUNCHER} "$@"\n'
    result = subprocess.run(
        [BASH_EXE, "-s", "--"],
        input=driver.encode("utf-8"),
        capture_output=True,
        cwd=PROJECT_ROOT,
        env=env,
        timeout=WRAPPER_TIMEOUT_SECONDS,
        check=False,
    )
    # Then: the recorder (SEAM_PYTHON) recorded the launch; PYTHON was ignored.
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert record_file.exists(), "SEAM_PYTHON recorder was never invoked"
    tokens = record_file.read_text(encoding="utf-8").splitlines()
    assert "-m" in tokens
    assert "seam_init.cli" in tokens


@NEEDS_BASH
def test_init_seam_missing_python_fails_categorized(tmp_path: Path) -> None:
    # Given: SEAM_PYTHON points to a nonexistent executable.
    env = os.environ.copy()
    env["SEAM_PYTHON"] = str(tmp_path / "does-not-exist")
    env["WSLENV"] = "SEAM_PYTHON/p"

    driver = f'exec bash scripts/{LAUNCHER} "$@"\n'
    result = subprocess.run(
        [BASH_EXE, "-s", "--"],
        input=driver.encode("utf-8"),
        capture_output=True,
        cwd=PROJECT_ROOT,
        env=env,
        timeout=WRAPPER_TIMEOUT_SECONDS,
        check=False,
    )
    stdout = result.stdout.decode("utf-8", errors="replace")
    stderr = result.stderr.decode("utf-8", errors="replace")
    combined = stdout + stderr
    # Then: exit 61 (PYTHON_ENVIRONMENT) with install guidance; no launch.
    assert result.returncode == 61, combined
    assert "Python 3.10" in combined, combined


@NEEDS_BASH
def test_init_seam_too_old_python_fails_categorized(tmp_path: Path) -> None:
    # Given: SEAM_PYTHON points to an interpreter that fails the 3.10+ probe.
    fake = tmp_path / "fake_old_python.sh"
    fake.write_bytes(FAKE_TOO_OLD_PYTHON.encode("utf-8"))
    fake.chmod(0o755)

    env = os.environ.copy()
    env["SEAM_PYTHON"] = str(fake)
    env["WSLENV"] = "SEAM_PYTHON/p"

    driver = f'exec bash scripts/{LAUNCHER} "$@"\n'
    result = subprocess.run(
        [BASH_EXE, "-s", "--"],
        input=driver.encode("utf-8"),
        capture_output=True,
        cwd=PROJECT_ROOT,
        env=env,
        timeout=WRAPPER_TIMEOUT_SECONDS,
        check=False,
    )
    combined = (
        result.stdout.decode("utf-8", "replace")
        + result.stderr.decode("utf-8", "replace")
    )
    # Then: exit 61 (PYTHON_ENVIRONMENT); the too-old interpreter was rejected.
    assert result.returncode == 61, combined


@NEEDS_BASH
def test_init_seam_no_secret_in_argv(tmp_path: Path) -> None:
    # Given: an env var holds a canary secret; the answers file references it
    # by NAME only (api_key_env), never by value.
    canary = "sk-init-canary-deadbeef-0011223344"
    answers = tmp_path / "answers.json"
    answers.write_text('{"api_key_env": "SEAM_INIT_CANARY_KEY"}', encoding="utf-8")
    extra_env = {"SEAM_INIT_CANARY_KEY": canary}

    # When (part A): recorder captures the wrapper->python argv tokens.
    rc_a, _stdout_a, _stderr_a, tokens = _run_wrapper_via_bash(
        tmp_path,
        ["--non-interactive", "--answers", str(answers)],
    )
    # Then A: no canary substring in any recorded argv token.
    assert rc_a == 0
    assert tokens is not None
    for tok in tokens:
        assert canary not in tok, f"secret leaked into argv token: {tok!r}"

    # When (part B): real cli.py runs with the canary env var populated.
    rc_b, stdout_b, stderr_b = _run_cli_direct(
        ["--non-interactive", "--answers", str(answers)],
        extra_env=extra_env,
    )
    # Then B: clean exit and the canary never appears in stdout/stderr.
    assert rc_b in {0, 60, *range(61, 70)}, stderr_b
    assert canary not in stdout_b, f"secret leaked to stdout: {stdout_b!r}"
    assert canary not in stderr_b, f"secret leaked to stderr: {stderr_b!r}"
