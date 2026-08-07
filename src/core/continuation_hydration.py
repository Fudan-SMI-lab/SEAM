from __future__ import annotations

from pathlib import Path
from typing import Final

from core.compat import assert_never

from core.continuation_accepted_attempt import verify_accepted_attempt
from core.continuation_hydration_authority import (
    load_canonical_json,
    require_hydration_authority,
)
from core.continuation_hydration_models import (
    ContinuationHydration,
    ContinuationHydrationError,
    ContinuationHydrationErrorKind,
    ContinuationHydrationRequest,
    InheritedPhaseResult,
    InheritedStateValue,
)
from core.continuation_models import PhasePresentationStatus, TerminalParentStatus
from core.run_manifest import CanonicalReference, EvidenceDigest
from core.run_outcome import PhaseId
from core.types import PhaseDefinition, WorkflowDefinition

_PHASE5: Final = PhaseId("phase_5_validation")


def _error(
    kind: ContinuationHydrationErrorKind, detail: str
) -> ContinuationHydrationError:
    return ContinuationHydrationError(kind, detail)


def _start_index(
    parent_status: TerminalParentStatus,
    parent_anchor: PhaseId,
    workflow: WorkflowDefinition,
) -> tuple[PhaseId, int, int]:
    phase_ids = tuple(PhaseId(phase.id) for phase in workflow.phases)
    phase5_count = phase_ids.count(_PHASE5)
    if phase5_count != 1:
        kind = (
            ContinuationHydrationErrorKind.UNKNOWN_ANCHOR
            if phase5_count == 0
            else ContinuationHydrationErrorKind.AMBIGUOUS_ANCHOR
        )
        raise _error(kind, "pinned workflow must contain exactly one Phase 5")
    start_phase = {
        TerminalParentStatus.PASS: _PHASE5,
        TerminalParentStatus.FAIL: parent_anchor,
    }[parent_status]
    anchor_count = phase_ids.count(start_phase)
    if anchor_count != 1:
        kind = (
            ContinuationHydrationErrorKind.UNKNOWN_ANCHOR
            if anchor_count == 0
            else ContinuationHydrationErrorKind.AMBIGUOUS_ANCHOR
        )
        raise _error(kind, f"continuation anchor is not unique: {start_phase}")
    return start_phase, phase_ids.index(start_phase), phase_ids.index(_PHASE5)


def _evidence_for(
    phase_id: PhaseId, inventory: tuple[EvidenceDigest, ...]
) -> EvidenceDigest:
    phase_name = str(phase_id)
    key = phase_name[6:] if phase_name.startswith("phase_") else phase_name
    artifact_name = f"phase_{key}_canonical.json"
    relative_path = f"validated/{artifact_name}"
    matches = tuple(item for item in inventory if item.relative_path == relative_path)
    if len(matches) != 1:
        raise _error(
            ContinuationHydrationErrorKind.MISSING_CANONICAL_OUTPUT,
            f"successful predecessor has no unique canonical output: {phase_id}",
        )
    return matches[0]


def _inherit_phase(
    phase: PhaseDefinition,
    sealed_root: Path,
    inventory: tuple[EvidenceDigest, ...],
) -> tuple[InheritedStateValue, InheritedPhaseResult]:
    phase_id = PhaseId(phase.id)
    evidence = _evidence_for(phase_id, inventory)
    _, canonical_json = load_canonical_json(sealed_root, evidence)
    state_key = phase.output_as or phase.id
    reference = CanonicalReference(
        phase_id=phase_id,
        artifact_name=Path(evidence.relative_path).name,
        digest=evidence.digest,
    )
    return (
        InheritedStateValue(state_key=state_key, canonical_json=canonical_json),
        InheritedPhaseResult(
            phase_id=phase_id,
            state_key=state_key,
            canonical_reference=reference,
        ),
    )


def hydrate_terminal_parent(
    request: ContinuationHydrationRequest,
) -> ContinuationHydration:
    authority = require_hydration_authority(request)
    summary = authority.summary
    workflow = authority.workflow
    start_phase, start_index, phase5_index = _start_index(
        request.parent.status,
        request.parent.terminal_anchor.phase_id,
        workflow,
    )
    summaries = {phase.phase_id: phase for phase in summary.phases}
    state_keys: set[str] = set()
    state_entries: list[InheritedStateValue] = []
    inherited: list[InheritedPhaseResult] = []
    sealed_root = authority.run_dir / "sealed-artifacts"
    for phase in workflow.phases[:start_index]:
        phase_summary = summaries.get(phase.id)
        if phase_summary is None:
            raise _error(
                ContinuationHydrationErrorKind.MISSING_CANONICAL_OUTPUT,
                f"predecessor has no authoritative phase result: {phase.id}",
            )
        status = phase_summary.status
        if status is PhasePresentationStatus.FAILED:
            raise _error(
                ContinuationHydrationErrorKind.FAILED_CANONICAL_PREDECESSOR,
                f"canonical predecessor failed: {phase.id}",
            )
        elif status is PhasePresentationStatus.SKIPPED:
            continue
        elif status is PhasePresentationStatus.PASSED:
            pass
        elif status is PhasePresentationStatus.UNKNOWN:
            raise _error(
                ContinuationHydrationErrorKind.AUTHORITY_MISMATCH,
                "predecessor status is not terminal: unknown",
            )
        else:
            assert_never(status)
        state_entry, result = _inherit_phase(
            phase, sealed_root, request.parent.run_manifest.sealed_evidence
        )
        if state_entry.state_key in state_keys:
            raise _error(
                ContinuationHydrationErrorKind.AMBIGUOUS_ANCHOR,
                f"canonical state key is ambiguous: {state_entry.state_key}",
            )
        state_keys.add(state_entry.state_key)
        state_entries.append(state_entry)
        inherited.append(result)
    accepted = None
    if start_index > phase5_index:
        accepted = request.parent_accepted_attempt
        phase5_result = next(
            (item for item in inherited if item.phase_id == _PHASE5), None
        )
        if (
            accepted is None
            or phase5_result is None
            or accepted.canonical_reference != phase5_result.canonical_reference
            or accepted.review_fail_closed
            != ((workflow.globals or {}).get("review_fail_closed") is not False)
        ):
            raise _error(
                ContinuationHydrationErrorKind.ACCEPTED_ATTEMPT_INVALID,
                "post-Phase5 continuation requires the inherited accepted attempt",
            )
        verify_accepted_attempt(accepted, request, sealed_root)
    return ContinuationHydration(
        state_entries=tuple(state_entries),
        phase_results=tuple(inherited),
        start_phase_id=start_phase,
        parent_accepted_attempt=accepted,
    )
