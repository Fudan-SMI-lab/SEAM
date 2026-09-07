from __future__ import annotations

from pathlib import Path

import pytest

from tests.terminal_run_continuation_hydration_support import (
    PHASE_ORDER,
    canonical_value,
    create_hydration_parent,
    hydrate,
    phase5_reference,
)

from core.continuation import resolve_terminal_parent


@pytest.mark.parametrize(
    ("status", "anchor", "statuses", "canonical_ids", "expected_start", "inherited"),
    (
        (
            "PASS",
            "phase_6_report",
            ("passed",) * 5,
            PHASE_ORDER,
            "phase_5_validation",
            PHASE_ORDER[:3],
        ),
        (
            "FAIL",
            "phase_4_migrate",
            ("passed", "passed", "failed", "skipped", "skipped"),
            PHASE_ORDER[:2],
            "phase_4_migrate",
            PHASE_ORDER[:2],
        ),
        (
            "FAIL",
            "phase_5_validation",
            ("passed", "passed", "passed", "failed", "skipped"),
            PHASE_ORDER[:3],
            "phase_5_validation",
            PHASE_ORDER[:3],
        ),
        (
            "FAIL",
            "phase_6_report",
            ("passed", "passed", "passed", "passed", "failed"),
            PHASE_ORDER[:4],
            "phase_6_report",
            PHASE_ORDER[:4],
        ),
    ),
)
def test_anchor_matrix_hydrates_only_successful_canonical_predecessors(
    tmp_path: Path,
    status: str,
    anchor: str,
    statuses: tuple[str, ...],
    canonical_ids: tuple[str, ...],
    expected_start: str,
    inherited: tuple[str, ...],
) -> None:
    # Given
    parent = create_hydration_parent(
        tmp_path,
        status=status,
        anchor_phase=anchor,
        phase_statuses=statuses,
        canonical_phase_ids=canonical_ids,
    )
    accepted_reference = (
        phase5_reference(parent) if expected_start == "phase_6_report" else None
    )

    # When
    hydration = hydrate(parent, accepted_reference)

    # Then
    assert str(hydration.start_phase_id) == expected_start
    assert tuple(str(item.phase_id) for item in hydration.phase_results) == inherited
    assert all(item.inherited for item in hydration.phase_results)
    assert hydration.initial_state["prepared_environment"] == canonical_value(
        "phase_2_prepare"
    )
    assert (
        hydration.parent_accepted_attempt is accepted_reference
        if expected_start == "phase_6_report"
        else hydration.parent_accepted_attempt is None
    )


def test_hydrate_rejects_failed_predecessor(tmp_path: Path) -> None:
    # Given
    parent = create_hydration_parent(
        tmp_path,
        status="FAIL",
        anchor_phase="phase_6_report",
        phase_statuses=("passed", "passed", "failed", "passed", "failed"),
        canonical_phase_ids=PHASE_ORDER[:4],
    )

    # When / Then
    with pytest.raises(continuation_error()) as raised:
        _ = hydrate(parent, phase5_reference(parent))
    assert raised.value.kind.value == "failed_canonical_predecessor"


def test_hydrate_rejects_missing_canonical_output(tmp_path: Path) -> None:
    # Given
    parent = create_hydration_parent(
        tmp_path,
        status="FAIL",
        anchor_phase="phase_5_validation",
        phase_statuses=("passed", "passed", "passed", "failed", "skipped"),
        canonical_phase_ids=PHASE_ORDER[:2],
    )

    # When / Then
    with pytest.raises(continuation_error()) as raised:
        _ = hydrate(parent)
    assert raised.value.kind.value == "missing_canonical_output"


def test_hydrate_rejects_unknown_anchor(tmp_path: Path) -> None:
    # Given
    parent = create_hydration_parent(
        tmp_path,
        status="FAIL",
        anchor_phase="phase_9_unknown",
        phase_ids=("phase_0_detect", "phase_9_unknown"),
        phase_statuses=("passed", "failed"),
        canonical_phase_ids=("phase_0_detect",),
    )

    # When / Then
    with pytest.raises(continuation_error()) as raised:
        _ = hydrate(parent)
    assert raised.value.kind.value == "unknown_anchor"


