from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import NamedTuple

from typing_extensions import TypeAlias

from harness.run.finalization_contract import PhaseStatus


ResultScalar: TypeAlias = "str | int | float | bool | None"


class WorkflowResultProjection(NamedTuple):
    phases: tuple[PhaseStatus, ...]
    entry_script: str | None


def project_workflow_result(
    phase_ids: Sequence[str],
    phase_results: Mapping[str, Mapping[str, ResultScalar]],
    state: Mapping[str, Mapping[str, ResultScalar] | ResultScalar],
) -> WorkflowResultProjection:
    order = {phase_id: index for index, phase_id in enumerate(phase_ids)}
    projected: list[PhaseStatus] = []
    statuses = {"success": "passed", "failure": "failed", "skipped": "skipped"}
    for phase_id, result in phase_results.items():
        status = str(result.get("status", "unknown"))
        summary = str(result.get("output_summary", ""))
        duration = float(result.get("duration", 0.0) or 0.0)
        projected.append(
            PhaseStatus(
                phase_number=order.get(phase_id, 999) + 1,
                phase_id=phase_id,
                label=phase_id,
                status=statuses.get(status, status),
                duration_seconds=round(duration, 3),
                error=summary[:500] if status == "failure" else None,
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
