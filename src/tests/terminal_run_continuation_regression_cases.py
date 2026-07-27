from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

import core.continuation_lock as continuation_lock
from core.continuation import (
    ContinuationError,
    ContinuationErrorKind,
    ContinuationRequest,
    TerminalParentStatus,
    claim_terminal_parent,
    resolve_terminal_parent,
)
from core.continuation_resolver import resolve_authority
from tests.terminal_run_continuation_lock_cases import _lock_path
from tests.terminal_run_continuation_test_support import (
    PARENT_RUN_ID,
    claim_rejection,
    create_parent_run,
    read_summary,
    write_summary,
)


class FinalizationFailure(RuntimeError):
    pass


def test_resolve_eligible_fail_parent(tmp_path: Path) -> None:
    # Given an explicit terminal FAIL parent with a failed authoritative anchor.
    parent = create_parent_run(tmp_path, status="FAIL", phase_status="failed")

    # When the supplied summary is resolved.
    resolved = resolve_terminal_parent(parent.summary_path)

    # Then FAIL remains eligible without being promoted to PASS.
    assert resolved.status is TerminalParentStatus.FAIL
    assert resolved.terminal_anchor.phase_id == "phase_5_validation"


@pytest.mark.skipif(os.name != "nt", reason="Windows 8.3 aliases are platform-specific")
def test_resolve_accepts_valid_windows_short_path_alias(tmp_path: Path) -> None:
    # Given the exact summary expressed through a valid Windows 8.3 spelling.
    parent = create_parent_run(tmp_path)
    home = Path.home().resolve(strict=True)
    completed = subprocess.run(
        ["cmd", "/d", "/c", "dir", "/x", str(home.parent)],
        check=True,
        capture_output=True,
        text=True,
        encoding="mbcs",
    )
    matching_line = next(
        (
            line
            for line in completed.stdout.splitlines()
            if line.rstrip().endswith(home.name)
        ),
        "",
    )
    short_component = next(
        (part for part in matching_line.split() if "~" in part),
        "",
    )
    if not short_component:
        pytest.skip("8.3 aliases are disabled on this volume")
    short_home = home.parent / short_component
    short_summary = short_home / parent.summary_path.relative_to(home)

    # When the physical alias is resolved.
    resolved = resolve_terminal_parent(short_summary)

    # Then one physical parent is accepted without treating the alias as a link.
    assert resolved.run_id == PARENT_RUN_ID
    assert resolved.output_project == parent.project_dir.resolve(strict=True)


def test_eligibility_rejects_parent_reused_as_child_id(tmp_path: Path) -> None:
    # Given an eligible parent selected as its own proposed child.
    parent = create_parent_run(tmp_path)

    # When continuation ownership is attempted.
    kind = claim_rejection(parent, child_run_id=PARENT_RUN_ID)

    # Then continuation requires a distinct new child run ID.
    assert kind is ContinuationErrorKind.CHILD_RUN_ID_REUSED


def test_eligibility_rejects_pass_with_failed_phase(tmp_path: Path) -> None:
    # Given a PASS summary that contradicts itself with another failed phase.
    parent = create_parent_run(tmp_path)
    payload = read_summary(parent)
    phases = payload["phases"]
    assert isinstance(phases, list)
    phases.append(
        {
            "phase_number": 4,
            "phase_id": "phase_4_migration",
            "label": "phase_4_migration",
            "status": "failed",
            "duration_seconds": 0.5,
            "error": "migration failed",
        }
    )
    write_summary(parent, payload)

    # When continuation eligibility is claimed.
    kind = claim_rejection(parent)

    # Then a contradictory PASS parent cannot continue.
    assert kind is ContinuationErrorKind.INCOMPLETE_PARENT


def test_lock_releases_after_caller_finalization_error(tmp_path: Path) -> None:
    # Given an eligible parent and its deterministic owner lock.
    parent = create_parent_run(tmp_path)
    lock_path = _lock_path(parent.project_dir, parent.reports_root)

    # When caller finalization raises inside the ownership context.
    with pytest.raises(FinalizationFailure):
        with claim_terminal_parent(
            ContinuationRequest(
                summary_path=parent.summary_path,
                child_run_id="child-run-001",
            )
        ):
            assert lock_path.is_file()
            raise FinalizationFailure

    # Then deterministic context cleanup releases only after the error unwinds.
    assert not lock_path.exists()


def test_eligibility_translates_invalid_storage_boundary(tmp_path: Path) -> None:
    # Given a summary that makes the authority root its claimed output project.
    parent = create_parent_run(tmp_path)
    payload = read_summary(parent)
    payload["temp_dir"] = str(parent.reports_root)
    write_summary(parent, payload)

    # When continuation eligibility is claimed.
    kind = claim_rejection(parent)

    # Then the public continuation boundary returns its typed authority error.
    assert kind is ContinuationErrorKind.AUTHORITY_INVALID


def test_lock_fstat_failure_does_not_strand_new_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given resolved authority and an injected first identity-probe failure.
    parent = create_parent_run(tmp_path)
    authority = resolve_authority(parent.summary_path)
    lock_path = _lock_path(parent.project_dir, parent.reports_root)

    def fail_fstat(_descriptor: int) -> os.stat_result:
        raise OSError("identity unavailable")

    monkeypatch.setattr(continuation_lock.os, "fstat", fail_fstat)

    # When exclusive lock publication begins.
    with pytest.raises(ContinuationError) as raised:
        _ = continuation_lock._ExclusiveProjectLock(authority, "child-run-001")

    # Then failure is typed and the owner file is not stranded.
    assert raised.value.kind is ContinuationErrorKind.LOCK_IO
    assert not lock_path.exists()
