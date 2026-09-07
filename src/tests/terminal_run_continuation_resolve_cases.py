from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from unittest.mock import Mock

import pytest

from core.continuation import (
    ContinuationError,
    ContinuationErrorKind,
    ContinuationRequest,
    claim_terminal_parent,
    resolve_terminal_parent,
)
from core.run_manifest import RunId
from tests.terminal_run_continuation_test_support import (
    CHILD_RUN_ID,
    claim_rejection,
    create_parent_run,
    read_json_payload,
    read_summary,
    tree_bytes,
    write_summary,
)


def test_resolve_eligible_terminal_parent_uses_explicit_summary(tmp_path: Path) -> None:
    # Given an explicit PASS summary with sealed authoritative manifests.
    parent = create_parent_run(tmp_path)
    decoy_dir = parent.reports_root / "newer-invalid-run"
    decoy_dir.mkdir()
    decoy = decoy_dir / "summary.json"
    _ = decoy.write_text('{"overall_status":"RUNNING"}', encoding="utf-8")
    os.utime(decoy, (time.time() + 60, time.time() + 60))

    # When the exact supplied summary is resolved.
    resolved = resolve_terminal_parent(parent.summary_path)

    # Then all returned values are typed, canonical, and authority-backed.
    assert resolved.run_id == RunId("parent-run-001")
    assert resolved.output_project == parent.project_dir.resolve(strict=True)
    assert resolved.workflow_path == parent.workflow_path.resolve(strict=True)
    assert resolved.workflow_digest == parent.workflow_digest
    assert resolved.terminal_anchor.phase_id == "phase_5_validation"
    assert resolved.run_manifest.evidence_sealed is True
    assert resolved.resource_manifest.sealed is True


@pytest.mark.parametrize("status", ["CANCELLED", "ERROR", "PASS ", "pass"])
def test_eligibility_rejects_non_exact_terminal_status(
    tmp_path: Path, status: str
) -> None:
    # Given a structurally complete parent with a non-eligible summary status.
    parent = create_parent_run(tmp_path)
    payload = read_summary(parent)
    payload["overall_status"] = status
    write_summary(parent, payload)

    # When eligibility is resolved.
    kind = claim_rejection(parent)

    # Then only an exact PASS or FAIL is accepted.
    assert kind is ContinuationErrorKind.STATUS_INELIGIBLE


def test_eligibility_rejects_missing_manifest_anchor(tmp_path: Path) -> None:
    # Given an otherwise authoritative parent whose run manifest omits its anchor.
    parent = create_parent_run(tmp_path)
    payload = read_json_payload(parent.run_manifest_path)
    del payload["terminal_anchor"]
    _ = parent.run_manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    # When eligibility is resolved.
    kind = claim_rejection(parent)

    # Then malformed authority is rejected without inference.
    assert kind is ContinuationErrorKind.AUTHORITY_INVALID


def test_eligibility_rejects_anchor_absent_from_summary(tmp_path: Path) -> None:
    # Given a valid manifest anchor that is not an executed summary phase.
    parent = create_parent_run(tmp_path, anchor_phase="phase_missing")

    # When eligibility is resolved.
    kind = claim_rejection(parent)

    # Then no fallback anchor is inferred.
    assert kind is ContinuationErrorKind.ANCHOR_INVALID


def test_eligibility_rejects_workflow_digest_mismatch(tmp_path: Path) -> None:
    # Given a pinned workflow changed after both manifests were sealed.
    parent = create_parent_run(tmp_path)
    _ = parent.workflow_path.write_text("name: changed\n", encoding="utf-8")

    # When eligibility is resolved.
    kind = claim_rejection(parent)

    # Then actual workflow bytes, not the claimed digest, control eligibility.
    assert kind is ContinuationErrorKind.WORKFLOW_MISMATCH


def test_eligibility_rejects_workspace_mismatch(tmp_path: Path) -> None:
    # Given a summary claiming another extant output project.
    parent = create_parent_run(tmp_path)
    other_project = tmp_path / "other-project"
    other_project.mkdir()
    payload = read_summary(parent)
    payload["temp_dir"] = str(other_project)
    write_summary(parent, payload)

    # When eligibility is resolved.
    kind = claim_rejection(parent)

    # Then the authoritative workspace binding rejects the claim.
    assert kind is ContinuationErrorKind.AUTHORITY_INVALID


