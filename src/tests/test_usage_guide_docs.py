"""Usage-guide and public dashboard documentation contract (Todo 9).

The generated USAGE guide must expose the active ``ui_events.jsonl`` path only
when the dashboard sink is active, and every public doc / launcher help must
document the same optional-dashboard contract: the exact install command,
AUTO/ON/OFF behavior, Textual->Rich fallback, explicit-ON missing-dependency
error, ``q`` semantics, and ``ui_events.jsonl`` location.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from core.dashboard import DASHBOARD_INSTALL_COMMAND
from tests.e2e.e2e_test_v3 import write_usage_guide

ROOT = Path(__file__).resolve().parents[2]

# Every public surface that must carry the same dashboard contract.
PUBLIC_DASHBOARD_DOCS = (
    ROOT / "README.md",
    ROOT / "README.en.md",
    ROOT / "README.zh.md",
    ROOT / "src" / "README.md",
    ROOT / "src" / "docs" / "E2E_TESTING.md",
)
RUN_SEAM = ROOT / "src" / "scripts" / "run_seam.sh"

INSTALL_COMMAND = DASHBOARD_INSTALL_COMMAND
UI_EVENTS_FILE = "ui_events.jsonl"


# --- Baseline: existing usage-guide fields must remain valid ---


def test_usage_guide_baseline_preserves_existing_fields(tmp_path: Path) -> None:
    """Given a passing run, the generated USAGE guide keeps its v1.2.0 fields."""
    usage_path = write_usage_guide(
        tmp_path,
        entry_script="python test_data_and_scripts/run_e2e.py",
        overall_status="PASS",
        output_dir=tmp_path / "reports",
    )
    content = Path(usage_path).read_text(encoding="utf-8")
    assert "E2E TEST PASSED" in content
    assert f"cd {tmp_path}" in content
    assert "python test_data_and_scripts/run_e2e.py" in content
    assert ".sm-artifacts/" in content
    assert "migration_reports/USAGE.md" in content


def test_usage_guide_without_ui_events_path_omits_ui_telemetry(
    tmp_path: Path,
) -> None:
    """OFF / direct calls must not claim or create UI telemetry."""
    usage_path = write_usage_guide(
        tmp_path,
        entry_script="python run.py",
        overall_status="PASS",
        output_dir=tmp_path / "reports",
    )
    content = Path(usage_path).read_text(encoding="utf-8")
    assert UI_EVENTS_FILE not in content
    assert not (tmp_path / UI_EVENTS_FILE).exists()


def test_usage_guide_failed_status_keeps_failure_heading(tmp_path: Path) -> None:
    usage_path = write_usage_guide(
        tmp_path,
        entry_script=None,
        overall_status="FAIL",
        output_dir=tmp_path / "reports",
    )
    content = Path(usage_path).read_text(encoding="utf-8")
    assert "Migration did not fully pass validation" in content
    assert UI_EVENTS_FILE not in content


# --- ON-mode usage must expose the active sink path ---


def test_usage_guide_with_ui_events_path_lists_exact_path_once(
    tmp_path: Path,
) -> None:
    """Given an active sink, the guide lists the exact ui_events path once."""
    sink_path = tmp_path / "reports" / "e2e-v3-run" / UI_EVENTS_FILE
    usage_path = write_usage_guide(
        tmp_path,
        entry_script="python run.py",
        overall_status="PASS",
        output_dir=tmp_path / "reports",
        ui_events_path=str(sink_path),
    )
    content = Path(usage_path).read_text(encoding="utf-8")
    assert str(sink_path) in content
    events_lines = [line for line in content.splitlines() if UI_EVENTS_FILE in line]
    assert len(events_lines) == 1
    reports_idx = content.index("## Reports")
    assert str(sink_path) in content[reports_idx:]


def test_usage_guide_with_none_ui_events_path_omits_entry(tmp_path: Path) -> None:
    """Passing ui_events_path=None is identical to omitting it."""
    usage_path = write_usage_guide(
        tmp_path,
        entry_script="python run.py",
        overall_status="PASS",
        output_dir=tmp_path / "reports",
        ui_events_path=None,
    )
    content = Path(usage_path).read_text(encoding="utf-8")
    assert UI_EVENTS_FILE not in content


# --- Public documentation contract ---


@pytest.mark.parametrize("doc", PUBLIC_DASHBOARD_DOCS)
def test_public_docs_document_full_dashboard_contract(doc: Path) -> None:
    """Every public doc carries the same dashboard contract."""
    text = doc.read_text(encoding="utf-8")
    assert INSTALL_COMMAND in text, f"{doc.name} must quote {INSTALL_COMMAND!r}"
    assert UI_EVENTS_FILE in text, f"{doc.name} must mention {UI_EVENTS_FILE!r}"
    for mode in ("auto", "on", "off"):
        assert mode in text, f"{doc.name} missing mode {mode!r}"
    assert "textual" in text.lower(), f"{doc.name} must mention textual"
    assert "rich" in text.lower(), f"{doc.name} must mention rich"
    assert "`q`" in text, f"{doc.name} must document the q key"


# --- run_seam.sh --help contract (parsed from the source heredoc) ---


def _run_seam_help_body() -> str:
    source = RUN_SEAM.read_text(encoding="utf-8")
    match = re.search(r"cat <<'EOF'\n(?P<body>.*?)\nEOF", source, re.DOTALL)
    assert match, "run_seam.sh usage heredoc not found"
    return match.group("body")


def test_run_seam_help_documents_dashboard_contract() -> None:
    help_text = _run_seam_help_body()
    assert INSTALL_COMMAND in help_text
    assert UI_EVENTS_FILE in help_text
    for mode in ("auto", "on", "off"):
        assert mode in help_text
    assert "textual" in help_text.lower()
    assert "rich" in help_text.lower()
    assert "`q`" in help_text
