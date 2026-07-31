import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest

from scripts.e2e_smoke_test import MockSessionManager
from validators.validate_entry_static import validate as validate_entry_static
from validators.validate_env_detect import validate as validate_env_detect

PROJECT_ROOT = Path(__file__).resolve().parent.parent

BASH = shutil.which("bash")
NEEDS_BASH = pytest.mark.skipif(BASH is None, reason="bash is required for wrapper argv tests")

# Fake SEAM_PYTHON: stands in for a Python 3.10+ interpreter. It records the
# argv of the final ``-m tests.e2e.e2e_test_v3`` launch (one token per line)
# and no-ops every other invocation (runtime probe, OpenCode diagnostics).
FAKE_SEAM_PYTHON = (
    "#!/usr/bin/env bash\n"
    'case " $* " in\n'
    '  *" -m tests.e2e.e2e_test_v3 "*)\n'
    '    for a in "$@"; do printf \'%s\\n\' "$a" >> "$SEAM_ARGV_RECORD"; done\n'
    "    ;;\n"
    "esac\n"
    "exit 0\n"
)

WRAPPER_TIMEOUT_SECONDS = 180


def _run_wrapper_via_bash(
    tmp_path: Path,
    launcher: str,
    cli_args: list[str],
) -> tuple[int, str, str, list[str] | None]:
    """Capture the wrapper's Python-harness argv; tokens is None if never launched."""
    project_dir = tmp_path / "proj with space"
    project_dir.mkdir(parents=True, exist_ok=True)
    recorder = tmp_path / "fake_seam_python.sh"
    recorder.write_bytes(FAKE_SEAM_PYTHON.encode("utf-8"))
    recorder.chmod(0o755)
    record_file = tmp_path / "argv-record.txt"

    env = os.environ.copy()
    env["PYTHON"] = str(recorder)
    env["SEAM_ARGV_RECORD"] = str(record_file)
    env["SEAM_T1_PROJECT"] = str(project_dir)
    # WSLENV /p asks WSL to translate the Windows paths; on native Linux the
    # values are already POSIX paths and WSLENV is ignored.
    env["WSLENV"] = "PYTHON/p:SEAM_ARGV_RECORD/p:SEAM_T1_PROJECT/p"

    driver = f'exec bash scripts/{launcher} "$SEAM_T1_PROJECT" "$@"\n'
    result = subprocess.run(
        ["bash", "-s", "--", *cli_args],
        input=driver.encode("utf-8"),
        capture_output=True,
        cwd=PROJECT_ROOT,
        env=env,
        timeout=WRAPPER_TIMEOUT_SECONDS,
        check=False,
    )
    stdout = result.stdout.decode("utf-8", errors="replace")
    stderr = result.stderr.decode("utf-8", errors="replace")
    tokens = None
    if record_file.exists():
        tokens = record_file.read_text(encoding="utf-8").splitlines()
    return result.returncode, stdout, stderr, tokens


def _run_e2e_v3(
    tmp_path: Path, cli_args: list[str]
) -> tuple[int, str, str, list[str] | None]:
    return _run_wrapper_via_bash(tmp_path, "run_e2e_v3.sh", cli_args)


def test_verify_improvements_accepts_seam_execution_root() -> None:
    script = (PROJECT_ROOT / "scripts" / "verify_improvements.sh").read_bytes()
    result = subprocess.run(
        [
            "bash",
            "-s",
            "--",
            "--output-dir",
            ".",
            "--repo-root",
            "..",
        ],
        cwd=PROJECT_ROOT,
        input=script.replace(b"\r\n", b"\n"),
        capture_output=True,
        check=False,
    )
    stdout = result.stdout.decode("utf-8", errors="replace")
    stderr = result.stderr.decode("utf-8", errors="replace")

    assert result.returncode == 0, stdout + stderr
    assert "Results: 4 passed, 0 failed" in stdout


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


def test_public_launcher_invokes_v3_wrapper_with_bash() -> None:
    """The public launcher should not require run_e2e_v3.sh to have +x."""
    launcher = PROJECT_ROOT / "scripts" / "run_seam.sh"
    content = launcher.read_text(encoding="utf-8")

    assert 'exec bash "$SRC_DIR/scripts/run_e2e_v3.sh"' in content


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


