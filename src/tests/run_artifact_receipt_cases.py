from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import os
import shutil
import subprocess
import tempfile

import pytest

from core.run_outcome import TerminalOutcome
from harness.run import (
    EMPTY_ARTIFACT_UPDATE,
    FinalizationHooks,
    RunArtifacts,
    RunArtifactUpdate,
    finalize_run,
)
from .run_finalizer_test_support import FinalizerScenario, finalization_request


def _file_symlink(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"file symlink unavailable: {exc}")


def _directory_link(link: Path, target: Path) -> None:
    if os.name == "nt":
        created = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            encoding="mbcs",
            errors="replace",
            check=False,
        )
        if created.returncode != 0:
            pytest.skip(f"junction unavailable: {created.stderr or created.stdout}")
        return
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlink unavailable: {exc}")


@pytest.mark.parametrize(
    ("later_action", "expected_error"),
    [("deleted", "SidecarValidationError"), ("changed", "ArtifactProvenanceError")],
)
def test_hook_artifact_is_revalidated_at_final_freeze(
    tmp_path: Path,
    later_action: str,
    expected_error: str,
) -> None:
    # Given one hook creates a file that a later hook deletes or changes.
    claim = tmp_path / "claim.json"

    def evidence(_outcome: TerminalOutcome) -> RunArtifactUpdate:
        _ = claim.write_text("created", encoding="utf-8")
        return RunArtifactUpdate(telemetry_paths=(("claim", str(claim)),))

    def trace(_outcome: TerminalOutcome) -> RunArtifactUpdate:
        if later_action == "deleted":
            claim.unlink()
        else:
            _ = claim.write_text("changed", encoding="utf-8")
        return EMPTY_ARTIFACT_UPDATE

    request = finalization_request(
        tmp_path,
        FinalizerScenario(
            hooks=FinalizationHooks(evidence_replay=evidence, trace_export=trace)
        ),
    )

    # When finalization freezes the summary after every hook.
    result = finalize_run(request)

    # Then a no-longer-current receipt is diagnosed and omitted.
    assert "claim" not in result.summary.telemetry_paths
    assert any(item.error_type == expected_error for item in result.diagnostics)


def test_hook_cannot_claim_preexisting_unchanged_artifact(tmp_path: Path) -> None:
    # Given a stale file already exists before the hook starts.
    stale = tmp_path / "stale.json"
    _ = stale.write_text("stale", encoding="utf-8")

    def evidence(_outcome: TerminalOutcome) -> RunArtifactUpdate:
        return RunArtifactUpdate(telemetry_paths=(("stale", str(stale)),))

    request = finalization_request(
        tmp_path,
        FinalizerScenario(hooks=FinalizationHooks(evidence_replay=evidence)),
    )

    # When the hook returns the unchanged preexisting path.
    result = finalize_run(request)

    # Then missing hook provenance prevents the stale claim.
    assert "stale" not in result.summary.telemetry_paths
    assert any(
        item.error_type == "ArtifactProvenanceError" for item in result.diagnostics
    )


@pytest.mark.skipif(os.name != "nt", reason="Windows 8.3 path alias regression")
def test_windows_short_path_alias_remains_contained() -> None:
    # Given a report and artifact use the same Windows 8.3 lexical namespace.
    report = Path(tempfile.mkdtemp(prefix="seam-short-path-"))
    artifact = report / "artifact.json"
    _ = artifact.write_text("artifact", encoding="utf-8")
    request = finalization_request(report, FinalizerScenario())
    request = replace(
        request,
        initial_artifacts=RunArtifacts(telemetry_paths=(("artifact", str(artifact)),)),
    )

    # When canonical containment expands the user-directory alias.
    try:
        result = finalize_run(request)
    finally:
        shutil.rmtree(report)

    # Then lexical link checks and canonical containment both accept the file.
    assert result.summary.telemetry_paths == {"artifact": str(artifact)}
    assert result.diagnostics == ()


def test_contained_file_symlink_is_never_accepted(tmp_path: Path) -> None:
    # Given a symlink whose resolved target remains inside the report.
    target = tmp_path / "target.json"
    _ = target.write_text("target", encoding="utf-8")
    link = tmp_path / "linked.json"
    _file_symlink(link, target)

    def evidence(_outcome: TerminalOutcome) -> RunArtifactUpdate:
        return RunArtifactUpdate(telemetry_paths=(("linked", str(link)),))

    # When a hook returns the contained link.
    result = finalize_run(
        finalization_request(
            tmp_path,
            FinalizerScenario(hooks=FinalizationHooks(evidence_replay=evidence)),
        )
    )

    # Then lexical link ancestry is rejected independently of resolve containment.
    assert "linked" not in result.summary.telemetry_paths
    assert result.diagnostics


def test_contained_directory_junction_is_never_accepted(tmp_path: Path) -> None:
    # Given a junction or directory symlink targeting a contained directory.
    target = tmp_path / "target-dir"
    target.mkdir()
    link = tmp_path / "linked-dir"
    _directory_link(link, target)

    def evidence(_outcome: TerminalOutcome) -> RunArtifactUpdate:
        return RunArtifactUpdate(directory_paths=(("linked_dir", str(link)),))

    # When a hook returns the contained directory link.
    result = finalize_run(
        finalization_request(
            tmp_path,
            FinalizerScenario(hooks=FinalizationHooks(evidence_replay=evidence)),
        )
    )

    # Then reparse-point ancestry prevents the claim.
    assert "linked_dir" not in result.summary.telemetry_paths
    assert result.diagnostics


