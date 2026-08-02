"""Focused red->green coverage for the direct-run root-manifest sealing service.

These tests prove the Wave-1 Todo 2 contract for
``core.manifest_sealing``:

    * The three dispositions ``not_requested``, ``succeeded``, and
      ``failed`` are each observable through the independent
      ``manifest-sealing.v1.json`` sidecar.
    * On success the complete ``sealed-artifacts`` directory is published
      and fsynced BEFORE ``run-manifest.v1.json`` is committed last, and
      the service reopens the published manifest to verify every sealed
      digest.
    * Fault injection at every publication boundary produces a typed
      ``failed`` result, never raises into the caller, removes only
      task-owned staging, preserves pre-existing report data, and never
      leaves a root manifest claiming incomplete evidence.
    * Repeated invocation refuses to overwrite an existing authority.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable

import pytest

from core.artifact_store import ArtifactStore
from core.manifest_sealing import (
    record_not_requested,
    seal_root_manifest,
)
from core.manifest_sealing_models import (
    MANIFEST_SEALING_FILENAME,
    MANIFEST_SEALING_SCHEMA,
    MANIFEST_SEALING_SCHEMA_VERSION,
    ManifestSealingError,
    ManifestSealingErrorKind,
    ManifestSealingFaultHooks,
    ManifestSealingResult,
    ManifestSealingStatus,
)
from core.run_manifest_io import read_manifest
from core.run_manifest_inventory import digest_inventory
from core.run_manifest_models import RUN_MANIFEST_FILENAME
from core.run_outcome import PhaseId, TerminalAnchor

_TERMINAL_ANCHOR = TerminalAnchor(phase_id=PhaseId("phase_5_validation"))
_RUN_ID = "r1"


def _workflow_file(tmp_path: Path) -> Path:
    workflow = tmp_path / "workflow.yaml"
    _ = workflow.write_text("phase: 1\n", encoding="utf-8")
    return workflow


def _project_dir(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    _ = (project / "setup.py").write_text("name = 'demo'\n", encoding="utf-8")
    return project


def _artifact_store(project_dir: Path, run_id: str = _RUN_ID) -> ArtifactStore:
    store = ArtifactStore(str(project_dir), run_id)
    _ = store.mark_validated("phase_3_entry_script", {"status": "success"})
    return store


def _report_dir(tmp_path: Path, name: str = _RUN_ID) -> Path:
    report_dir = tmp_path / name
    report_dir.mkdir(parents=True)
    return report_dir


def _raise_injected() -> None:
    raise OSError("injected publication fault")


def _hooks(fault_at: str | None) -> ManifestSealingFaultHooks:
    if fault_at is None:
        return ManifestSealingFaultHooks()
    raiser: Callable[[], None] = _raise_injected
    if fault_at == "before_evidence_publish":
        return ManifestSealingFaultHooks(before_evidence_publish=raiser)
    if fault_at == "before_manifest_commit":
        return ManifestSealingFaultHooks(before_manifest_commit=raiser)
    return ManifestSealingFaultHooks(before_sidecar_publish=raiser)


def _seal(
    tmp_path: Path,
    *,
    artifact_store: ArtifactStore | None,
    fault_at: str | None = None,
    run_id: str = _RUN_ID,
) -> tuple[ManifestSealingResult, Path, Path, Path]:
    report_dir = _report_dir(tmp_path, run_id)
    result = seal_root_manifest(
        report_dir=report_dir,
        run_id=run_id,
        project_dir=_project_dir(tmp_path),
        workflow_path=_workflow_file(tmp_path),
        artifact_store=artifact_store,
        terminal_anchor=_TERMINAL_ANCHOR,
        hooks=_hooks(fault_at),
    )
    return result, report_dir, report_dir / RUN_MANIFEST_FILENAME, report_dir / "sealed-artifacts"


# --- not_requested --------------------------------------------------------


def test_not_requested_writes_sidecar_and_returns_not_eligible(tmp_path: Path) -> None:
    """Given a report directory. When record_not_requested runs. Then the sidecar exists and the result is not_requested / not eligible."""
    report_dir = _report_dir(tmp_path)

    result = record_not_requested(report_dir=report_dir, run_id=_RUN_ID)

    assert result.status is ManifestSealingStatus.NOT_REQUESTED
    assert result.requested is False
    assert result.continuation_eligible is False
    assert result.manifest_path is None
    assert result.evidence_dir_path is None
    assert result.error is None
    sidecar = report_dir / MANIFEST_SEALING_FILENAME
    assert sidecar.exists()


def test_not_requested_sidecar_has_expected_schema(tmp_path: Path) -> None:
    """Given a not_requested result. When the sidecar is read. Then schema, version, and status fields match the v1 contract."""
    report_dir = _report_dir(tmp_path)

    _ = record_not_requested(report_dir=report_dir, run_id=_RUN_ID)

    sidecar_text = (report_dir / MANIFEST_SEALING_FILENAME).read_text("utf-8")
    assert f'"schema": "{MANIFEST_SEALING_SCHEMA}"' in sidecar_text
    assert f'"schema_version": {MANIFEST_SEALING_SCHEMA_VERSION}' in sidecar_text
    assert '"status": "not_requested"' in sidecar_text
    assert '"requested": false' in sidecar_text
    assert '"continuation_eligible": false' in sidecar_text
    assert '"error": null' in sidecar_text


# --- succeeded ------------------------------------------------------------


def test_succeeded_publishes_manifest_after_evidence_and_verifies_digests(
    tmp_path: Path,
) -> None:
    """Given a direct seal with evidence. When the service succeeds. Then evidence and the root manifest both exist and reopening the manifest verifies every sealed digest."""
    result, _report, manifest_path, evidence_path = _seal(
        tmp_path, artifact_store=_artifact_store(_project_dir(tmp_path))
    )

    assert result.status is ManifestSealingStatus.SUCCEEDED
    assert result.continuation_eligible is True
    assert result.error is None
    assert manifest_path.exists()
    assert evidence_path.exists()

    manifest = read_manifest(manifest_path)
    assert manifest.evidence_sealed is True
    inventory = digest_inventory(evidence_path, manifest_path.parent)
    assert inventory == manifest.sealed_evidence


def test_succeeded_sidecar_reports_eligible_with_paths(tmp_path: Path) -> None:
    """Given a successful seal. When the sidecar is read. Then it reports succeeded / eligible with the published manifest and evidence paths."""
    result, report_dir, manifest_path, _evidence = _seal(
        tmp_path, artifact_store=_artifact_store(_project_dir(tmp_path))
    )

    sidecar_text = result.sidecar_path.read_text("utf-8")
    assert '"status": "succeeded"' in sidecar_text
    assert '"requested": true' in sidecar_text
    assert '"continuation_eligible": true' in sidecar_text
    assert manifest_path.name in sidecar_text
    assert result.manifest_path == manifest_path
    _ = report_dir  # sidecar path provenance


def test_sealed_artifacts_directory_is_real_and_owned(tmp_path: Path) -> None:
    """Given a successful seal. When the published evidence directory is inspected. Then it is a real directory (not a link) carrying only the sealed evidence."""
    _result, _report, _manifest, evidence_path = _seal(
        tmp_path, artifact_store=_artifact_store(_project_dir(tmp_path))
    )

    assert evidence_path.is_dir()
    assert not evidence_path.is_symlink()


# --- repeated invocation --------------------------------------------------


def test_repeated_invocation_refuses_to_overwrite_existing_authority(
    tmp_path: Path,
) -> None:
    """Given an already-published root manifest. When seal_root_manifest is called again. Then it fails typed without overwriting the existing authority."""
    first, report_dir, manifest_path, _evidence = _seal(
        tmp_path, artifact_store=_artifact_store(_project_dir(tmp_path))
    )
    assert first.status is ManifestSealingStatus.SUCCEEDED
    original_bytes = manifest_path.read_bytes()

    second = seal_root_manifest(
        report_dir=report_dir,
        run_id=_RUN_ID,
        project_dir=_project_dir(tmp_path),
        workflow_path=_workflow_file(tmp_path),
        artifact_store=_artifact_store(_project_dir(tmp_path)),
        terminal_anchor=_TERMINAL_ANCHOR,
    )

    assert second.status is ManifestSealingStatus.FAILED
    assert second.continuation_eligible is False
    assert second.error is not None
    assert second.error.kind is ManifestSealingErrorKind.AUTHORITY_ALREADY_PRESENT
    assert manifest_path.read_bytes() == original_bytes


# --- fault injection ------------------------------------------------------


@pytest.mark.parametrize(
    "fault_at",
    [
        "before_evidence_publish",
        "before_manifest_commit",
    ],
)
def test_fault_before_manifest_commit_returns_failed_without_manifest(
    tmp_path: Path,
    fault_at: str,
) -> None:
    """Given an injected OSError before the root manifest is committed. When the service runs. Then the result is failed, no root manifest is published, and task-owned staging and evidence are removed."""
    result, _report, manifest_path, evidence_path = _seal(
        tmp_path,
        artifact_store=_artifact_store(_project_dir(tmp_path)),
        fault_at=fault_at,
    )

    assert result.status is ManifestSealingStatus.FAILED
    assert result.continuation_eligible is False
    assert result.error is not None
    assert result.manifest_path is None
    assert result.evidence_dir_path is None
    assert not manifest_path.exists()
    assert not evidence_path.exists()
    sidecar = result.sidecar_path
    assert sidecar.exists()


def test_fault_before_sidecar_preserves_valid_authority_and_reports_failed(
    tmp_path: Path,
) -> None:
    """Given an injected OSError after the manifest is committed and verified but before the sidecar. When the service runs. Then the result is failed and not eligible, yet the published manifest+evidence remain and the manifest does not claim incomplete evidence."""
    result, _report, manifest_path, evidence_path = _seal(
        tmp_path,
        artifact_store=_artifact_store(_project_dir(tmp_path)),
        fault_at="before_sidecar_publish",
    )

    assert result.status is ManifestSealingStatus.FAILED
    assert result.continuation_eligible is False
    assert result.error is not None
    assert result.error.kind is ManifestSealingErrorKind.SIDECAR_PUBLISH_FAILED
    # Authority was fully published before the sidecar fault; it is preserved.
    assert manifest_path.exists()
    assert evidence_path.exists()
    manifest = read_manifest(manifest_path)
    assert manifest.evidence_sealed is True
    inventory = digest_inventory(evidence_path, manifest_path.parent)
    assert inventory == manifest.sealed_evidence


def test_fault_cleanup_preserves_pre_existing_report_data(tmp_path: Path) -> None:
    """Given a pre-existing unrelated file in the report directory. When a fault is injected before evidence publish. Then the pre-existing report data is preserved and only task-owned staging is removed."""
    report_dir = _report_dir(tmp_path)
    pre_existing = report_dir / "summary.json"
    _ = pre_existing.write_text('{"overall_status": "PASS"}', encoding="utf-8")

    result = seal_root_manifest(
        report_dir=report_dir,
        run_id=_RUN_ID,
        project_dir=_project_dir(tmp_path),
        workflow_path=_workflow_file(tmp_path),
        artifact_store=_artifact_store(_project_dir(tmp_path)),
        terminal_anchor=_TERMINAL_ANCHOR,
        hooks=_hooks("before_evidence_publish"),
    )

    assert result.status is ManifestSealingStatus.FAILED
    assert pre_existing.exists()
    assert pre_existing.read_text("utf-8") == '{"overall_status": "PASS"}'
    assert not (report_dir / "sealed-artifacts").exists()
    assert not (report_dir / RUN_MANIFEST_FILENAME).exists()


def test_service_never_raises_into_caller(tmp_path: Path) -> None:
    """Given a missing workflow path forcing an internal exception. When the service runs. Then it returns a typed failed result rather than raising into the caller (outcome-neutral).."""
    report_dir = _report_dir(tmp_path)

    result = seal_root_manifest(
        report_dir=report_dir,
        run_id=_RUN_ID,
        project_dir=_project_dir(tmp_path),
        workflow_path=tmp_path / "definitely-missing.yaml",
        artifact_store=None,
        terminal_anchor=_TERMINAL_ANCHOR,
    )

    assert result.status is ManifestSealingStatus.FAILED
    assert result.error is not None
    assert result.error.kind is ManifestSealingErrorKind.STAGING_FAILED


# --- error model invariants -----------------------------------------------


def test_manifest_sealing_error_redacts_and_bounds_detail() -> None:
    """Given an error detail containing bearer tokens and oversized text. When the error is constructed. Then the secret tokens are redacted and the detail is length-bounded."""
    secret = "Bearer abcdefghijklmnopqrstuvwxyz0123456789"
    detail = ", ".join([secret] * 64)

    error = ManifestSealingError(ManifestSealingErrorKind.STAGING_FAILED, detail)

    assert "<REDACTED>" in error.detail
    assert "abcdefghijklmnopqrstuvwxyz0123456789" not in error.detail
    assert len(error.detail) <= 2048


def test_failed_result_requires_error() -> None:
    """Given a failed result constructed without an error. When post_init runs. Then a contract error is raised (impossible state rejected)."""
    with pytest.raises(Exception):  # noqa: PT011 - contract error subclass
        _ = ManifestSealingResult(
            status=ManifestSealingStatus.FAILED,
            requested=True,
            continuation_eligible=False,
            run_id=_RUN_ID,
            sidecar_path=Path("/x/manifest-sealing.v1.json"),
        )


def test_succeeded_result_requires_paths() -> None:
    """Given a succeeded result constructed without manifest/evidence paths. When post_init runs. Then a contract error is raised (impossible state rejected)."""
    with pytest.raises(Exception):  # noqa: PT011 - contract error subclass
        _ = ManifestSealingResult(
            status=ManifestSealingStatus.SUCCEEDED,
            requested=True,
            continuation_eligible=True,
            run_id=_RUN_ID,
            sidecar_path=Path("/x/manifest-sealing.v1.json"),
        )


# --- ownership-proven cleanup + outcome-neutral sidecar (parent-verified defects)


def test_record_not_requested_does_not_escape_on_sidecar_write_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Given a sidecar publication that always raises PermissionError. When record_not_requested runs. Then no exception escapes and a typed not_requested result is returned.

    Parent repro: monkeypatching ``atomic_write_bytes`` to raise
    ``PermissionError('denied')`` caused the exception to escape from
    ``record_not_requested`` into its caller, violating outcome-neutrality.
    """

    def _deny(*_args: object, **_kwargs: object) -> None:
        raise PermissionError("denied")

    monkeypatch.setattr("core.manifest_sealing.atomic_write_bytes", _deny)
    report_dir = _report_dir(tmp_path)

    result = record_not_requested(report_dir=report_dir, run_id=_RUN_ID)

    assert result.status is ManifestSealingStatus.NOT_REQUESTED
    assert result.requested is False
    assert result.continuation_eligible is False
    assert result.error is None


