from __future__ import annotations

import os
from pathlib import Path
from typing import NoReturn

import pytest

from core import run_manifest_allocation, run_manifest_evidence_seal
from core.artifact_store import ArtifactStore
from core.atomic_directory import rename_directory_no_replace
from core.phase5_attempt_receipt import (
    CustomOpGateEvidence,
    CustomOpGateStatus,
    artifact_file_receipt,
    finalize_attempt_receipt,
)
from core.phase5_attempt_models import Phase5AttemptReceipt
from core.run_manifest import RunManifestError, RunManifestStore
from core.run_manifest_models import RunStorageContext
from core.run_outcome import ReviewOutcome
from harness.session.trace_export_models import TraceExportError, TraceExportRequest
from harness.session import trace_export_transaction
from harness.session.trace_export_transaction import TraceExportTransaction
from tests.run_manifest_test_support import root_manifest, storage_context
from tests.phase5_receipt_test_support import authority, review, save_attempt


class _PrimaryBodyError(ValueError):
    pass


def _accepted_evidence(
    tmp_path: Path,
) -> tuple[RunStorageContext, ArtifactStore, Path, Phase5AttemptReceipt]:
    context = storage_context(tmp_path)
    working = ArtifactStore(str(context.workspace_root), "parent-run-001")
    receipt_path = save_attempt(working, context.workspace_root, exit_code=0)
    finalized = finalize_attempt_receipt(
        receipt_path,
        custom_op_gate=CustomOpGateEvidence(status=CustomOpGateStatus.INACTIVE),
        review=review(ReviewOutcome.DISABLED),
    )
    working.record_finalized_phase5_authority(str(receipt_path), finalized)
    accepted = working.accept_phase5_attempt_receipt(
        receipt_path,
        authority(working, receipt_path),
    )
    return context, working, receipt_path, accepted