def test_artifact_link_retarget_is_rejected_at_freeze(tmp_path: Path) -> None:
    # Given a hook-created directory is replaced later by a contained link.
    claim = tmp_path / "artifact-dir"
    target = tmp_path / "replacement"
    target.mkdir()

    def evidence(_outcome: TerminalOutcome) -> RunArtifactUpdate:
        claim.mkdir()
        _ = (claim / "created.txt").write_text("created", encoding="utf-8")
        return RunArtifactUpdate(artifact_dir=str(claim))

    def trace(_outcome: TerminalOutcome) -> RunArtifactUpdate:
        shutil.rmtree(claim)
        _directory_link(claim, target)
        return EMPTY_ARTIFACT_UPDATE

    # When finalization reaches the receipt freeze after link retargeting.
    result = finalize_run(
        finalization_request(
            tmp_path,
            FinalizerScenario(
                hooks=FinalizationHooks(evidence_replay=evidence, trace_export=trace)
            ),
        )
    )

    # Then the retargeted artifact directory is omitted and diagnosed.
    assert result.summary.artifact_dir is None
    assert result.diagnostics


@pytest.mark.parametrize("invalid_kind", ["missing", "outside", "wrong-kind"])
def test_invalid_initial_artifacts_use_the_same_trust_boundary(
    tmp_path: Path,
    invalid_kind: str,
) -> None:
    # Given an initial telemetry claim that is missing, outside, wrong-kind, or linked.
    report = tmp_path / "report"
    report.mkdir()
    outside = tmp_path / "outside.json"
    _ = outside.write_text("outside", encoding="utf-8")
    wrong_kind = report / "directory"
    wrong_kind.mkdir()
    target = report / "target.json"
    _ = target.write_text("target", encoding="utf-8")
    candidates = {
        "missing": report / "missing.json",
        "outside": outside,
        "wrong-kind": wrong_kind,
    }
    request = finalization_request(report, FinalizerScenario())
    request = replace(
        request,
        initial_artifacts=RunArtifacts(
            telemetry_paths=(("initial", str(candidates[invalid_kind])),),
            entry_script="python app.py",
        ),
    )

    # When initial artifacts cross finalization's trust boundary.
    result = finalize_run(request)

    # Then the unsafe initial claim is diagnosed and never serialized.
    assert "initial" not in result.summary.telemetry_paths
    assert result.diagnostics


def test_initial_directory_link_uses_the_same_trust_boundary(tmp_path: Path) -> None:
    # Given an initial artifact directory is a contained junction or symlink.
    report = tmp_path / "report"
    report.mkdir()
    target = report / "target-dir"
    target.mkdir()
    link = report / "linked-dir"
    _directory_link(link, target)
    request = finalization_request(report, FinalizerScenario())
    request = replace(
        request,
        initial_artifacts=RunArtifacts(
            artifact_dir=str(link), entry_script="python app.py"
        ),
    )

    # When initial artifacts cross finalization's trust boundary.
    result = finalize_run(request)

    # Then the link claim is diagnosed and omitted.
    assert result.summary.artifact_dir is None
    assert result.diagnostics


def test_valid_initial_artifact_remains_claimed_when_unchanged(tmp_path: Path) -> None:
    # Given a contained regular initial file.
    initial = tmp_path / "initial.json"
    _ = initial.write_text("initial", encoding="utf-8")
    request = finalization_request(tmp_path, FinalizerScenario())
    request = replace(
        request,
        initial_artifacts=RunArtifacts(
            telemetry_paths=(("initial", str(initial)),),
            entry_script="python app.py",
        ),
    )

    # When it remains unchanged through final freeze.
    result = finalize_run(request)

    # Then the receipt remains a current summary claim.
    assert result.summary.telemetry_paths == {"initial": str(initial)}
    assert result.diagnostics == ()


def test_initial_artifact_is_revalidated_at_final_freeze(tmp_path: Path) -> None:
    # Given a valid initial file is changed by a later hook.
    initial = tmp_path / "initial.json"
    _ = initial.write_text("initial", encoding="utf-8")

    def trace(_outcome: TerminalOutcome) -> RunArtifactUpdate:
        _ = initial.write_text("changed", encoding="utf-8")
        return EMPTY_ARTIFACT_UPDATE

    request = finalization_request(
        tmp_path,
        FinalizerScenario(hooks=FinalizationHooks(trace_export=trace)),
    )
    request = replace(
        request,
        initial_artifacts=RunArtifacts(telemetry_paths=(("initial", str(initial)),)),
    )

    # When finalization freezes all receipts.
    result = finalize_run(request)

    # Then the changed initial receipt is diagnosed and omitted.
    assert "initial" not in result.summary.telemetry_paths
    assert any(
        item.error_type == "ArtifactProvenanceError" for item in result.diagnostics
    )
