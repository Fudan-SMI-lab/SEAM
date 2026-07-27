from __future__ import annotations

from pathlib import Path

import pytest

from core.continuation import (
    ContinuationRequest,
    claim_terminal_parent,
    resolve_terminal_parent,
)
from core.continuation_evidence import ChildEvidenceRequest
from core.run_manifest import CanonicalReference, RunId
from core.run_outcome import PhaseId, TerminalAnchor
from tests.terminal_run_continuation_test_support import (
    create_parent_run,
    tree_bytes,
)
from tests.continuation_evidence_security_cases import (
    test_artifact_store_exclusive_creation_rejects_unsafe_run_id as test_artifact_store_exclusive_creation_rejects_unsafe_run_id,
    test_final_verification_rejects_child_sealed_without_evidence_root as test_final_verification_rejects_child_sealed_without_evidence_root,
    test_final_verification_maps_child_manifest_io_failure as test_final_verification_maps_child_manifest_io_failure,
    test_final_verification_rejects_semantically_equal_parent_summary_bytes as test_final_verification_rejects_semantically_equal_parent_summary_bytes,
    test_prepare_rejects_unverified_inherited_canonical_digest as test_prepare_rejects_unverified_inherited_canonical_digest,
    test_project_baseline_rejects_directory_swapped_to_junction as test_project_baseline_rejects_directory_swapped_to_junction,
    test_project_baseline_records_link_without_following_target as test_project_baseline_records_link_without_following_target,
    test_seal_failure_is_a_typed_continuation_evidence_error as test_seal_failure_is_a_typed_continuation_evidence_error,
    test_sealed_root_detects_external_evidence_drift_without_prepared_state as test_sealed_root_detects_external_evidence_drift_without_prepared_state,
)

CHILD_RUN_ID = RunId("child-run-20260728-000001-a1b2c3")


def _request(parent: Path) -> ChildEvidenceRequest:
    manifest = resolve_terminal_parent(parent).run_manifest
    evidence = next(
        item
        for item in manifest.sealed_evidence
        if item.relative_path == "validated/phase_5_validation_canonical.json"
    )
    return ChildEvidenceRequest(
        continuation=ContinuationRequest(
            summary_path=parent,
            child_run_id=str(CHILD_RUN_ID),
        ),
        inherited_canonical=(
            CanonicalReference(
                phase_id=PhaseId("phase_5_validation"),
                artifact_name="phase_5_validation_canonical.json",
                digest=evidence.digest,
            ),
        ),
    )


def _assert_owner_released(reports_root: Path) -> None:
    lock_dir = reports_root / "locks"
    assert not lock_dir.exists() or not tuple(lock_dir.glob("*.lock"))


def test_prepare_archives_mutable_reports_and_records_shared_baseline(
    tmp_path: Path,
) -> None:
    # Given an authoritative sealed parent and mutable lineage-shared project.
    from core.continuation_evidence import (
        prepare_child_evidence,
        seal_child_evidence,
        verify_final_child_evidence,
    )

    parent = create_parent_run(tmp_path)
    source = parent.project_dir / "migration_reports"
    (source / "nested").mkdir(parents=True)
    _ = (source / "result.json").write_bytes(b'{"status":"parent"}\n')
    _ = (source / "nested" / "notes.txt").write_bytes(b"complete\n")
    _ = (parent.project_dir / "model.py").write_bytes(b"value = 1\n")
    parent_before = tree_bytes(parent.report_dir)

    # When child evidence is prepared, populated, sealed, and finally verified.
    with claim_terminal_parent(_request(parent.summary_path).continuation) as resolved:
        prepared = prepare_child_evidence(resolved, _request(parent.summary_path))
        assert not prepared.namespace.trace_dir.exists()
        prepared.namespace.trace_dir.mkdir()
        _ = (prepared.namespace.trace_dir / "trace-manifest.json").write_bytes(
            b'{"complete":true}\n'
        )
        _ = prepared.artifact_store.mark_validated("phase_5_validation", {"ok": True})
        child_anchor = TerminalAnchor(phase_id=PhaseId("phase_7_finalize"))
        sealed = seal_child_evidence(prepared, terminal_anchor=child_anchor)
        verified = verify_final_child_evidence(prepared)

    # Then external run-scoped evidence is complete without freezing the project.
    assert prepared.namespace.report_dir.name == str(CHILD_RUN_ID)
    assert str(CHILD_RUN_ID) in prepared.namespace.trace_dir.parts
    assert str(CHILD_RUN_ID) in prepared.namespace.artifact_dir.parts
    assert parent.project_dir not in prepared.namespace.report_dir.parents
    assert prepared.project_baseline.workspace_kind == "lineage_shared_mutable"
    assert prepared.project_baseline.complete is True
    assert prepared.project_baseline.links == ()
    assert prepared.migration_archive.complete is True
    assert {item.relative_path for item in prepared.migration_archive.files} == {
        "nested/notes.txt",
        "result.json",
    }
    assert (prepared.namespace.migration_archive_dir / "result.json").read_bytes() == (
        b'{"status":"parent"}\n'
    )
    assert sealed.evidence_sealed is True
    assert sealed.terminal_anchor == child_anchor
    assert (
        verified.child_manifest.inherited_canonical
        == prepared.request.inherited_canonical
    )
    assert verified.child_manifest.parent_evidence_digests == (
        resolved.run_manifest.sealed_evidence
    )
    assert tree_bytes(parent.report_dir) == parent_before
    _assert_owner_released(parent.reports_root)