def test_eligibility_rejects_mismatched_run_identity(tmp_path: Path) -> None:
    # Given a summary whose run ID differs from its explicit report namespace.
    parent = create_parent_run(tmp_path)
    payload = read_summary(parent)
    payload["run_id"] = "another-run"
    write_summary(parent, payload)

    # When eligibility is resolved.
    kind = claim_rejection(parent)

    # Then namespace identity is exact.
    assert kind is ContinuationErrorKind.RUN_ID_MISMATCH


@pytest.mark.parametrize(
    ("mode", "expected_kind"),
    [
        ("empty", ContinuationErrorKind.INCOMPLETE_PARENT),
        ("unknown", ContinuationErrorKind.INCOMPLETE_PARENT),
        ("missing_failed_phase", ContinuationErrorKind.ANCHOR_INVALID),
    ],
)
def test_eligibility_rejects_incomplete_parent(
    tmp_path: Path,
    mode: str,
    expected_kind: ContinuationErrorKind,
) -> None:
    # Given a terminal label without complete authoritative phase evidence.
    parent = create_parent_run(tmp_path, status="FAIL", phase_status="failed")
    payload = read_summary(parent)
    phases = payload["phases"]
    assert isinstance(phases, list)
    if mode == "empty":
        phases.clear()
    elif mode == "unknown":
        phase = phases[0]
        assert isinstance(phase, dict)
        phase["status"] = "unknown"
    else:
        phase = phases[0]
        assert isinstance(phase, dict)
        phase["status"] = "passed"
    write_summary(parent, payload)

    # When eligibility is resolved.
    kind = claim_rejection(parent)

    # Then traceback-only, unfinished, and anchorless failures are refused.
    assert kind is expected_kind


def test_eligibility_rejects_relative_and_traversal_summary_paths(
    tmp_path: Path,
) -> None:
    # Given relative and lexically traversing names for one valid summary.
    parent = create_parent_run(tmp_path)
    relative = Path(parent.summary_path.name)
    traversing = parent.report_dir / ".." / parent.report_dir.name / "summary.json"

    # When either ambiguous spelling is resolved.
    errors: list[ContinuationErrorKind] = []
    for candidate in (relative, traversing):
        errors.append(claim_rejection(parent, candidate))

    # Then only one absolute, traversal-free spelling is accepted.
    assert errors == [
        ContinuationErrorKind.UNSAFE_SUMMARY_PATH,
        ContinuationErrorKind.UNSAFE_SUMMARY_PATH,
    ]


def test_eligibility_rejects_junction_or_symlink_summary_ancestor(
    tmp_path: Path,
) -> None:
    # Given an alternate link/junction path to a valid authoritative report.
    parent = create_parent_run(tmp_path)
    alias = tmp_path / "report-alias"
    if os.name == "nt":
        _ = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(alias), str(parent.report_dir)],
            check=True,
            capture_output=True,
        )
    else:
        alias.symlink_to(parent.report_dir, target_is_directory=True)

    # When the linked spelling is supplied.
    kind = claim_rejection(parent, alias / "summary.json")

    # Then canonical equivalence cannot bypass physical-path identity.
    assert kind is ContinuationErrorKind.UNSAFE_SUMMARY_PATH


def test_eligibility_rejection_is_read_only_and_calls_no_factories(
    tmp_path: Path,
) -> None:
    # Given an invalid parent and factories placed strictly inside claim ownership.
    parent = create_parent_run(tmp_path)
    payload = read_summary(parent)
    payload["overall_status"] = "RUNNING"
    write_summary(parent, payload)
    parent_before = tree_bytes(parent.report_dir)
    project_before = tree_bytes(parent.project_dir)
    session_factory = Mock()
    backend_factory = Mock()
    child_artifact_factory = Mock()

    # When continuation ownership is attempted.
    with pytest.raises(ContinuationError):
        with claim_terminal_parent(
            ContinuationRequest(
                summary_path=parent.summary_path, child_run_id=CHILD_RUN_ID
            )
        ):
            session_factory()
            backend_factory()
            child_artifact_factory()

    # Then rejection precedes every factory and leaves both trees byte-identical.
    session_factory.assert_not_called()
    backend_factory.assert_not_called()
    child_artifact_factory.assert_not_called()
    assert tree_bytes(parent.report_dir) == parent_before
    assert tree_bytes(parent.project_dir) == project_before
