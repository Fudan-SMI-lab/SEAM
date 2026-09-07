from __future__ import annotations

from pathlib import Path

import pytest

from harness.project_preflight import (
    ProjectPreflightError,
    validate_project_input,
)


def test_rejects_missing_project(tmp_path: Path) -> None:
    with pytest.raises(ProjectPreflightError, match="does not exist"):
        validate_project_input(tmp_path / "missing")


def test_rejects_non_directory(tmp_path: Path) -> None:
    project_file = tmp_path / "project.py"
    project_file.write_text("print('hello')", encoding="utf-8")

    with pytest.raises(ProjectPreflightError, match="not a directory"):
        validate_project_input(project_file)


def test_rejects_empty_project(tmp_path: Path) -> None:
    project = tmp_path / "empty"
    project.mkdir()

    with pytest.raises(ProjectPreflightError, match="no migratable files"):
        validate_project_input(project)


def test_rejects_project_with_only_generated_metadata(tmp_path: Path) -> None:
    project = tmp_path / "metadata-only"
    (project / ".git").mkdir(parents=True)
    (project / ".git" / "config").write_text("[core]", encoding="utf-8")
    (project / "migration_reports").mkdir()
    (project / "migration_reports" / "SUMMARY_REPORT.md").write_text(
        "old report", encoding="utf-8"
    )
    (project / ".DS_Store").write_bytes(b"metadata")

    with pytest.raises(ProjectPreflightError, match="no migratable files"):
        validate_project_input(project)


def test_accepts_any_regular_project_file(tmp_path: Path) -> None:
    project = tmp_path / "cpp-project"
    source = project / "src" / "kernel.cu"
    source.parent.mkdir(parents=True)
    source.write_text("__global__ void kernel() {}", encoding="utf-8")

    result = validate_project_input(project)

    assert result.project_dir == project.resolve()
    assert result.regular_file_count == 1
    assert result.meaningful_file_count == 1


def test_counts_file_symlink_without_following_directory_symlinks(
    tmp_path: Path,
) -> None:
    external = tmp_path / "external"
    external.mkdir()
    (external / "model.py").write_text("pass", encoding="utf-8")
    project = tmp_path / "linked-project"
    project.mkdir()
    (project / "external-dir").symlink_to(external, target_is_directory=True)

    result = validate_project_input(project)

    assert result.meaningful_file_count == 1
    assert result.regular_file_count == 0


def test_v3_runner_rejects_empty_project_before_report_or_server_side_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.e2e import e2e_test_v3 as target

    project = tmp_path / "empty"
    project.mkdir()

    def forbidden(*_args: object, **_kwargs: object) -> object:
        pytest.fail("empty project must fail before allocating reports")

    monkeypatch.setattr(target, "allocate_report_directory", forbidden)

    result = target.run_e2e_v3(
        base_url="http://127.0.0.1:4098",
        max_phase5_iter=1,
        keep_temp_dir=True,
        agent_name=None,
        project_dir=project,
        server_auto_start=False,
        opencode_readiness="off",
    )

    assert result == 1
