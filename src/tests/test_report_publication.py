from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.run_outcome import TerminalOutcome
from harness.run.report_publication import (
    ReportPublicationError,
    ReportPublisher,
)


class FakeStore:
    def __init__(self, artifact_dir: Path, output: dict[str, object] | None) -> None:
        self.artifact_dir = str(artifact_dir)
        self.output = output

    def load_phase_output(self, phase_id: str) -> dict[str, object] | None:
        assert phase_id == "phase_6_report"
        return self.output


def _store_with_reports(
    tmp_path: Path, names: tuple[str, ...] = ("SUMMARY_REPORT.md", "TOOLS.md")
) -> FakeStore:
    artifact_dir = tmp_path / "project" / ".sm-artifacts" / "run-1"
    reports_dir = artifact_dir / "reports"
    reports_dir.mkdir(parents=True)
    paths: list[str] = []
    for name in names:
        path = reports_dir / name
        path.write_text(f"content for {name}\n", encoding="utf-8")
        paths.append(str(path))
    return FakeStore(artifact_dir, {"report_paths": paths})


def test_publishes_reports_and_manifest(tmp_path: Path) -> None:
    store = _store_with_reports(tmp_path)
    project = tmp_path / "migrated"
    project.mkdir()

    update = ReportPublisher(store, project)(TerminalOutcome.PASSED)

    report_dir = project / "migration_reports"
    assert (report_dir / "SUMMARY_REPORT.md").read_text(encoding="utf-8")
    assert (report_dir / "TOOLS.md").is_file()
    manifest = json.loads(
        (report_dir / "report_manifest.json").read_text(encoding="utf-8")
    )
    assert [item["name"] for item in manifest["reports"]] == [
        "SUMMARY_REPORT.md",
        "TOOLS.md",
    ]
    assert dict(update.directory_paths)["migration_reports_dir"] == str(report_dir)
    assert all(len(item["sha256"]) == 64 for item in manifest["reports"])


def test_rejects_report_path_outside_canonical_root(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifact"
    (artifact_dir / "reports").mkdir(parents=True)
    outside = tmp_path / "SUMMARY_REPORT.md"
    outside.write_text("outside", encoding="utf-8")
    store = FakeStore(artifact_dir, {"report_paths": [str(outside)]})

    with pytest.raises(ReportPublicationError, match="escapes"):
        ReportPublisher(store, tmp_path / "migrated").publish()


def test_requires_summary_report(tmp_path: Path) -> None:
    store = _store_with_reports(tmp_path, names=("TOOLS.md",))

    with pytest.raises(ReportPublicationError, match="SUMMARY_REPORT"):
        ReportPublisher(store, tmp_path / "migrated").publish()


def test_failed_run_does_not_require_or_publish_reports(tmp_path: Path) -> None:
    project = tmp_path / "migrated"
    store = FakeStore(tmp_path / "artifact", None)

    update = ReportPublisher(store, project)(TerminalOutcome.FAILED)

    assert update.directory_paths == ()
    assert not project.exists()


def test_rejects_symlinked_destination_directory(tmp_path: Path) -> None:
    store = _store_with_reports(tmp_path)
    project = tmp_path / "migrated"
    outside = tmp_path / "outside"
    project.mkdir()
    outside.mkdir()
    (project / "migration_reports").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ReportPublicationError, match="must not be a symlink"):
        ReportPublisher(store, project).publish()

    assert list(outside.iterdir()) == []