def test_hydrate_rejects_ambiguous_anchor(tmp_path: Path) -> None:
    # Given
    duplicate_phase5 = b"""\
name: duplicate-anchor
version: 1
phases:
  - {id: phase_5_validation, type: builtin, operation: noop}
  - {id: phase_5_validation, type: builtin, operation: noop}
terminals: [complete]
"""
    parent = create_hydration_parent(
        tmp_path,
        status="FAIL",
        anchor_phase="phase_5_validation",
        phase_ids=("phase_5_validation",),
        phase_statuses=("failed",),
        canonical_phase_ids=(),
        workflow_bytes=duplicate_phase5,
    )

    # When / Then
    with pytest.raises(continuation_error()) as raised:
        _ = hydrate(parent)
    assert raised.value.kind.value == "ambiguous_anchor"


def test_hydrate_rejects_workflow_digest_drift(tmp_path: Path) -> None:
    # Given
    parent = create_hydration_parent(
        tmp_path,
        status="FAIL",
        anchor_phase="phase_5_validation",
        phase_statuses=("passed", "passed", "passed", "failed", "skipped"),
        canonical_phase_ids=PHASE_ORDER[:3],
    )
    resolved = resolve_terminal_parent(parent.summary_path)
    _ = parent.workflow_path.write_bytes(parent.workflow_path.read_bytes() + b"\n")

    # When / Then
    with pytest.raises(continuation_error()) as raised:
        _ = hydrate(parent, resolved=resolved)
    assert raised.value.kind.value == "workflow_digest_mismatch"


def test_hydrate_rejects_canonical_digest_drift(tmp_path: Path) -> None:
    # Given
    parent = create_hydration_parent(
        tmp_path,
        status="FAIL",
        anchor_phase="phase_5_validation",
        phase_statuses=("passed", "passed", "passed", "failed", "skipped"),
        canonical_phase_ids=PHASE_ORDER[:3],
    )
    resolved = resolve_terminal_parent(parent.summary_path)
    canonical = (
        parent.report_dir
        / "sealed-artifacts"
        / "validated"
        / "phase_0_detect_canonical.json"
    )
    _ = canonical.write_text('{"tampered": true}', encoding="utf-8")

    # When / Then
    with pytest.raises(continuation_error()) as raised:
        _ = hydrate(parent, resolved=resolved)
    assert raised.value.kind.value == "canonical_digest_mismatch"


def test_hydrate_rejects_missing_phase5_reference_after_phase5(
    tmp_path: Path,
) -> None:
    # Given
    parent = create_hydration_parent(
        tmp_path,
        status="FAIL",
        anchor_phase="phase_6_report",
        phase_statuses=("passed", "passed", "passed", "passed", "failed"),
        canonical_phase_ids=PHASE_ORDER[:4],
    )

    # When / Then
    with pytest.raises(continuation_error()) as raised:
        _ = hydrate(parent)
    assert raised.value.kind.value == "accepted_attempt_invalid"


def test_hydrate_rejects_canonical_without_success_record(tmp_path: Path) -> None:
    # Given
    parent = create_hydration_parent(
        tmp_path,
        status="FAIL",
        anchor_phase="phase_5_validation",
        phase_ids=("phase_0_detect", "phase_4_migrate", "phase_5_validation"),
        phase_statuses=("passed", "passed", "failed"),
        canonical_phase_ids=PHASE_ORDER[:3],
    )

    # When / Then
    with pytest.raises(continuation_error()) as raised:
        _ = hydrate(parent)
    assert raised.value.kind.value == "missing_canonical_output"


def continuation_error():
    from core import continuation as continuation_api

    return continuation_api.ContinuationHydrationError