def test_directory_publication_does_not_replace_empty_preplant(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    destination = tmp_path / "destination"
    staging.mkdir()
    destination.mkdir()

    with pytest.raises(FileExistsError):
        rename_directory_no_replace(staging, destination)
    assert staging.is_dir()
    assert destination.is_dir()


def test_preexisting_seal_must_match_current_working_evidence(tmp_path: Path) -> None:
    context = storage_context(tmp_path)
    working = ArtifactStore(str(context.workspace_root), "parent-run-001")
    _ = working.mark_validated("phase_3_entry_script", {"status": "success"})
    writer = RunManifestStore.create(context, root_manifest(context))
    sealed = context.authoritative_root / "parent-run-001" / "sealed-artifacts"
    sealed.mkdir()
    _ = (sealed / "attacker.txt").write_text("untrusted", encoding="utf-8")

    with pytest.raises(RunManifestError):
        _ = writer.seal_working_evidence(working)


def test_matching_hardlinked_seal_is_rejected(tmp_path: Path) -> None:
    context = storage_context(tmp_path)
    working = ArtifactStore(str(context.workspace_root), "parent-run-001")
    _ = working.mark_validated("phase_3_entry_script", {"status": "success"})
    writer = RunManifestStore.create(context, root_manifest(context))
    source = Path(working.artifact_dir)
    sealed = context.authoritative_root / "parent-run-001" / "sealed-artifacts"
    sealed.mkdir()
    for source_path in source.rglob("*"):
        destination = sealed / source_path.relative_to(source)
        if source_path.is_dir():
            destination.mkdir()
        else:
            os.link(source_path, destination)

    with pytest.raises(RunManifestError):
        _ = writer.seal_working_evidence(working)


def test_coherently_forged_accepted_receipt_is_rejected_at_seal(
    tmp_path: Path,
) -> None:
    context, working, receipt_path, accepted = _accepted_evidence(tmp_path)
    stdout_path = Path(accepted.artifacts.stdout.path)
    _ = stdout_path.write_text("forged validation output", encoding="utf-8")
    forged = accepted.model_copy(
        update={
            "artifacts": accepted.artifacts.model_copy(
                update={"stdout": artifact_file_receipt(stdout_path)}
            )
        }
    )
    _ = receipt_path.write_text(forged.model_dump_json(), encoding="utf-8")
    writer = RunManifestStore.create(context, root_manifest(context))

    with pytest.raises(RunManifestError):
        _ = writer.seal_working_evidence(working)


def test_downgraded_accepted_receipt_is_rejected_at_seal(tmp_path: Path) -> None:
    context, working, receipt_path, accepted = _accepted_evidence(tmp_path)
    downgraded = accepted.model_copy(update={"accepted": False})
    _ = receipt_path.write_text(downgraded.model_dump_json(), encoding="utf-8")
    writer = RunManifestStore.create(context, root_manifest(context))

    with pytest.raises(RunManifestError):
        _ = writer.seal_working_evidence(working)


def test_deleted_accepted_receipt_is_rejected_at_seal(tmp_path: Path) -> None:
    context, working, receipt_path, _accepted = _accepted_evidence(tmp_path)
    receipt_path.unlink()
    writer = RunManifestStore.create(context, root_manifest(context))

    with pytest.raises(RunManifestError):
        _ = writer.seal_working_evidence(working)


def test_renamed_accepted_receipt_is_rejected_at_seal(tmp_path: Path) -> None:
    context, working, receipt_path, _accepted = _accepted_evidence(tmp_path)
    _ = receipt_path.rename(receipt_path.with_suffix(".hidden"))
    writer = RunManifestStore.create(context, root_manifest(context))

    with pytest.raises(RunManifestError):
        _ = writer.seal_working_evidence(working)


def test_nested_extra_accepted_receipt_is_rejected_at_seal(tmp_path: Path) -> None:
    context, working, _receipt_path, accepted = _accepted_evidence(tmp_path)
    nested = Path(working.artifact_dir) / "nested"
    nested.mkdir()
    _ = (nested / "extra.receipt.json").write_text(
        accepted.model_dump_json(),
        encoding="utf-8",
    )
    writer = RunManifestStore.create(context, root_manifest(context))

    with pytest.raises(RunManifestError):
        _ = writer.seal_working_evidence(working)


def test_seal_parent_sync_failure_rolls_back_published_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = storage_context(tmp_path)
    working = ArtifactStore(str(context.workspace_root), "parent-run-001")
    _ = working.mark_validated("phase_3_entry_script", {"status": "success"})
    writer = RunManifestStore.create(context, root_manifest(context))
    sealed = context.authoritative_root / "parent-run-001" / "sealed-artifacts"

    def fail_sync(_path: Path) -> NoReturn:
        raise OSError("forced seal parent sync failure")

    monkeypatch.setattr(run_manifest_evidence_seal, "fsync_parent", fail_sync)

    with pytest.raises(RunManifestError, match="forced seal parent sync failure"):
        _ = writer.seal_working_evidence(working)
    assert not sealed.exists()


def test_trace_owner_write_failure_removes_publish_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "trace"
    request = TraceExportRequest(
        destination=destination,
        seeds=(),
        overflow_roots=(),
        captured_at="2026-07-30T00:00:00+00:00",
    )
    original_write = trace_export_transaction.private_file_create

    def fail_owner_write(path: Path, content: bytes) -> None:
        if path.name == "owner" and path.parent.name.endswith(".publish.lock"):
            raise OSError("forced owner write failure")
        original_write(path, content)

    monkeypatch.setattr(
        trace_export_transaction, "private_file_create", fail_owner_write
    )

    with pytest.raises(TraceExportError, match="forced owner write failure"):
        with TraceExportTransaction(request):
            pass
    assert not (tmp_path / ".trace.publish.lock").exists()


def test_trace_commit_rejects_replaced_staging_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = TraceExportRequest(
        destination=tmp_path / "trace",
        seeds=(),
        overflow_roots=(),
        captured_at="2026-07-30T00:00:00+00:00",
    )
    original_rename = trace_export_transaction.atomic_directory_rename

    def publish_replacement(staging: Path, destination: Path) -> None:
        _ = staging.rename(staging.with_name(f"{staging.name}.original"))
        staging.mkdir()
        (staging / "sessions").mkdir()
        (staging / "overflows").mkdir()
        _ = (staging / "forged.txt").write_text("forged", encoding="utf-8")
        original_rename(staging, destination)

    monkeypatch.setattr(
        trace_export_transaction,
        "atomic_directory_rename",
        publish_replacement,
    )

    with pytest.raises(TraceExportError, match="published trace differs"):
        with TraceExportTransaction(request) as transaction:
            transaction.commit()

    assert not request.destination.exists()


def test_trace_abort_preserves_primary_body_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = TraceExportRequest(
        destination=tmp_path / "trace",
        seeds=(),
        overflow_roots=(),
        captured_at="2026-07-30T00:00:00+00:00",
    )

    def fail_cleanup(_transaction: TraceExportTransaction) -> str:
        return "forced cleanup failure"

    monkeypatch.setattr(TraceExportTransaction, "_remove_staging", fail_cleanup)

    with pytest.raises(_PrimaryBodyError, match="primary body failure"):
        with TraceExportTransaction(request):
            raise _PrimaryBodyError("primary body failure")


def test_run_directory_sync_failure_rolls_back_namespace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = storage_context(tmp_path)
    manifest = root_manifest(context)
    run_dir = context.authoritative_root / str(manifest.run_id)

    def fail_run_sync(path: Path) -> None:
        if path == run_dir:
            raise OSError("forced run directory sync failure")

    monkeypatch.setattr(run_manifest_allocation, "fsync_parent", fail_run_sync)

    with pytest.raises(RunManifestError, match="forced run directory sync failure"):
        _ = RunManifestStore.create(context, manifest)
    assert not run_dir.exists()
