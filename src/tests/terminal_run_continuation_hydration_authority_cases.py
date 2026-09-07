from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

from core.continuation import resolve_terminal_parent
from core.run_outcome import PhaseId, TerminalAnchor
from tests.terminal_run_continuation_hydration_cases import continuation_error
from tests.terminal_run_continuation_hydration_support import (
    PHASE_ORDER,
    create_hydration_parent,
    hydrate,
    phase5_reference,
)
from tests.terminal_run_continuation_test_support import read_summary, write_summary


def test_hydrate_rejects_resolved_anchor_manifest_mismatch(tmp_path: Path) -> None:
    # Given
    parent = create_hydration_parent(
        tmp_path,
        status="FAIL",
        anchor_phase="phase_5_validation",
        phase_statuses=("passed", "passed", "passed", "failed", "skipped"),
        canonical_phase_ids=PHASE_ORDER[:3],
    )
    resolved = resolve_terminal_parent(parent.summary_path)
    forged = resolved.model_copy(
        update={"terminal_anchor": TerminalAnchor(PhaseId("phase_4_migrate"))}
    )

    # When / Then
    with pytest.raises(continuation_error()) as raised:
        _ = hydrate(parent, resolved=forged)
    assert raised.value.kind.value == "authority_mismatch"


def test_hydrate_rejects_summary_snapshot_drift(tmp_path: Path) -> None:
    # Given
    parent = create_hydration_parent(
        tmp_path,
        status="FAIL",
        anchor_phase="phase_5_validation",
        phase_statuses=("passed", "passed", "passed", "failed", "skipped"),
        canonical_phase_ids=PHASE_ORDER[:3],
    )
    resolved = resolve_terminal_parent(parent.summary_path)
    assert (
        str(resolved.summary_digest)
        == hashlib.sha256(parent.summary_path.read_bytes()).hexdigest()
    )
    payload = read_summary(parent)
    phases = payload["phases"]
    assert isinstance(phases, list)
    phase4 = phases[2]
    assert isinstance(phase4, dict)
    phase4["status"] = "failed"
    write_summary(parent, payload)

    # When / Then
    with pytest.raises(continuation_error()) as raised:
        _ = hydrate(parent, resolved=resolved)
    assert raised.value.kind.value == "authority_mismatch"


def test_hydrate_rejects_forged_accepted_attempt_id(tmp_path: Path) -> None:
    # Given
    parent = create_hydration_parent(
        tmp_path,
        status="FAIL",
        anchor_phase="phase_6_report",
        phase_statuses=("passed", "passed", "passed", "passed", "failed"),
        canonical_phase_ids=PHASE_ORDER[:4],
    )
    accepted = phase5_reference(parent)
    forged = replace(
        accepted,
        attempt_id=type(accepted.attempt_id)("phase_5_validation-attempt-2"),
    )

    # When / Then
    with pytest.raises(continuation_error()) as raised:
        _ = hydrate(parent, forged)
    assert raised.value.kind.value == "accepted_attempt_invalid"


def test_hydration_state_is_copy_on_read(tmp_path: Path) -> None:
    # Given
    parent = create_hydration_parent(
        tmp_path,
        status="FAIL",
        anchor_phase="phase_5_validation",
        phase_statuses=("passed", "passed", "passed", "failed", "skipped"),
        canonical_phase_ids=PHASE_ORDER[:3],
    )
    hydration = hydrate(parent)

    # When
    mutable = hydration.initial_state
    mutable["prepared_environment"]["phase"] = "tampered"

    # Then
    assert hydration.initial_state["prepared_environment"]["phase"] == "phase_2_prepare"


def test_hydrate_rejects_state_bytes_detached_from_provenance(tmp_path: Path) -> None:
    parent = create_hydration_parent(
        tmp_path,
        status="FAIL",
        anchor_phase="phase_5_validation",
        phase_statuses=("passed", "passed", "passed", "failed", "skipped"),
        canonical_phase_ids=PHASE_ORDER[:3],
    )
    hydration = hydrate(parent)
    first = replace(hydration.state_entries[0], canonical_json=b'{"forged":true}')

    with pytest.raises(continuation_error()) as raised:
        _ = replace(hydration, state_entries=(first, *hydration.state_entries[1:]))

    assert raised.value.kind.value == "authority_mismatch"