def test_parent_digest_drift_aborts_before_project_mutation(tmp_path: Path) -> None:
    # Given ownership of a parent whose authoritative evidence drifts afterward.
    from core.continuation_evidence import (
        ContinuationEvidenceError,
        ContinuationEvidenceErrorKind,
        prepare_child_evidence,
    )

    parent = create_parent_run(tmp_path)
    request = _request(parent.summary_path)
    project_before = tree_bytes(parent.project_dir)

    # When preparation verifies the parent before allocating child project state.
    with pytest.raises(ContinuationEvidenceError) as raised:
        with claim_terminal_parent(request.continuation) as resolved:
            evidence = parent.report_dir / "sealed-artifacts" / "validated"
            target = next(evidence.iterdir())
            _ = target.write_bytes(b"drifted")
            _ = prepare_child_evidence(resolved, request)

    # Then no child namespace or project mutation occurs and ownership is released.
    assert raised.value.kind is ContinuationEvidenceErrorKind.PARENT_EVIDENCE_DRIFT
    assert not (parent.reports_root / str(CHILD_RUN_ID)).exists()
    assert tree_bytes(parent.project_dir) == project_before
    _assert_owner_released(parent.reports_root)


def test_duplicate_child_namespace_fails_without_second_project_mutation(
    tmp_path: Path,
) -> None:
    # Given one prepared child namespace with a globally unique run ID.
    from core.continuation_evidence import (
        ContinuationEvidenceError,
        ContinuationEvidenceErrorKind,
        prepare_child_evidence,
    )

    parent = create_parent_run(tmp_path)
    request = _request(parent.summary_path)
    with claim_terminal_parent(request.continuation) as resolved:
        _ = prepare_child_evidence(resolved, request)
    project_before = tree_bytes(parent.project_dir)

    # When the same child run ID is prepared again.
    with pytest.raises(ContinuationEvidenceError) as raised:
        with claim_terminal_parent(request.continuation) as resolved:
            _ = prepare_child_evidence(resolved, request)

    # Then exclusive creation rejects the collision without touching the project.
    assert raised.value.kind is ContinuationEvidenceErrorKind.NAMESPACE_EXISTS
    assert tree_bytes(parent.project_dir) == project_before
    _assert_owner_released(parent.reports_root)


@pytest.mark.parametrize(
    ("seam", "expected_kind"),
    [
        ("snapshot_project_baseline", "snapshot_failed"),
        ("archive_migration_reports", "archive_failed"),
    ],
)
def test_preparation_failure_aborts_before_child_working_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seam: str,
    expected_kind: str,
) -> None:
    # Given a real parent/project and one narrow failing filesystem seam.
    import core.continuation_evidence as evidence

    parent = create_parent_run(tmp_path)
    request = _request(parent.summary_path)
    project_before = tree_bytes(parent.project_dir)

    def fail_filesystem(*_args: Path) -> tuple[()]:
        raise OSError("injected filesystem failure")

    monkeypatch.setattr(evidence, seam, fail_filesystem)

    # When preparation reaches the failing snapshot or archive operation.
    with pytest.raises(evidence.ContinuationEvidenceError) as raised:
        with claim_terminal_parent(request.continuation) as resolved:
            _ = evidence.prepare_child_evidence(resolved, request)

    # Then failure is typed, project mutation has not begun, and lock is released.
    assert raised.value.kind.value == expected_kind
    assert tree_bytes(parent.project_dir) == project_before
    assert not (parent.project_dir / ".sm-artifacts" / str(CHILD_RUN_ID)).exists()
    _assert_owner_released(parent.reports_root)


def test_final_verification_rejects_parent_drift_after_child_seal(
    tmp_path: Path,
) -> None:
    # Given a prepared and sealed child inside the retained ownership context.
    from core.continuation_evidence import (
        ContinuationEvidenceError,
        ContinuationEvidenceErrorKind,
        prepare_child_evidence,
        seal_child_evidence,
        verify_final_child_evidence,
    )

    parent = create_parent_run(tmp_path)
    request = _request(parent.summary_path)

    # When parent evidence changes after child finalization but before release.
    with pytest.raises(ContinuationEvidenceError) as raised:
        with claim_terminal_parent(request.continuation) as resolved:
            prepared = prepare_child_evidence(resolved, request)
            _ = seal_child_evidence(prepared)
            target = next((parent.report_dir / "sealed-artifacts").rglob("*.json"))
            _ = target.write_bytes(b"post-finalization drift")
            _ = verify_final_child_evidence(prepared)

    # Then final verification fails closed and deterministic release still runs.
    assert raised.value.kind is ContinuationEvidenceErrorKind.PARENT_EVIDENCE_DRIFT
    _assert_owner_released(parent.reports_root)


def test_final_verification_rejects_mutated_precontinuation_record(
    tmp_path: Path,
) -> None:
    # Given a sealed child whose external baseline record was later modified.
    from core.continuation_evidence import (
        ContinuationEvidenceError,
        ContinuationEvidenceErrorKind,
        prepare_child_evidence,
        seal_child_evidence,
        verify_final_child_evidence,
    )

    parent = create_parent_run(tmp_path)
    request = _request(parent.summary_path)

    # When final verification rechecks child pre-continuation evidence.
    with pytest.raises(ContinuationEvidenceError) as raised:
        with claim_terminal_parent(request.continuation) as resolved:
            prepared = prepare_child_evidence(resolved, request)
            _ = seal_child_evidence(prepared)
            _ = prepared.namespace.baseline_path.write_bytes(b"{}")
            _ = verify_final_child_evidence(prepared)

    # Then the child is incomplete rather than silently accepting stale evidence.
    assert raised.value.kind is ContinuationEvidenceErrorKind.CHILD_EVIDENCE_DRIFT
    _assert_owner_released(parent.reports_root)
