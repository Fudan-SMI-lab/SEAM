from __future__ import annotations

from pathlib import Path

import pytest
from typing_extensions import override

from core.continuation import current_project_owner_lock
from core.continuation_evidence import (
    seal_child_evidence,
    verify_final_child_evidence,
)
from core.terminal_continuation import prepare_terminal_continuation
from core import terminal_continuation as lifecycle
from tests.terminal_run_continuation_hydration_support import (
    PHASE_ORDER,
    create_hydration_parent,
)
from tests.terminal_run_continuation_test_support import tree_bytes


class _ChildExecutionFailure(RuntimeError):
    __slots__ = ("detail",)

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail

    @override
    def __str__(self) -> str:
        return self.detail


class _UnexpectedWorkflowReload(RuntimeError):
    pass


def test_terminal_continuation_prepares_and_seals_child_under_owner_lock(
    tmp_path: Path,
) -> None:
    # Given
    parent = create_hydration_parent(
        tmp_path,
        status="FAIL",
        anchor_phase="phase_2_prepare",
        phase_statuses=("passed", "failed", "skipped", "skipped", "skipped"),
        canonical_phase_ids=PHASE_ORDER[:1],
    )
    parent_evidence_before = tree_bytes(parent.report_dir)

    # When
    with prepare_terminal_continuation(
        parent.summary_path,
        "child-run-001",
    ) as prepared:
        active_lock = current_project_owner_lock()
        assert active_lock is not None and active_lock.active
        assert prepared.parent.run_id != prepared.evidence.child_store.read().run_id
        assert str(prepared.hydration.start_phase_id) == "phase_2_prepare"
        assert prepared.prompt_facts.resource_eligibility == (
            "phase2_establishment_eligible"
        )
        _ = seal_child_evidence(prepared.evidence)
        verified = verify_final_child_evidence(prepared.evidence)

    # Then
    assert current_project_owner_lock() is None
    assert tree_bytes(parent.report_dir) == parent_evidence_before
    assert verified.child_manifest.parent_run_id == verified.parent_manifest.run_id
    assert verified.child_manifest.evidence_sealed


def test_terminal_continuation_releases_owner_after_child_failure(
    tmp_path: Path,
) -> None:
    # Given
    parent = create_hydration_parent(
        tmp_path,
        status="FAIL",
        anchor_phase="phase_2_prepare",
        phase_statuses=("passed", "failed", "skipped", "skipped", "skipped"),
        canonical_phase_ids=PHASE_ORDER[:1],
    )
    parent_evidence_before = tree_bytes(parent.report_dir)

    # When
    with pytest.raises(_ChildExecutionFailure, match="child execution failed"):
        with prepare_terminal_continuation(
            parent.summary_path,
            "child-run-002",
        ):
            raise _ChildExecutionFailure("child execution failed")

    # Then
    assert current_project_owner_lock() is None
    assert tree_bytes(parent.report_dir) == parent_evidence_before


def test_terminal_continuation_executes_the_verified_workflow_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    parent = create_hydration_parent(
        tmp_path,
        status="FAIL",
        anchor_phase="phase_2_prepare",
        phase_statuses=("passed", "failed", "skipped", "skipped", "skipped"),
        canonical_phase_ids=PHASE_ORDER[:1],
    )

    def reject_reload(_path: str):
        raise _UnexpectedWorkflowReload

    monkeypatch.setattr(lifecycle, "load_workflow", reject_reload, raising=False)

    # When
    with prepare_terminal_continuation(
        parent.summary_path,
        "child-run-003",
    ) as prepared:
        workflow_name = prepared.workflow.name

    # Then
    assert workflow_name == "continuation-workflow"
