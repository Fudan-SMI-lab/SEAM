from __future__ import annotations

from pathlib import Path

from pydantic import JsonValue

from core import continuation as continuation_api
from core.continuation import (
    ContinuationHydration,
    ParentAcceptedAttemptReference,
    ResolvedTerminalParent,
    resolve_terminal_parent,
)
from core.run_manifest import CanonicalReference
from core.run_outcome import (
    AcceptedAttemptId,
    PhaseId,
    ReviewOutcome,
)
from tests.terminal_run_continuation_parent_scenarios import (
    ParentCanonicalFixture,
    ParentPhaseFixture,
    ParentRunScenario,
)
from tests.terminal_run_continuation_test_support import (
    ParentRun,
    create_parent_run,
)

WORKFLOW_BYTES = b"""\
name: continuation-workflow
version: 1
globals:
  review_fail_closed: true
phases:
  - id: phase_0_detect
    type: builtin
    operation: noop
    transitions: {on_success: phase_2_prepare}
  - id: phase_2_prepare
    type: builtin
    operation: noop
    output_as: prepared_environment
    transitions: {on_success: phase_4_migrate}
  - id: phase_4_migrate
    type: builtin
    operation: noop
    transitions: {on_success: phase_5_validation}
  - id: phase_5_validation
    type: builtin
    operation: noop
    transitions: {on_success: phase_6_report}
  - id: phase_6_report
    type: builtin
    operation: noop
    transitions: {on_success: complete}
terminals: [complete, failed]
"""

PHASE_ORDER = (
    "phase_0_detect",
    "phase_2_prepare",
    "phase_4_migrate",
    "phase_5_validation",
    "phase_6_report",
)


def canonical_value(phase_id: str) -> dict[str, JsonValue]:
    return {"phase": phase_id, "canonical": True}


def create_hydration_parent(
    tmp_path: Path,
    *,
    status: str,
    anchor_phase: str,
    phase_statuses: tuple[str, ...],
    canonical_phase_ids: tuple[str, ...],
    phase_ids: tuple[str, ...] = PHASE_ORDER,
    workflow_bytes: bytes = WORKFLOW_BYTES,
) -> ParentRun:
    assert len(phase_ids) == len(phase_statuses)
    scenario = ParentRunScenario(
        workflow_bytes=workflow_bytes,
        phases=tuple(
            ParentPhaseFixture(phase_id, phase_status)
            for phase_id, phase_status in zip(phase_ids, phase_statuses)
        ),
        canonical_outputs=tuple(
            ParentCanonicalFixture(phase_id, canonical_value(phase_id))
            for phase_id in canonical_phase_ids
        ),
    )
    return create_parent_run(
        tmp_path,
        status=status,
        phase_status="failed" if status == "FAIL" else "passed",
        anchor_phase=anchor_phase,
        scenario=scenario,
    )


def phase5_reference(parent: ParentRun) -> ParentAcceptedAttemptReference:
    parent_manifest = resolve_terminal_parent(parent.summary_path).run_manifest
    evidence = next(
        item
        for item in parent_manifest.sealed_evidence
        if item.relative_path == "validated/phase_5_validation_canonical.json"
    )
    reference = CanonicalReference(
        phase_id=PhaseId("phase_5_validation"),
        artifact_name="phase_5_validation_canonical.json",
        digest=evidence.digest,
    )
    receipt_evidence = next(
        item
        for item in parent_manifest.sealed_evidence
        if item.relative_path.endswith(".receipt.json")
    )
    return continuation_api.ParentAcceptedAttemptReference(
        parent_run_id=parent_manifest.run_id,
        attempt_id=AcceptedAttemptId("phase_5_validation-attempt-1"),
        canonical_reference=reference,
        receipt_evidence=receipt_evidence,
        review_outcome=ReviewOutcome.DISABLED,
        review_fail_closed=True,
        review_rounds=(),
    )


def hydrate(
    parent: ParentRun,
    accepted_reference: ParentAcceptedAttemptReference | None = None,
    resolved: ResolvedTerminalParent | None = None,
) -> ContinuationHydration:
    resolved_parent = resolved or resolve_terminal_parent(parent.summary_path)
    request = continuation_api.ContinuationHydrationRequest(
        parent=resolved_parent,
        summary_path=parent.summary_path,
        parent_accepted_attempt=accepted_reference,
    )
    return continuation_api.hydrate_terminal_parent(request)
