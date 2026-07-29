from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import NamedTuple

from core.types import TransitionDefinition


class TransitionRequest(NamedTuple):
    current_phase_id: str
    status: str
    transition: TransitionDefinition | None
    transitions: Mapping[str, str]
    phase_ids: Sequence[str]
    phase_index: Mapping[str, int]
    phase7_enabled: bool


def _phase7_target(target: str, enabled: bool) -> str:
    if target in ("phase_7a_evaluate", "phase_7b_refine") and not enabled:
        return "complete"
    return target


def plan_next_phase(request: TransitionRequest) -> str | None:
    transition = request.transition
    if transition is not None:
        targets = {
            "success": transition.on_success,
            "failure": transition.on_failure,
            "skipped": transition.on_skip,
            "stagnation": transition.on_stagnation,
            "reject_exhausted": transition.on_reject_exhausted,
        }
        target = targets.get(request.status)
        if target:
            return _phase7_target(target, request.phase7_enabled)
    aliases = {
        "success": ("success", "on_success"),
        "failure": ("failure", "on_failure"),
        "skipped": ("skipped", "on_skip"),
        "stagnation": ("stagnation", "on_stagnation"),
        "reject_exhausted": ("reject_exhausted", "on_reject_exhausted"),
    }
    for key in aliases.get(request.status, (request.status,)):
        target = request.transitions.get(key)
        if target:
            return _phase7_target(target, request.phase7_enabled)
    if request.status not in ("success", "skipped"):
        return None
    index = request.phase_index.get(request.current_phase_id, -1)
    if index >= 0 and index + 1 < len(request.phase_ids):
        return _phase7_target(request.phase_ids[index + 1], request.phase7_enabled)
    return None
