from __future__ import annotations

from typing import NamedTuple


class StagnationState(NamedTuple):
    last_error_signature: str
    stagnation_count: int


class StagnationDecision(NamedTuple):
    state: StagnationState
    stagnated: bool


def reduce_stagnation(
    error_signature: str,
    previous: StagnationState,
    threshold: int,
) -> StagnationDecision:
    normalized = "\n".join(line.rstrip() for line in error_signature.splitlines())
    if normalized == previous.last_error_signature and normalized:
        count = previous.stagnation_count + 1
    else:
        count = 1
    state = StagnationState(normalized, count)
    return StagnationDecision(state, count >= threshold)
