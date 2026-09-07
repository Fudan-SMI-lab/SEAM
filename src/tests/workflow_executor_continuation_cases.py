from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core import continuation as continuation_api
from core.config import load_workflow
from core.run_outcome import PhaseId, TerminalOutcome
from core.workflow_executor import WorkflowExecutor
from tests.terminal_run_continuation_hydration_support import (
    PHASE_ORDER,
    WORKFLOW_BYTES,
    create_hydration_parent,
    hydrate,
    phase5_reference,
)


@pytest.mark.parametrize(
    ("status", "anchor", "statuses", "expected_calls"),
    (
        ("PASS", "phase_6_report", ("passed",) * 5, PHASE_ORDER[3:]),
        (
            "FAIL",
            "phase_4_migrate",
            ("passed", "passed", "failed", "skipped", "skipped"),
            PHASE_ORDER[2:],
        ),
        (
            "FAIL",
            "phase_5_validation",
            ("passed", "passed", "passed", "failed", "skipped"),
            PHASE_ORDER[3:],
        ),
        (
            "FAIL",
            "phase_6_report",
            ("passed", "passed", "passed", "passed", "failed"),
            PHASE_ORDER[4:],
        ),
    ),
)
def test_continuation_executor_starts_at_anchor_and_marks_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: str,
    anchor: str,
    statuses: tuple[str, ...],
    expected_calls: tuple[str, ...],
) -> None:
    # Given
    anchor_index = PHASE_ORDER.index(anchor)
    canonical_ids = PHASE_ORDER if status == "PASS" else PHASE_ORDER[:anchor_index]
    if anchor == "phase_6_report":
        canonical_ids = PHASE_ORDER[:4]
    parent = create_hydration_parent(
        tmp_path,
        status=status,
        anchor_phase=anchor,
        phase_statuses=statuses,
        canonical_phase_ids=canonical_ids,
    )
    reference = phase5_reference(parent) if anchor == "phase_6_report" else None
    hydration = hydrate(parent, reference)
    executor = WorkflowExecutor(
        load_workflow(str(parent.workflow_path)),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        project_dir=str(tmp_path),
        output_dir=str(tmp_path),
        continuation=hydration,
    )
    executor.hook_manager = MagicMock()
    calls: list[str] = []

    def execute_builtin(phase, state, context):
        calls.append(phase.id)
        if phase.id == "phase_5_validation":
            return "success", {
                "status": "success",
                "loop_state": {
                    "script_exit_code": 0,
                    "latest_shell_attempt_artifacts": {"attempt": 2},
                },
            }
        return "success", {"child_phase": phase.id}

    monkeypatch.setattr(executor, "_execute_builtin_phase", execute_builtin)

    # When
    result = executor.execute({})

    # Then
    assert tuple(calls) == expected_calls
    assert all(
        result["phase_results"][phase_id]["inherited"]
        for phase_id in canonical_ids
        if PHASE_ORDER.index(phase_id) < PHASE_ORDER.index(hydration.start_phase_id)
    )
    assert all(
        result["phase_results"][phase_id]["inherited"] is False
        for phase_id in expected_calls
    )
    assert result["state_provenance"]["prepared_environment"]["inherited"] is True
    assert result["state_provenance"][expected_calls[-1]]["inherited"] is False
    assert (
        tuple(str(item) for item in result["run_outcome"].executed_phases)
        == expected_calls
    )
    assert result["run_outcome"].terminal_outcome is TerminalOutcome.PASSED
    if hydration.parent_accepted_attempt is not None:
        assert reference is not None
        assert result["run_outcome"].accepted_attempt_id == reference.attempt_id
    elif "phase_5_validation" in expected_calls:
        assert (
            str(result["run_outcome"].accepted_attempt_id)
            == "phase_5_validation-attempt-2"
        )


def test_hydrate_executor_rejects_empty_child_execution(tmp_path: Path) -> None:
    # Given
    parent = create_hydration_parent(
        tmp_path,
        status="FAIL",
        anchor_phase="phase_5_validation",
        phase_statuses=("passed", "passed", "passed", "failed", "skipped"),
        canonical_phase_ids=PHASE_ORDER[:3],
    )
    hydration = replace(hydrate(parent), start_phase_id=PhaseId("complete"))

    # When / Then
    with pytest.raises(continuation_api.ContinuationHydrationError) as raised:
        WorkflowExecutor(
            load_workflow(str(parent.workflow_path)),
            MagicMock(),
            MagicMock(),
            MagicMock(),
            MagicMock(),
            continuation=hydration,
        ).execute({})
    assert raised.value.kind.value == "empty_child_execution"


def test_hydrate_executor_rejects_conditionally_skipped_anchor(
    tmp_path: Path,
) -> None:
    # Given
    workflow_bytes = WORKFLOW_BYTES.replace(
        b"  - id: phase_6_report\n    type: builtin\n",
        b"  - id: phase_6_report\n    type: builtin\n"
        b"    condition: ${context.WRITE_REPORT} == true\n",
    )
    parent = create_hydration_parent(
        tmp_path,
        status="FAIL",
        anchor_phase="phase_6_report",
        phase_statuses=("passed", "passed", "passed", "passed", "failed"),
        canonical_phase_ids=PHASE_ORDER[:4],
        workflow_bytes=workflow_bytes,
    )
    hydration = hydrate(parent, phase5_reference(parent))
    executor = WorkflowExecutor(
        load_workflow(str(parent.workflow_path)),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        continuation=hydration,
    )

    # When / Then
    with pytest.raises(continuation_api.ContinuationHydrationError) as raised:
        _ = executor.execute({"WRITE_REPORT": False})
    assert raised.value.kind.value == "empty_child_execution"


def test_hydrate_executor_rejects_anchor_returning_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = create_hydration_parent(
        tmp_path,
        status="FAIL",
        anchor_phase="phase_6_report",
        phase_statuses=("passed", "passed", "passed", "passed", "failed"),
        canonical_phase_ids=PHASE_ORDER[:4],
    )
    hydration = hydrate(parent, phase5_reference(parent))
    executor = WorkflowExecutor(
        load_workflow(str(parent.workflow_path)),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        continuation=hydration,
    )
    monkeypatch.setattr(
        executor,
        "_execute_builtin_phase",
        lambda phase, state, context: ("skipped", {"phase": phase.id}),
    )

    with pytest.raises(continuation_api.ContinuationHydrationError) as raised:
        _ = executor.execute({})

    assert raised.value.kind.value == "empty_child_execution"