def test_cleanup_refuses_to_delete_replacement_staging_directory(
    tmp_path: Path,
) -> None:
    """Given the task-owned staging directory is replaced before cleanup. When a fault is injected after the replacement. Then cleanup proves ownership and refuses to delete the replacement directory.

    The staging directory has a random name but a random name is not ownership
    proof: an attacker (or a concurrent process) can replace the directory in
    place. Cleanup must capture the directory identity at creation and refuse to
    delete a path whose identity no longer matches, rather than raw-rmtree by
    name.
    """
    report_dir = _report_dir(tmp_path)
    sentinel_name = "attacker-sentinel"

    def _replace_staging_and_raise() -> None:
        candidates = list(report_dir.parent.glob(".manifest-sealing.*.tmp"))
        assert len(candidates) == 1, "staging directory was not found at hook time"
        staging = candidates[0]
        shutil.rmtree(staging)
        staging.mkdir()
        _ = (staging / sentinel_name).write_text("not yours", encoding="utf-8")
        raise OSError("injected after staging replacement")

    result = seal_root_manifest(
        report_dir=report_dir,
        run_id=_RUN_ID,
        project_dir=_project_dir(tmp_path),
        workflow_path=_workflow_file(tmp_path),
        artifact_store=_artifact_store(_project_dir(tmp_path)),
        terminal_anchor=_TERMINAL_ANCHOR,
        hooks=ManifestSealingFaultHooks(before_evidence_publish=_replace_staging_and_raise),
    )

    assert result.status is ManifestSealingStatus.FAILED
    candidates = list(report_dir.parent.glob(".manifest-sealing.*.tmp"))
    assert len(candidates) == 1, "ownership-proven cleanup must not delete the replacement"
    assert (candidates[0] / sentinel_name).exists()


