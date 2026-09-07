from __future__ import annotations

from pathlib import Path

import pytest

from .e2e_v3_continuation_shell_cases import _run_launcher, _wsl_path


@pytest.mark.parametrize("script_name", ("run_seam.sh", "run_e2e_v3.sh"))
@pytest.mark.parametrize(
    "flag",
    ("--save-agent-trace", "--no-save-agent-trace"),
)
def test_v3_launcher_forwards_trace_policy(
    tmp_path: Path,
    script_name: str,
    flag: str,
) -> None:
    # Given an explicit raw-trace policy at either public shell boundary.
    summary_path = tmp_path / "shell-runtime" / "parent-run" / "summary.json"
    summary_path.parent.mkdir(parents=True)
    _ = summary_path.write_text("{}", encoding="utf-8")

    # When the checked-out launcher builds the Python command.
    completed = _run_launcher(
        tmp_path,
        script_name,
        "--continue-from",
        _wsl_path(summary_path),
        flag,
        "--dry-run",
    )

    # Then the exact positive or negative token is forwarded once.
    assert completed.returncode == 0, completed.stderr
    command = completed.stdout.split("Would execute:", maxsplit=1)[1]
    assert command.count(flag) == 1


@pytest.mark.parametrize("script_name", ("run_seam.sh", "run_e2e_v3.sh"))
def test_v3_launcher_omits_trace_policy_by_default(
    tmp_path: Path,
    script_name: str,
) -> None:
    # Given a public dry-run with no raw-trace policy.
    summary_path = tmp_path / "shell-runtime" / "parent-run" / "summary.json"
    summary_path.parent.mkdir(parents=True)
    _ = summary_path.write_text("{}", encoding="utf-8")

    # When the checked-out launcher builds the Python command.
    completed = _run_launcher(
        tmp_path,
        script_name,
        "--continue-from",
        _wsl_path(summary_path),
        "--dry-run",
    )

    # Then default off performs no explicit trace request.
    assert completed.returncode == 0, completed.stderr
    command = completed.stdout.split("Would execute:", maxsplit=1)[1]
    assert "--save-agent-trace" not in command
    assert "--no-save-agent-trace" not in command


@pytest.mark.parametrize("script_name", ("run_seam.sh", "run_e2e_v3.sh"))
def test_v3_launcher_rejects_conflicting_trace_policy(
    tmp_path: Path,
    script_name: str,
) -> None:
    # Given contradictory raw-trace options.
    summary_path = tmp_path / "shell-runtime" / "parent-run" / "summary.json"
    summary_path.parent.mkdir(parents=True)
    _ = summary_path.write_text("{}", encoding="utf-8")

    # When either shell boundary parses the conflict.
    completed = _run_launcher(
        tmp_path,
        script_name,
        "--continue-from",
        _wsl_path(summary_path),
        "--save-agent-trace",
        "--no-save-agent-trace",
        "--dry-run",
    )

    # Then parsing fails before Python execution.
    assert completed.returncode != 0
    assert "conflicting agent trace options" in completed.stderr.lower()
