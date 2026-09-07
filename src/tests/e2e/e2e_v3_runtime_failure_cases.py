from __future__ import annotations

from pathlib import Path

import pytest

from .e2e_v3_runtime_fakes import SessionScript
from .e2e_v3_runtime_fixture import RuntimeScenario, read_json, run_runtime_scenario


@pytest.mark.parametrize("failure_kind", ["session", "server"])
def test_v3_runtime_cleanup_failures_preserve_frozen_pass_and_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_kind: str,
) -> None:
    # Given one optional cleanup boundary that raises after migration PASS.
    session_script = (
        SessionScript(cleanup_error=RuntimeError("session cleanup failed"))
        if failure_kind == "session"
        else None
    )
    server_error = (
        RuntimeError("server cleanup failed") if failure_kind == "server" else None
    )
    scenario = RuntimeScenario(
        run_hex=("3" if failure_kind == "session" else "4") * 32,
        session_script=session_script,
        server_cleanup_error=server_error,
    )

    # When the actual ordered finalizer runs every cleanup stage.
    result = run_runtime_scenario(tmp_path, monkeypatch, scenario)

    # Then PASS/0 remains frozen and the failure is durable, not swallowed.
    summary = read_json(result.report_dir / "summary.json")
    diagnostics = (result.report_dir / "finalization_diagnostics.json").read_text(
        encoding="utf-8"
    )
    assert result.exit_code == 0
    assert summary["overall_status"] == "PASS"
    assert f"{failure_kind} cleanup failed" in diagnostics
    assert result.validation_count_path.read_text(encoding="utf-8") == "1"
    assert not (result.report_dir / "traceback.txt").exists()


def test_v3_runtime_required_resource_failure_preserves_phase_result_but_exits_one(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a successful migration followed by interrupted required manifest sealing.
    scenario = RuntimeScenario(
        run_hex="5" * 32,
        resource_seal_error=OSError("resource seal interrupted"),
    )

    # When required post-cleanup publication fails.
    result = run_runtime_scenario(tmp_path, monkeypatch, scenario)

    # Then migration evidence stays passed while finalization refuses PASS publication.
    phases = (result.report_dir / "phase_results.json").read_text(encoding="utf-8")
    diagnostics = (result.report_dir / "finalization_diagnostics.json").read_text(
        encoding="utf-8"
    )
    assert result.exit_code == 1
    assert '"status": "passed"' in phases
    assert "resource seal interrupted" in diagnostics
    assert not (result.report_dir / "summary.json").exists()
    assert result.validation_count_path.read_text(encoding="utf-8") == "1"
