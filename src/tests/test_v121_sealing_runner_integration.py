"""Focused runner/summary integration proofs for the wired direct-run sealing.

These tests prove the Wave-1 Todo 3 runner-integration contract via the
:mod:`core.manifest_sealing_runner` helper that the E2E coordinator
calls after the ordinary finalization result is frozen. They cover the
five dispositions the workplan names (success, failure, omission,
sidecar fault, summary-projection fault) and the absolute user
invariant: a direct sealing fault never mutates the frozen E2E
``overall_status``, the PASS/FAIL headline bytes, or the pre-sealing
exit code, while the sidecar, console line, and summary projection
independently surface the sealing result.

The helper is exercised directly with synthetic frozen-finalization
state (a pre-written ``summary.json`` and the in-scope runner inputs)
so the full E2E machinery is not required to prove the contract.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import TypeAdapter

from core.artifact_store import ArtifactStore
from core.manifest_sealing_models import (
    MANIFEST_SEALING_FILENAME,
    ManifestSealingFaultHooks,
    ManifestSealingStatus,
)
from core.manifest_sealing_projection import (
    ManifestSealingProjectionStatus,
)
from core.manifest_sealing_runner import (
    DirectManifestSealingReport,
    run_direct_manifest_sealing,
)
from core.run_outcome import PhaseId, TerminalAnchor

_TERMINAL_ANCHOR = TerminalAnchor(phase_id=PhaseId("phase_5_validation"))
_RUN_ID = "r1"
_SUMMARY_ADAPTER: TypeAdapter[dict[str, object]] = TypeAdapter(dict[str, object])


def _workflow_file(tmp_path: Path) -> Path:
    workflow = tmp_path / "workflow.yaml"
    _ = workflow.write_text("phase: 1\n", encoding="utf-8")
    return workflow


def _project_dir(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir(parents=True, exist_ok=True)
    _ = (project / "setup.py").write_text("name = 'demo'\n", encoding="utf-8")
    return project


def _report_dir(tmp_path: Path, name: str = _RUN_ID) -> Path:
    report_dir = tmp_path / name
    report_dir.mkdir(parents=True, exist_ok=True)
    return report_dir


def _artifact_store(project_dir: Path) -> ArtifactStore:
    store = ArtifactStore(str(project_dir), _RUN_ID)
    _ = store.mark_validated("phase_3_entry_script", {"status": "success"})
    return store


def _raise_injected() -> None:
    raise OSError("injected publication fault")


def _hooks(fault_at: str | None) -> ManifestSealingFaultHooks | None:
    if fault_at is None:
        return None
    if fault_at == "before_evidence_publish":
        return ManifestSealingFaultHooks(before_evidence_publish=_raise_injected)
    if fault_at == "before_manifest_commit":
        return ManifestSealingFaultHooks(before_manifest_commit=_raise_injected)
    return ManifestSealingFaultHooks(before_sidecar_publish=_raise_injected)


def _write_summary(report_dir: Path, overall_status: str) -> Path:
    summary_path = report_dir / "summary.json"
    payload: dict[str, object] = {
        "run_id": _RUN_ID,
        "overall_status": overall_status,
        "phases": (),
        "errors": (),
    }
    _ = summary_path.write_text(json.dumps(payload), encoding="utf-8")
    return summary_path


def _load_summary(summary_path: Path) -> dict[str, object]:
    return _SUMMARY_ADAPTER.validate_json(summary_path.read_bytes())


def _load_sidecar(report_dir: Path) -> dict[str, object]:
    return _SUMMARY_ADAPTER.validate_json(
        (report_dir / MANIFEST_SEALING_FILENAME).read_bytes()
    )


def _run(
    tmp_path: Path,
    *,
    seal_requested: bool,
    overall_status: str,
    fault_at: str | None = None,
    is_continuation: bool = False,
    summary_override: Path | None = None,
) -> tuple[DirectManifestSealingReport, Path, Path]:
    report_dir = _report_dir(tmp_path)
    project_dir = _project_dir(tmp_path)
    summary_path = (
        summary_override
        if summary_override is not None
        else _write_summary(report_dir, overall_status)
    )
    report = run_direct_manifest_sealing(
        seal_requested=seal_requested,
        is_continuation=is_continuation,
        report_dir=report_dir,
        run_id=_RUN_ID,
        project_dir=project_dir,
        workflow_path=_workflow_file(tmp_path),
        artifact_store=_artifact_store(project_dir),
        terminal_anchor=_TERMINAL_ANCHOR,
        summary_path=summary_path,
        hooks=_hooks(fault_at),
    )
    return report, report_dir, summary_path


def test_sealing_success_keeps_pass_summary_and_projects_eligible(
    tmp_path: Path,
) -> None:
    """Given a frozen PASS summary. When a successful direct seal runs. Then overall_status stays PASS, the sidecar says succeeded, and the summary projection says continuation_eligible=true."""
    report, report_dir, summary_path = _run(
        tmp_path, seal_requested=True, overall_status="PASS"
    )

    assert report.result.status is ManifestSealingStatus.SUCCEEDED
    assert report.projection.status is ManifestSealingProjectionStatus.PROJECTED
    summary = _load_summary(summary_path)
    assert summary["overall_status"] == "PASS"
    projection = summary["manifest_sealing"]
    assert isinstance(projection, dict)
    assert projection["status"] == "succeeded"
    assert projection["continuation_eligible"] is True
    sidecar = _load_sidecar(report_dir)
    assert sidecar["status"] == "succeeded"


def test_sealing_failure_keeps_pass_summary_and_projects_not_eligible(
    tmp_path: Path,
) -> None:
    """Given a frozen PASS summary. When an injected sealing fault runs. Then overall_status stays PASS, the sidecar says failed, and the summary projection says continuation_eligible=false."""
    report, report_dir, summary_path = _run(
        tmp_path,
        seal_requested=True,
        overall_status="PASS",
        fault_at="before_evidence_publish",
    )

    assert report.result.status is ManifestSealingStatus.FAILED
    assert report.result.continuation_eligible is False
    summary = _load_summary(summary_path)
    assert summary["overall_status"] == "PASS"
    projection = summary["manifest_sealing"]
    assert isinstance(projection, dict)
    assert projection["status"] == "failed"
    assert projection["continuation_eligible"] is False
    sidecar = _load_sidecar(report_dir)
    assert sidecar["status"] == "failed"
    assert sidecar["continuation_eligible"] is False


def test_sealing_failure_keeps_fail_summary_unchanged(tmp_path: Path) -> None:
    """Given a frozen FAIL summary. When an injected sealing fault runs. Then overall_status stays FAIL with its original exit-code semantics and the sidecar independently says failed."""
    report, report_dir, summary_path = _run(
        tmp_path,
        seal_requested=True,
        overall_status="FAIL",
        fault_at="before_manifest_commit",
    )

    assert report.result.status is ManifestSealingStatus.FAILED
    summary = _load_summary(summary_path)
    assert summary["overall_status"] == "FAIL"
    sidecar = _load_sidecar(report_dir)
    assert sidecar["status"] == "failed"


def test_sealing_omission_writes_not_requested_sidecar(tmp_path: Path) -> None:
    """Given a frozen summary and no seal requested. When the runner helper runs. Then the sidecar says not_requested, the run is not eligible, and the summary overall_status is unchanged."""
    report, report_dir, summary_path = _run(
        tmp_path, seal_requested=False, overall_status="PASS"
    )

    assert report.result.status is ManifestSealingStatus.NOT_REQUESTED
    assert report.result.continuation_eligible is False
    summary = _load_summary(summary_path)
    assert summary["overall_status"] == "PASS"
    sidecar = _load_sidecar(report_dir)
    assert sidecar["status"] == "not_requested"
    assert sidecar["continuation_eligible"] is False


def test_sealing_continuation_always_records_not_requested(tmp_path: Path) -> None:
    """Given a continuation run. When seal_requested is True. Then the helper records not_requested because a continuation child must consume the parent's already-sealed evidence rather than re-seal its own root manifest."""
    report, _report_dir, _summary_path = _run(
        tmp_path,
        seal_requested=True,
        overall_status="PASS",
        is_continuation=True,
    )

    assert report.result.status is ManifestSealingStatus.NOT_REQUESTED
    assert report.result.requested is False


