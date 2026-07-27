from __future__ import annotations

import os
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from core.artifact_store import ArtifactStore
from core.continuation import (
    ContinuationRequest,
    claim_terminal_parent,
    resolve_terminal_parent,
)
from core.continuation_evidence import (
    ChildEvidenceRequest,
    ContinuationEvidenceError,
    ContinuationEvidenceErrorKind,
    prepare_child_evidence,
    seal_child_evidence,
    verify_final_child_evidence,
)
from core.continuation_evidence_authority import verify_external_evidence_root
from core.continuation_models import RunSummaryDocument
from core.run_manifest import (
    CanonicalReference,
    RunId,
    RunManifest,
    RunManifestStore,
    Sha256Digest,
)
from core.run_outcome import PhaseId
from tests.terminal_run_continuation_test_support import create_parent_run

SECURITY_CHILD_ID = RunId("child-run-security-001")


def _request(summary_path: Path) -> ChildEvidenceRequest:
    manifest = resolve_terminal_parent(summary_path).run_manifest
    evidence = next(
        item
        for item in manifest.sealed_evidence
        if item.relative_path == "validated/phase_5_validation_canonical.json"
    )
    return ChildEvidenceRequest(
        continuation=ContinuationRequest(
            summary_path=summary_path,
            child_run_id=str(SECURITY_CHILD_ID),
        ),
        inherited_canonical=(
            CanonicalReference(
                phase_id=PhaseId("phase_5_validation"),
                artifact_name="phase_5_validation_canonical.json",
                digest=evidence.digest,
            ),
        ),
    )


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
    link.symlink_to(target, target_is_directory=True)


def test_prepare_rejects_unverified_inherited_canonical_digest(tmp_path: Path) -> None:
    parent = create_parent_run(tmp_path)
    valid = _request(parent.summary_path)
    forged = valid.model_copy(
        update={
            "inherited_canonical": (
                valid.inherited_canonical[0].model_copy(
                    update={"digest": Sha256Digest("d" * 64)}
                ),
            )
        }
    )

    with pytest.raises(ContinuationEvidenceError) as raised:
        with claim_terminal_parent(forged.continuation) as resolved:
            _ = prepare_child_evidence(resolved, forged)

    assert (
        raised.value.kind is ContinuationEvidenceErrorKind.INHERITED_REFERENCE_INVALID
    )
    assert not (parent.reports_root / str(SECURITY_CHILD_ID)).exists()


def test_final_verification_rejects_semantically_equal_parent_summary_bytes(
    tmp_path: Path,
) -> None:
    parent = create_parent_run(tmp_path)
    request = _request(parent.summary_path)

    with pytest.raises(ContinuationEvidenceError) as raised:
        with claim_terminal_parent(request.continuation) as resolved:
            prepared = prepare_child_evidence(resolved, request)
            _ = seal_child_evidence(prepared)
            payload = RunSummaryDocument.model_validate_json(
                parent.summary_path.read_bytes()
            )
            _ = parent.summary_path.write_text(
                payload.model_dump_json(),
                encoding="utf-8",
            )
            _ = verify_final_child_evidence(prepared)

    assert raised.value.kind is ContinuationEvidenceErrorKind.PARENT_EVIDENCE_DRIFT


def test_sealed_root_detects_external_evidence_drift_without_prepared_state(
    tmp_path: Path,
) -> None:
    parent = create_parent_run(tmp_path)
    request = _request(parent.summary_path)
    with claim_terminal_parent(request.continuation) as resolved:
        prepared = prepare_child_evidence(resolved, request)
        sealed = seal_child_evidence(prepared)
        _ = verify_final_child_evidence(prepared)

    _ = prepared.namespace.baseline_path.write_bytes(b"{}")

    with pytest.raises(ContinuationEvidenceError) as raised:
        verify_external_evidence_root(prepared.namespace.report_dir, sealed)

    assert raised.value.kind is ContinuationEvidenceErrorKind.CHILD_EVIDENCE_DRIFT


