"""Bounded config-specific repair and revalidation loop — public API.

At most two transactional edit/revalidate rounds. Invalid or unavailable
actions fail closed (STOPPED) after one prompt. Transaction/filesystem
failures become typed TERMINAL outcomes. Prompt and outcome text is
redacted before truncation. Classification, outcome types, request types,
and port protocols live in :mod:`seam_init.repair_classify`; the internal
state machine lives in :mod:`seam_init.repair_loop`.
"""
from __future__ import annotations

from seam_init.repair_classify import (
    OmoEditPort,
    OmoRevalidatePort,
    OpencodeEditPort,
    OpencodeRevalidatePort,
    RepairOutcome,
    RepairRequest,
    RepairStatus,
)
from seam_init.repair_loop import _Loop

__all__ = [
    "OmoEditPort", "OmoRevalidatePort", "OpencodeEditPort",
    "OpencodeRevalidatePort", "RepairOutcome", "RepairRequest",
    "RepairStatus", "run_repair",
]


def run_repair(request: RepairRequest) -> RepairOutcome:
    """Run the bounded repair loop with at most two transactional rounds."""
    return _Loop(request).run()