def test_requested_seal_with_unavailable_inputs_is_failed_not_not_requested(
    tmp_path: Path,
) -> None:
    """Given a requested direct seal with project_dir=None and workflow_path=None. When the runner helper runs. Then the result is failed with requested=True and continuation_eligible=False, never not_requested, because a requested-but-impossible seal is a typed failure, not an omission."""
    report_dir = _report_dir(tmp_path)
    summary_path = _write_summary(report_dir, "PASS")

    report = run_direct_manifest_sealing(
        seal_requested=True,
        is_continuation=False,
        report_dir=report_dir,
        run_id=_RUN_ID,
        project_dir=None,
        workflow_path=None,
        artifact_store=None,
        terminal_anchor=_TERMINAL_ANCHOR,
        summary_path=summary_path,
    )

    assert report.result.status is ManifestSealingStatus.FAILED
    assert report.result.requested is True
    assert report.result.continuation_eligible is False
    sidecar = _load_sidecar(report_dir)
    assert sidecar["status"] == "failed"
    assert sidecar["requested"] is True
    summary = _load_summary(summary_path)
    assert summary["overall_status"] == "PASS"


def test_sidecar_fault_stays_outcome_neutral(tmp_path: Path) -> None:
    """Given a frozen PASS summary. When the sidecar publish faults after the manifest+evidence are committed. Then the result is failed (fail-closed on the eligibility signal) while the summary overall_status stays PASS and the helper does not raise."""
    report, _report_dir, summary_path = _run(
        tmp_path,
        seal_requested=True,
        overall_status="PASS",
        fault_at="before_sidecar_publish",
    )

    assert report.result.status is ManifestSealingStatus.FAILED
    assert report.result.continuation_eligible is False
    summary = _load_summary(summary_path)
    assert summary["overall_status"] == "PASS"


