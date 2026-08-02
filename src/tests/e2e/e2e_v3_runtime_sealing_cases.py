"""Real hardware-free runner tests for direct-run manifest sealing.

These tests drive the actual ``run_e2e_v3`` finalization tail
(``e2e_test_v3.py:1433-1463``) through deterministic fakes and prove the
Wave-1 Todo 3 runner-level acceptance contract that helper-only tests
cannot cover:

    * A requested seal with successful or faulted publication preserves
      the frozen PASS/FAIL headline bytes and exit code while the
      sidecar and summary projection independently carry the sealing
      disposition.
    * Exactly one ``Manifest sealing:`` console line appears per run
      under normal INFO logging (no stdout/stderr duplication).
    * A FAIL migration remains FAIL with its original exit code whether
      or not sealing is requested.

The faulted-seal case injects an ``OSError`` at the service seam
(``core.manifest_sealing_runner.seal_root_manifest``) via the existing
``ManifestSealingFaultHooks`` mechanism so the real service runs its
cleanup path while the runner's finalization/headline/return path
executes unchanged.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from pydantic import TypeAdapter

import core.manifest_sealing_runner as sealing_runner_module
from core.artifact_store import ArtifactStore
from core.manifest_sealing import seal_root_manifest as _original_seal_root_manifest
from core.manifest_sealing_models import (
    MANIFEST_SEALING_FILENAME,
    ManifestSealingFaultHooks,
    ManifestSealingResult,
)
from core.run_outcome import TerminalAnchor

from .e2e_v3_runtime_fixture import RuntimeScenario, run_runtime_scenario

_SIDECAR_ADAPTER: TypeAdapter[dict[str, object]] = TypeAdapter(dict[str, object])
_SUMMARY_ADAPTER: TypeAdapter[dict[str, object]] = TypeAdapter(dict[str, object])


def _raise_oserror() -> None:
    raise OSError("injected runner-level sealing fault")


def _headline(stdout: str) -> str:
    for line in stdout.splitlines():
        stripped = line.strip()
        if "E2E PASS" in stripped or "E2E FAIL" in stripped:
            return stripped
    return ""


def _sealing_lines(text: str) -> list[str]:
    return [
        line
        for line in text.splitlines()
        if line.strip().startswith("Manifest sealing:")
    ]


def _read_sidecar(report_dir: Path) -> dict[str, object]:
    return _SIDECAR_ADAPTER.validate_json(
        (report_dir / MANIFEST_SEALING_FILENAME).read_bytes()
    )


def _read_summary_projection(report_dir: Path) -> dict[str, object] | None:
    summary = _SUMMARY_ADAPTER.validate_json(
        (report_dir / "summary.json").read_bytes()
    )
    raw = summary.get("manifest_sealing")
    if not isinstance(raw, dict):
        return None
    return _SUMMARY_ADAPTER.validate_python(raw)


def test_v3_runtime_seal_preserves_pass_headline_and_exit_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Given a deterministic PASS migration. When run with seal_manifest=False vs seal_manifest=True. Then both runs exit 0 with identical ``E2E PASS`` headlines and the sealed run additionally publishes a succeeded sidecar and summary projection."""
    no_seal = run_runtime_scenario(
        tmp_path,
        monkeypatch,
        RuntimeScenario(run_hex="f0" * 16, seal_manifest=False),
    )
    no_seal_stdout = capsys.readouterr().out

    seal = run_runtime_scenario(
        tmp_path,
        monkeypatch,
        RuntimeScenario(run_hex="f1" * 16, seal_manifest=True),
    )
    seal_stdout = capsys.readouterr().out

    assert no_seal.exit_code == 0
    assert seal.exit_code == 0
    assert _headline(no_seal_stdout) == _headline(seal_stdout)
    assert "E2E PASS" in _headline(no_seal_stdout)

    sidecar = _read_sidecar(seal.report_dir)
    assert sidecar["status"] == "succeeded"
    assert sidecar["continuation_eligible"] is True
    projection = _read_summary_projection(seal.report_dir)
    assert projection is not None
    assert projection["status"] == "succeeded"