@NEEDS_BASH
def test_run_e2e_v3_non_dashboard_extra_args_keep_order(tmp_path: Path) -> None:
    rc, stdout, stderr, tokens = _run_e2e_v3(
        tmp_path,
        [
            "--agent",
            "Agent Name With Spaces",
            "--verbose",
            "--extra",
            "--trace-alpha beta",
            "--max-iter",
            "3",
        ],
    )

    assert rc == 0, stdout + stderr
    assert tokens is not None, "wrapper never launched the Python harness"
    assert tokens[-5:] == [
        "--agent",
        "Agent Name With Spaces",
        "--verbose",
        "--trace-alpha",
        "beta",
    ]
    assert tokens[tokens.index("--max-phase5-iter") + 1] == "3"
    assert tokens[tokens.index("--container-retention") + 1] == "retain"
    assert tokens[tokens.index("--opencode-readiness") + 1] == "off"
    project_token = tokens[tokens.index("--project-dir") + 1]
    assert project_token.endswith("proj with space")


@NEEDS_BASH
@pytest.mark.parametrize(
    ("cli_args", "expected_tail"),
    [
        (["--dashboard"], ["--dashboard"]),
        (["--no-dashboard"], ["--no-dashboard"]),
        (["--dashboard-mode", "on"], ["--dashboard-mode", "on"]),
        (["--dashboard-mode", "off"], ["--dashboard-mode", "off"]),
        (["--dashboard-mode", "auto"], ["--dashboard-mode", "auto"]),
        (
            ["--dashboard", "--dashboard-mode", "on", "--agent", "Agent Name With Spaces"],
            ["--dashboard", "--dashboard-mode", "on", "--agent", "Agent Name With Spaces"],
        ),
        (
            ["--verbose", "--no-dashboard", "--dashboard-mode", "off"],
            ["--verbose", "--no-dashboard", "--dashboard-mode", "off"],
        ),
    ],
)
def test_run_e2e_v3_dashboard_flags_stay_distinct_argv_tokens(
    cli_args: list[str], expected_tail: list[str], tmp_path: Path
) -> None:
    rc, stdout, stderr, tokens = _run_e2e_v3(tmp_path, cli_args)

    assert rc == 0, stdout + stderr
    assert tokens is not None, "wrapper never launched the Python harness"
    assert tokens[-len(expected_tail) :] == expected_tail


@NEEDS_BASH
def test_run_e2e_v3_dry_run_lists_dashboard_tokens_distinctly(tmp_path: Path) -> None:
    rc, stdout, stderr, _ = _run_e2e_v3(
        tmp_path, ["--dashboard", "--dashboard-mode", "on", "--verbose", "--dry-run"]
    )

    assert rc == 0, stdout + stderr
    assert "Would execute:" in stdout
    would_execute = stdout.split("Would execute:", 1)[1]
    display_tokens = shlex.split(would_execute.replace("\\\n", " "))
    assert display_tokens.count("--dashboard") == 1
    assert display_tokens.count("--dashboard-mode") == 1
    assert display_tokens[display_tokens.index("--dashboard-mode") + 1] == "on"
    assert display_tokens.count("--verbose") == 1


@NEEDS_BASH
@pytest.mark.parametrize(
    "cli_args",
    [
        ["--dashboard-mode"],
        ["--dashboard-mode", "banana"],
        ["--dashboard-mode", "ON"],
        ["--dashboard-mode", "auto on"],
    ],
)
def test_run_e2e_v3_dashboard_mode_validation_is_actionable(
    cli_args: list[str], tmp_path: Path
) -> None:
    rc, stdout, stderr, tokens = _run_e2e_v3(tmp_path, cli_args)

    assert rc != 0, stdout + stderr
    assert "--dashboard-mode requires one of: auto, on, off" in stdout + stderr
    assert tokens is None, "invalid --dashboard-mode must not launch the Python harness"


@NEEDS_BASH
def test_public_launcher_forwards_dashboard_mode_tokens(tmp_path: Path) -> None:
    rc, stdout, stderr, tokens = _run_wrapper_via_bash(
        tmp_path, "run_seam.sh", ["--dashboard-mode", "off", "--verbose"]
    )

    assert rc == 0, stdout + stderr
    assert tokens is not None, "wrapper never launched the Python harness"
    assert tokens.count("--dashboard-mode") == 1
    assert tokens[tokens.index("--dashboard-mode") + 1] == "off"
    assert tokens.count("--verbose") == 1
    assert tokens[tokens.index("--container-retention") + 1] == "retain"
