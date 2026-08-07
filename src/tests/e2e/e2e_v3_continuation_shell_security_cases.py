from __future__ import annotations

from pathlib import Path

import pytest

from .e2e_v3_continuation_shell_cases import (
    _argument_recording_environment,
    _run_launcher,
    _wsl_path,
)


@pytest.mark.parametrize(
    "injected",
    ("--container-retention delete", "--container-retention=delete"),
)
def test_v3_launcher_rejects_retention_smuggling_through_extra(
    tmp_path: Path,
    injected: str,
) -> None:
    completed = _run_launcher(
        tmp_path,
        "run_e2e_v3.sh",
        "project",
        "--extra",
        injected,
    )

    assert completed.returncode != 0
    assert "cannot be supplied through --extra" in completed.stderr


def test_v3_launcher_keeps_hostile_agent_value_as_one_argument(tmp_path: Path) -> None:
    shell_root = tmp_path / "shell-runtime"
    summary = shell_root / "parent" / "summary.json"
    summary.parent.mkdir(parents=True)
    _ = summary.write_text("{}", encoding="utf-8")
    environment, argument_log = _argument_recording_environment(tmp_path)
    hostile_agent = "worker --container-retention delete"

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

    completed = _run_launcher(
        tmp_path,
        "run_e2e_v3.sh",
        *arguments,
        environment=environment,
    )

    assert completed.returncode == 0, completed.stderr
    recorded = argument_log.read_text(encoding="utf-8").splitlines()
    assert recorded.count("<--container-retention>") == 1
    retention_index = recorded.index("<--container-retention>")
    assert recorded[retention_index + 1] == "<retain>"
    assert "<delete>" not in recorded