def test_v3_runtime_faulted_seal_preserves_pass_headline_and_exit_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Given a real no-seal PASS baseline and a PASS migration with a fault injected at the sealing service seam. When both run through the real runner finalization tail. Then the faulted-seal run produces an identical ``E2E PASS`` headline and identical exit code while the sidecar and summary projection independently say failed / not eligible."""
    baseline = run_runtime_scenario(
        tmp_path,
        monkeypatch,
        RuntimeScenario(run_hex="f6" * 16, seal_manifest=False),
    )
    baseline_stdout = capsys.readouterr().out

    def faulted_seal(
        *,
        report_dir: Path,
        run_id: str,
        project_dir: Path,
        workflow_path: Path,
        artifact_store: ArtifactStore | None,
        terminal_anchor: TerminalAnchor,
        hooks: ManifestSealingFaultHooks | None = None,
    ) -> ManifestSealingResult:
        del hooks
        return _original_seal_root_manifest(
            report_dir=report_dir,
            run_id=run_id,
            project_dir=project_dir,
            workflow_path=workflow_path,
            artifact_store=artifact_store,
            terminal_anchor=terminal_anchor,
            hooks=ManifestSealingFaultHooks(
                before_evidence_publish=_raise_oserror
            ),
        )

    monkeypatch.setattr(sealing_runner_module, "seal_root_manifest", faulted_seal)

    result = run_runtime_scenario(
        tmp_path,
        monkeypatch,
        RuntimeScenario(run_hex="f2" * 16, seal_manifest=True),
    )
    stdout = capsys.readouterr().out

    assert result.exit_code == baseline.exit_code
    assert _headline(stdout) == _headline(baseline_stdout)
    assert "E2E PASS" in _headline(stdout)
    sidecar = _read_sidecar(result.report_dir)
    assert sidecar["status"] == "failed"
    assert sidecar["continuation_eligible"] is False
    projection = _read_summary_projection(result.report_dir)
    assert projection is not None
    assert projection["status"] == "failed"


def test_v3_runtime_seal_preserves_fail_headline_and_exit_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Given a deterministic FAIL migration (validation exits nonzero). When run with seal_manifest=False vs seal_manifest=True. Then both runs exit 1 with identical ``E2E FAIL`` headlines; sealing does not rescue or further penalise the migration."""
    no_seal = run_runtime_scenario(
        tmp_path,
        monkeypatch,
        RuntimeScenario(run_hex="f3" * 16, seal_manifest=False, validation_fails=True),
    )
    no_seal_stdout = capsys.readouterr().out

    seal = run_runtime_scenario(
        tmp_path,
        monkeypatch,
        RuntimeScenario(run_hex="f4" * 16, seal_manifest=True, validation_fails=True),
    )
    seal_stdout = capsys.readouterr().out

    assert no_seal.exit_code == 1
    assert seal.exit_code == 1
    assert _headline(no_seal_stdout) == _headline(seal_stdout)
    assert "E2E FAIL" in _headline(no_seal_stdout)

    sidecar = _read_sidecar(seal.report_dir)
    assert sidecar["status"] == "succeeded"
    assert sidecar["continuation_eligible"] is True
    projection = _read_summary_projection(seal.report_dir)
    assert projection is not None
    assert projection["status"] == "succeeded"
    summary = _SUMMARY_ADAPTER.validate_json(
        (seal.report_dir / "summary.json").read_bytes()
    )
    assert summary["overall_status"] == "FAIL"


def test_v3_runtime_seal_emits_exactly_one_manifest_sealing_line(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Given a real runner run with seal_manifest=True under INFO logging. When the finalization tail executes. Then stdout contains exactly one ``Manifest sealing:`` line and stderr contains none, proving no stdout/INFO duplication."""
    handler = logging.StreamHandler()
    handler.setLevel(logging.INFO)
    root = logging.getLogger()
    root.addHandler(handler)
    prior_level = root.level
    root.setLevel(logging.INFO)
    try:
        _ = run_runtime_scenario(
            tmp_path,
            monkeypatch,
            RuntimeScenario(run_hex="f5" * 16, seal_manifest=True),
        )
    finally:
        root.removeHandler(handler)
        root.setLevel(prior_level)
    captured = capsys.readouterr()

    assert len(_sealing_lines(captured.out)) == 1
    assert len(_sealing_lines(captured.err)) == 0
