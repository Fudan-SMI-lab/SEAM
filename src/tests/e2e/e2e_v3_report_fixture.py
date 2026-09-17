"""Supply real report files for runtime fixtures that omit the reporting agent."""
from pathlib import Path

import pytest

from harness.run.report_publication import ReportPublisher


def install_report_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    original = ReportPublisher.publish

    def publish_with_fixture(publisher: ReportPublisher) -> Path:
        store = publisher.artifact_store
        output = store.load_phase_output("phase_6_report")
        if not output or not output.get("report_paths"):
            root = Path(store.artifact_dir) / "reports"
            root.mkdir(parents=True, exist_ok=True)
            report = root / "SUMMARY_REPORT.md"
            report.write_text("# Runtime fixture report\n", encoding="utf-8")
            store.mark_validated("phase_6_report", {"report_paths": [str(report)]})
        return original(publisher)

    monkeypatch.setattr(ReportPublisher, "publish", publish_with_fixture)