def test_final_verification_rejects_child_sealed_without_evidence_root(
    tmp_path: Path,
) -> None:
    parent = create_parent_run(tmp_path)
    request = _request(parent.summary_path)
    with pytest.raises(ContinuationEvidenceError) as raised:
        with claim_terminal_parent(request.continuation) as resolved:
            prepared = prepare_child_evidence(resolved, request)
            _ = prepared.child_store.seal_working_evidence(prepared.artifact_store)
            _ = verify_final_child_evidence(prepared)

    assert raised.value.kind is ContinuationEvidenceErrorKind.CHILD_EVIDENCE_DRIFT


def test_final_verification_maps_child_manifest_io_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = create_parent_run(tmp_path)
    request = _request(parent.summary_path)
    with claim_terminal_parent(request.continuation) as resolved:
        prepared = prepare_child_evidence(resolved, request)
        _ = seal_child_evidence(prepared)
        original_read = RunManifestStore.read

        def fail_child_read(store: RunManifestStore) -> RunManifest:
            if store is prepared.child_store:
                raise OSError("injected child manifest read failure")
            return original_read(store)

        monkeypatch.setattr(RunManifestStore, "read", fail_child_read)

        with pytest.raises(ContinuationEvidenceError) as raised:
            _ = verify_final_child_evidence(prepared)

    assert raised.value.kind is ContinuationEvidenceErrorKind.CHILD_EVIDENCE_DRIFT


def test_project_baseline_records_link_without_following_target(tmp_path: Path) -> None:
    parent = create_parent_run(tmp_path)
    outside = tmp_path / "large-models"
    outside.mkdir()
    _ = (outside / "weights.bin").write_bytes(b"external-model")
    _directory_link(parent.project_dir / "model-link", outside)
    request = _request(parent.summary_path)

    with claim_terminal_parent(request.continuation) as resolved:
        prepared = prepare_child_evidence(resolved, request)

    assert tuple(item.relative_path for item in prepared.project_baseline.links) == (
        "model-link",
    )
    assert all(
        "weights.bin" not in item.relative_path
        for item in prepared.project_baseline.files
    )


def test_project_baseline_rejects_directory_swapped_to_junction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = create_parent_run(tmp_path)
    nested = parent.project_dir / "nested"
    nested.mkdir()
    _ = (nested / "local.txt").write_bytes(b"local")
    outside = tmp_path / "outside-swap"
    outside.mkdir()
    secret = outside / "secret.bin"
    _ = secret.write_bytes(b"outside")
    request = _request(parent.summary_path)
    original_iterdir = Path.iterdir
    swapped = False

    def swap_before_iteration(path: Path) -> Iterator[Path]:
        nonlocal swapped
        if path == nested and not swapped:
            swapped = True
            _ = path.rename(parent.project_dir / "nested-original")
            _directory_link(path, outside)
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", swap_before_iteration)

    with pytest.raises(ContinuationEvidenceError) as raised:
        with claim_terminal_parent(request.continuation) as resolved:
            _ = prepare_child_evidence(resolved, request)

    assert raised.value.kind is ContinuationEvidenceErrorKind.SNAPSHOT_FAILED
    assert secret.read_bytes() == b"outside"
    assert not (parent.project_dir / ".sm-artifacts" / str(SECURITY_CHILD_ID)).exists()


@pytest.mark.parametrize("run_id", ["../escaped", "C:/escaped", "child/run"])
def test_artifact_store_exclusive_creation_rejects_unsafe_run_id(
    tmp_path: Path,
    run_id: str,
) -> None:
    with pytest.raises(ValueError):
        _ = ArtifactStore.create_exclusive(str(tmp_path), run_id)

    assert not (tmp_path / "escaped").exists()


def test_seal_failure_is_a_typed_continuation_evidence_error(tmp_path: Path) -> None:
    parent = create_parent_run(tmp_path)
    request = _request(parent.summary_path)
    with claim_terminal_parent(request.continuation) as resolved:
        prepared = prepare_child_evidence(resolved, request)
        prepared.artifact_store.base_dir = str(tmp_path / "missing-project")

        with pytest.raises(ContinuationEvidenceError) as raised:
            _ = seal_child_evidence(prepared)

    assert raised.value.kind is ContinuationEvidenceErrorKind.CHILD_EVIDENCE_DRIFT