def test_projection_fault_stays_outcome_neutral(tmp_path: Path) -> None:
    """Given a frozen summary that cannot be re-parsed. When the projection runs. Then the projection outcome is FAILED, the sealing result is still independently observable via the sidecar, and the helper does not raise."""
    report_dir = _report_dir(tmp_path)
    project_dir = _project_dir(tmp_path)
    corrupt_summary = report_dir / "summary.json"
    _ = corrupt_summary.write_text("{ not valid json", encoding="utf-8")

    report = run_direct_manifest_sealing(
        seal_requested=True,
        is_continuation=False,
        report_dir=report_dir,
        run_id=_RUN_ID,
        project_dir=project_dir,
        workflow_path=_workflow_file(tmp_path),
        artifact_store=_artifact_store(project_dir),
        terminal_anchor=_TERMINAL_ANCHOR,
        summary_path=corrupt_summary,
    )

    assert report.projection.status is ManifestSealingProjectionStatus.FAILED
    sidecar = _load_sidecar(report_dir)
    assert sidecar["status"] in {"succeeded", "failed"}


def test_manifest_sealing_line_follows_headline_without_overwriting(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Given the E2E headline already printed. When the runner helper runs. Then stdout contains exactly one ``Manifest sealing:`` line that follows the headline and never emits an ``E2E PASS`` or ``E2E FAIL`` line, proving the sealing result is observable without rewriting the headline."""
    print("E2E PASS")
    print("- Output dir: /tmp/demo")
    _report, _report_dir, _summary_path = _run(
        tmp_path, seal_requested=True, overall_status="PASS"
    )

    captured = capsys.readouterr().out
    lines = [line for line in captured.splitlines() if line.strip()]
    headline_index = next(
        i for i, line in enumerate(lines) if line.startswith("E2E PASS")
    )
    sealing_indices = [
        i for i, line in enumerate(lines) if line.startswith("Manifest sealing:")
    ]
    assert len(sealing_indices) == 1
    assert sealing_indices[0] > headline_index
    assert not any("E2E FAIL" in line for line in lines)