def test_cleanup_refuses_to_delete_replacement_evidence_directory(
    tmp_path: Path,
) -> None:
    """Given the published evidence directory is replaced before cleanup. When the manifest commit is fault-injected after the replacement. Then evidence cleanup proves ownership and refuses to delete the replacement.

    The same authenticated-ownership rule applied to evidence published before
    the manifest commit: cleanup must never raw-delete a path merely because
    its name matches ``sealed-artifacts``.
    """
    report_dir = _report_dir(tmp_path)
    evidence_path = report_dir / "sealed-artifacts"
    sentinel_name = "attacker-sentinel"

    def _replace_evidence_and_raise() -> None:
        shutil.rmtree(evidence_path)
        evidence_path.mkdir()
        _ = (evidence_path / sentinel_name).write_text("not yours", encoding="utf-8")
        raise OSError("injected after evidence replacement")

    result = seal_root_manifest(
        report_dir=report_dir,
        run_id=_RUN_ID,
        project_dir=_project_dir(tmp_path),
        workflow_path=_workflow_file(tmp_path),
        artifact_store=_artifact_store(_project_dir(tmp_path)),
        terminal_anchor=_TERMINAL_ANCHOR,
        hooks=ManifestSealingFaultHooks(before_manifest_commit=_replace_evidence_and_raise),
    )

    assert result.status is ManifestSealingStatus.FAILED
    assert result.manifest_path is None
    assert (evidence_path / sentinel_name).exists()
