from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import Enum, unique
from typing import NamedTuple

from core.compat import TypeAlias, assert_never

from core.continuation_models import PhasePresentationStatus
from harness.run.finalization_contract import PhaseStatus

ResultScalar: TypeAlias = "str | int | float | bool | None"


@unique
class WorkflowExecutionStatus(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    SKIPPED = "skipped"
    UNKNOWN = "unknown"

    @classmethod
    def from_raw(cls, raw: ResultScalar) -> WorkflowExecutionStatus:
        if not isinstance(raw, str):
            return cls.UNKNOWN
        try:
            return cls(raw)
        except ValueError:
            return cls.UNKNOWN


class WorkflowResultProjection(NamedTuple):
    phases: tuple[PhaseStatus, ...]
    entry_script: str | None


def _project_status(
    status: WorkflowExecutionStatus,
    summary: str,
) -> tuple[PhasePresentationStatus, str | None]:
    if status is WorkflowExecutionStatus.SUCCESS:
        return PhasePresentationStatus.PASSED, None
    elif status is WorkflowExecutionStatus.FAILURE:
        return PhasePresentationStatus.FAILED, summary[:500]
    elif status is WorkflowExecutionStatus.SKIPPED:
        return PhasePresentationStatus.SKIPPED, None
    elif status is WorkflowExecutionStatus.UNKNOWN:
        return PhasePresentationStatus.UNKNOWN, None
    else:
        assert_never(status)


def project_workflow_result(
    phase_ids: Sequence[str],
    phase_results: Mapping[str, Mapping[str, ResultScalar]],
    state: Mapping[str, Mapping[str, ResultScalar] | ResultScalar],
) -> WorkflowResultProjection:
    order = {phase_id: index for index, phase_id in enumerate(phase_ids)}
    projected: list[PhaseStatus] = []
    for phase_id, result in phase_results.items():
        execution_status = WorkflowExecutionStatus.from_raw(result.get("status"))
        summary = str(result.get("output_summary", ""))
        duration = float(result.get("duration", 0.0) or 0.0)
        presentation_status, error = _project_status(execution_status, summary)
        projected.append(
            PhaseStatus(
                phase_number=order.get(phase_id, 999) + 1,
                phase_id=phase_id,
                label=phase_id,
                status=presentation_status,
                duration_seconds=round(duration, 3),
                error=error,
            )
        )
    projected.sort(key=lambda phase: phase.phase_number)
    phase_3 = state.get("phase_3_entry_script")
    entry_script = None
    if isinstance(phase_3, Mapping):
        command = phase_3.get("run_command")
        if isinstance(command, str) and command.strip():
            entry_script = command
    return WorkflowResultProjection(tuple(projected), entry_script)
