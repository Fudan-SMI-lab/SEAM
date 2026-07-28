from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


SRC_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = SRC_ROOT.parent


def _wsl_path(path: Path) -> str:
    resolved = path.resolve()
    drive = resolved.drive.removesuffix(":").lower()
    suffix = resolved.as_posix().split(":", maxsplit=1)[1]
    return f"/mnt/{drive}{suffix}"


def _run_launcher(
    tmp_path: Path,
    script_name: str,
    *arguments: str,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    shell_root = tmp_path / "shell-runtime"
    shell_scripts = shell_root / "src" / "scripts"
    shell_scripts.mkdir(parents=True)
    for source in (SRC_ROOT / "scripts").glob("run_*.sh"):
        normalized = source.read_text(encoding="utf-8").replace("\r\n", "\n")
        with (shell_scripts / source.name).open(
            "w", encoding="utf-8", newline="\n"
        ) as destination:
            _ = destination.write(normalized)
    diagnostics_dir = shell_root / "scripts"
    diagnostics_dir.mkdir()
    _ = shutil.copyfile(
        REPO_ROOT / "scripts" / "diagnose_seam_opencode.py",
        diagnostics_dir / "diagnose_seam_opencode.py",
    )
    return subprocess.run(
        ["bash", _wsl_path(shell_scripts / script_name), *arguments],
        cwd=shell_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=60,
        env=environment,
    )


def _argument_recording_environment(
    tmp_path: Path,
) -> tuple[dict[str, str], Path]:
    argument_log = tmp_path / "python-arguments.log"
    fake_python = tmp_path / "python-arguments.sh"
    _ = fake_python.write_text(
        '#!/usr/bin/env bash\nfor argument in "$@"; do printf "<%s>\\n" "$argument"; done > "$ARGUMENT_LOG"\n',
        encoding="utf-8",
        newline="\n",
    )
    environment = dict(os.environ)
    environment.update(
        {
            "PYTHON": _wsl_path(fake_python),
            "ARGUMENT_LOG": _wsl_path(argument_log),
            "WSLENV": ":".join(
                item
                for item in (environment.get("WSLENV"), "PYTHON", "ARGUMENT_LOG")
                if item
            ),
        }
    )
    return environment, argument_log


@pytest.mark.parametrize("script_name", ("run_seam.sh", "run_e2e_v3.sh"))
def test_v3_launcher_checked_out_bytes_parse_directly(script_name: str) -> None:
    # Given
    script = SRC_ROOT / "scripts" / script_name

    # When
    completed = subprocess.run(
        ["bash", "-n", _wsl_path(script)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=60,
    )

    # Then
    assert b"\r" not in script.read_bytes()
    assert completed.returncode == 0, completed.stderr


def test_v3_launcher_defers_session_diagnostics_until_after_ownership(
    tmp_path: Path,
) -> None:
    # Given
    shell_root = tmp_path / "shell-runtime"
    summary = shell_root / "parent" / "summary.json"
    summary.parent.mkdir(parents=True)
    _ = summary.write_text("{}", encoding="utf-8")
    probe_log = tmp_path / "python-invocations.log"
    fake_python = tmp_path / "python-probe.sh"
    _ = fake_python.write_text(
        '#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "$PROBE_LOG"\n',
        encoding="utf-8",
        newline="\n",
    )
    environment = dict(os.environ)
    environment.update(
        {
            "PYTHON": _wsl_path(fake_python),
            "PROBE_LOG": _wsl_path(probe_log),
            "WSLENV": ":".join(
                item
                for item in (environment.get("WSLENV"), "PYTHON", "PROBE_LOG")
                if item
            ),
        }
    )

    # When
    completed = _run_launcher(
        tmp_path,
        "run_e2e_v3.sh",
        "--continue-from",
        _wsl_path(summary),
        "--opencode-readiness",
        "message",
        environment=environment,
    )

    # Then
    assert completed.returncode == 0, completed.stderr
    invocations = probe_log.read_text(encoding="utf-8")
    assert "--mode message" not in invocations
    assert "-m tests.e2e.e2e_test_v3" in invocations


@pytest.mark.parametrize("script_name", ("run_seam.sh", "run_e2e_v3.sh"))
def test_v3_launcher_forwards_continuation_without_project(
    tmp_path: Path,
    script_name: str,
) -> None:
    # Given
    summary_path = tmp_path / "shell-runtime" / "parent-run" / "summary.json"
    summary_path.parent.mkdir(parents=True)
    _ = summary_path.write_text("{}", encoding="utf-8")
    summary = _wsl_path(summary_path)

    # When
    completed = _run_launcher(
        tmp_path,
        script_name,
        "--continue-from",
        summary,
        "--dry-run",
    )

    # Then
    assert completed.returncode == 0, completed.stderr
    command = completed.stdout.split("Would execute:", maxsplit=1)[1]
    assert f"--continue-from {summary}" in command
    assert "--project-dir" not in command
    assert "--workflow-path" not in command


@pytest.mark.parametrize("script_name", ("run_seam.sh", "run_e2e_v3.sh"))
@pytest.mark.parametrize("mode", ("missing", "conflicting"))
def test_v3_launcher_rejects_invalid_run_mode(
    tmp_path: Path,
    script_name: str,
    mode: str,
) -> None:
    # Given
    arguments = (
        ()
        if mode == "missing"
        else ("project", "--continue-from", "/tmp/parent/summary.json")
    )

    # When
    completed = _run_launcher(tmp_path, script_name, *arguments)

    # Then
    assert completed.returncode != 0
    assert "required" in completed.stderr or "mutually exclusive" in completed.stderr


@pytest.mark.parametrize("script_name", ("run_seam.sh", "run_e2e_v3.sh"))
def test_v3_launcher_canonicalizes_relative_continuation_summary(
    tmp_path: Path,
    script_name: str,
) -> None:
    # Given
    relative = Path("relative-parent") / "summary.json"
    shell_root = tmp_path / "shell-runtime"
    summary = shell_root / relative
    summary.parent.mkdir(parents=True)
    _ = summary.write_text("{}", encoding="utf-8")

    # When
    completed = _run_launcher(
        tmp_path,
        script_name,
        "--continue-from",
        relative.as_posix(),
        "--dry-run",
    )

    # Then
    assert completed.returncode == 0, completed.stderr
    command = completed.stdout.split("Would execute:", maxsplit=1)[1]
    assert f"--continue-from {relative.as_posix()}" not in command
    assert f"--continue-from {_wsl_path(summary)}" in command


@pytest.mark.parametrize("script_name", ("run_seam.sh", "run_e2e_v3.sh"))
@pytest.mark.parametrize("requested", (None, "delete"))
def test_v3_launcher_forwards_container_retention(
    tmp_path: Path,
    script_name: str,
    requested: str | None,
) -> None:
    # Given a public V3 dry-run with omitted or explicit retention policy.
    summary_path = tmp_path / "shell-runtime" / "parent-run" / "summary.json"
    summary_path.parent.mkdir(parents=True)
    _ = summary_path.write_text("{}", encoding="utf-8")
    arguments = [
        "--continue-from",
        _wsl_path(summary_path),
        "--dry-run",
    ]
    if requested is not None:
        arguments.extend(["--container-retention", requested])

    # When either checked-in launcher builds the Python command.
    completed = _run_launcher(tmp_path, script_name, *arguments)

    # Then retain is the default and explicit delete is forwarded exactly.
    assert completed.returncode == 0, completed.stderr
    expected = requested or "retain"
    command = completed.stdout.split("Would execute:", maxsplit=1)[1]
    assert f"--container-retention {expected}" in command


@pytest.mark.parametrize("script_name", ("run_seam.sh", "run_e2e_v3.sh"))
def test_v3_launcher_rejects_conflicting_container_retention(
    tmp_path: Path,
    script_name: str,
) -> None:
    # Given contradictory retention values at the public shell boundary.
    summary_path = tmp_path / "shell-runtime" / "parent-run" / "summary.json"
    summary_path.parent.mkdir(parents=True)
    _ = summary_path.write_text("{}", encoding="utf-8")
    arguments = (
        "--continue-from",
        _wsl_path(summary_path),
        "--container-retention",
        "retain",
        "--container-retention",
        "delete",
        "--dry-run",
    )

    # When the launcher parses the conflict.
    completed = _run_launcher(tmp_path, script_name, *arguments)

    # Then it fails before constructing a Python invocation.
    assert completed.returncode != 0
    assert "conflicting container retention" in completed.stderr.lower()


@pytest.mark.parametrize(
    "injected",
    ("--container-retention delete", "--container-retention=delete"),
)
def test_v3_launcher_rejects_retention_smuggling_through_extra(
    tmp_path: Path,
    injected: str,
) -> None:
    # Given a hidden retention override at the public --extra boundary.
    completed = _run_launcher(
        tmp_path,
        "run_e2e_v3.sh",
        "project",
        "--extra",
        injected,
    )

    # Then parsing fails before project discovery or Python execution.
    assert completed.returncode != 0
    assert "cannot be supplied through --extra" in completed.stderr


def test_v3_launcher_keeps_hostile_agent_value_as_one_argument(tmp_path: Path) -> None:
    # Given an agent name containing text that resembles a retention override.
    shell_root = tmp_path / "shell-runtime"
    summary = shell_root / "parent" / "summary.json"
    summary.parent.mkdir(parents=True)
    _ = summary.write_text("{}", encoding="utf-8")
    environment, argument_log = _argument_recording_environment(tmp_path)
    hostile_agent = "worker --container-retention delete"

    # When the real launcher invokes the Python entrypoint.
    completed = _run_launcher(
        tmp_path,
        "run_e2e_v3.sh",
        "--continue-from",
        _wsl_path(summary),
        "--opencode-readiness",
        "off",
        "--agent",
        hostile_agent,
        environment=environment,
    )

    # Then the hostile text remains one value and retain remains authoritative.
    assert completed.returncode == 0, completed.stderr
    arguments = argument_log.read_text(encoding="utf-8").splitlines()
    assert f"<{hostile_agent}>" in arguments
    assert arguments.count("<--container-retention>") == 1
    retention_index = arguments.index("<--container-retention>")
    assert arguments[retention_index + 1] == "<retain>"
    assert "<delete>" not in arguments


@pytest.mark.parametrize("vector", ("workflow", "constraints"))
def test_v3_launcher_keeps_hostile_path_as_one_argument(
    tmp_path: Path,
    vector: str,
) -> None:
    # Given a workflow or constraints path containing retention-like tokens.
    project_name = (
        "project --container-retention delete" if vector == "constraints" else "project"
    )
    project = tmp_path / "shell-runtime" / "original_projects" / project_name
    project.mkdir(parents=True)
    if vector == "constraints":
        _ = (project / "ADAPTATION_REQUIREMENTS.md").write_text(
            "retain resources\n",
            encoding="utf-8",
        )
    environment, argument_log = _argument_recording_environment(tmp_path)
    arguments = [project_name, "--opencode-readiness", "off"]
    if vector == "workflow":
        arguments.extend(["--workflow", "workflow.yaml --container-retention delete"])

    # When the real launcher builds the Python argument vector.
    completed = _run_launcher(
        tmp_path,
        "run_e2e_v3.sh",
        *arguments,
        environment=environment,
    )

    # Then retain occurs exactly once and hostile text is never reparsed.
    assert completed.returncode == 0, completed.stderr
    recorded = argument_log.read_text(encoding="utf-8").splitlines()
    assert recorded.count("<--container-retention>") == 1
    retention_index = recorded.index("<--container-retention>")
    assert recorded[retention_index + 1] == "<retain>"
    assert "<delete>" not in recorded
