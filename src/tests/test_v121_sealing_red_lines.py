"""Green proofs: outcome-neutral-but-observable sealing wired into the runner.

These tests lock the *wired* direct-run root-manifest sealing contract for
Wave-1 Todos 2 and 3 of the v1.2.1 remote-update remediation workplan:

    * Sealing status is independently observable: a
      ``manifest-sealing.v1.json`` sidecar (with status
      ``not_requested|succeeded|failed`` and ``continuation_eligible``)
      is written for every direct run, and a separate ``Manifest
      sealing:`` line follows the E2E headline. Direct sealing failure
      never mutates ``RunOutcome``, the headline, or the pre-sealing
      exit code.
    * The root ``run-manifest.v1.json`` is published LAST as the only
      authority commit marker. Sealed evidence is published (and
      fsynced) FIRST; if evidence publication fails the report directory
      contains NO root manifest claiming incomplete evidence.

At the c6cbed3 baseline the legacy ``_seal_root_run_manifest`` (i)
caught broad ``Exception`` and returned ``False`` without writing any
status sidecar, (ii) was invoked with its boolean result ignored, and
(iii) ``shutil.copy2``-published ``run-manifest.v1.json`` BEFORE
``shutil.copytree``-publishing ``sealed-artifacts``. Wave-1 Todos 2 and
3 replace that helper with the crash-safe :mod:`core.manifest_sealing`
service wired through :mod:`core.manifest_sealing_runner`; the tests
below prove the new contract via the typed service and runner helper.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from pydantic import TypeAdapter

from core.artifact_store import ArtifactStore
from core.manifest_sealing import seal_root_manifest
from core.manifest_sealing_models import (
    MANIFEST_SEALING_FILENAME,
    ManifestSealingFaultHooks,
    ManifestSealingResult,
    ManifestSealingStatus,
)
from core.manifest_sealing_runner import run_direct_manifest_sealing
from core.run_outcome import PhaseId, TerminalAnchor

_TERMINAL_ANCHOR = TerminalAnchor(phase_id=PhaseId("phase_5_validation"))
_RUN_ID = "r1"
_SIDECAR_ADAPTER: TypeAdapter[dict[str, object]] = TypeAdapter(dict[str, object])


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


def _failed_seal(tmp_path: Path) -> tuple[ManifestSealingResult, Path]:
    report_dir = _report_dir(tmp_path, "run-failed")
    project_dir = _project_dir(tmp_path)
    result = seal_root_manifest(
        report_dir=report_dir,
        run_id="run-failed",
        project_dir=project_dir,
        workflow_path=_workflow_file(tmp_path),
        artifact_store=_artifact_store(project_dir),
        terminal_anchor=_TERMINAL_ANCHOR,
        hooks=ManifestSealingFaultHooks(before_evidence_publish=_raise_injected),
    )
    return result, report_dir


def _load_sidecar(path: Path) -> dict[str, object]:
    return _SIDECAR_ADAPTER.validate_json(path.read_bytes())


def test_sealing_failure_writes_observable_status_sidecar(tmp_path: Path) -> None:
    """Given a fault-injected sealing invocation. When the service returns. Then ``manifest-sealing.v1.json`` exists with status ``failed`` and ``continuation_eligible == False`` so the failure is observable without mutating the E2E outcome or exit code."""
    result, report_dir = _failed_seal(tmp_path)

    assert result.status is ManifestSealingStatus.FAILED
    assert result.continuation_eligible is False
    sidecar = report_dir / MANIFEST_SEALING_FILENAME
    assert sidecar.exists(), (
        "Direct-run sealing failure must publish manifest-sealing.v1.json "
        "with status=failed/continuation_eligible=False so the caller can "
        "surface the result without mutating the E2E outcome or exit code."
    )
    payload = _load_sidecar(sidecar)
    assert payload["status"] == "failed"
    assert payload["continuation_eligible"] is False


def test_sealing_failure_emits_independent_manifest_sealing_line(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Given a fault-injected direct-run sealing. When the runner helper runs. Then stdout contains exactly one line starting with ``Manifest sealing:`` that indicates failure, proving the sealing result is observable independently from the E2E headline without any stdout/INFO duplication."""
    report_dir = _report_dir(tmp_path, "run-failed-log")
    project_dir = _project_dir(tmp_path)

    _ = run_direct_manifest_sealing(
        seal_requested=True,
        is_continuation=False,
        report_dir=report_dir,
        run_id="run-failed-log",
        project_dir=project_dir,
        workflow_path=_workflow_file(tmp_path),
        artifact_store=_artifact_store(project_dir),
        terminal_anchor=_TERMINAL_ANCHOR,
        summary_path=None,
        hooks=ManifestSealingFaultHooks(before_evidence_publish=_raise_injected),
    )

    captured = capsys.readouterr()
    sealing_lines = [
        line
        for line in captured.out.splitlines()
        if line.strip().startswith("Manifest sealing:")
    ]
    assert len(sealing_lines) == 1, (
        "Direct-run sealing must emit exactly one 'Manifest sealing: ...' "
        "console line independent from the E2E PASS/FAIL headline so the "
        "result is observable without rewriting the headline or exit code."
    )
    assert "failed" in sealing_lines[0]


def test_root_manifest_is_published_after_sealed_evidence() -> None:
    """Given the ``seal_root_manifest`` service source. When the source is inspected for the relative ordering of the ``run-manifest.v1.json`` publication (``atomic_create_bytes``) and the ``sealed-artifacts`` publication (``rename_directory_no_replace``). Then the ``sealed-artifacts`` publication must appear BEFORE the ``run-manifest.v1.json`` publication, because the root manifest is the LAST artifact and the only authority commit marker; publishing it first leaves a manifest that claims incomplete evidence when the sealed-artifacts publication is interrupted."""
    source = inspect.getsource(seal_root_manifest)
    evidence_marker = "rename_directory_no_replace(evidence_source, evidence_path)"
    manifest_marker = "atomic_create_bytes(manifest_path, manifest_bytes)"

    evidence_pos = source.find(evidence_marker)
    manifest_pos = source.find(manifest_marker)

    assert evidence_pos != -1, "test setup: sealed-evidence publish token not found"
    assert manifest_pos != -1, "test setup: root-manifest publish token not found"

    assert evidence_pos < manifest_pos, (
        "sealed-artifacts must be published BEFORE run-manifest.v1.json so "
        "the root manifest is the LAST commit marker; the c6cbed3 legacy "
        "helper copied run-manifest.v1.json before sealed-artifacts and was "
        "replaced by the crash-safe service in core.manifest_sealing."
    )
