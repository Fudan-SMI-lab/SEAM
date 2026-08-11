"""Typed stage ledger enforcing workflow ordering invariants.

Exactly one ``IN_PROGRESS`` at a time; ``append_terminal`` rejects active
IN_PROGRESS and duplicate StageKinds; ``replace_last`` rejects a different
kind; ``snapshot`` rejects IN_PROGRESS leaks.
"""
from __future__ import annotations

from seam_init.models import StageKind, StageRecord, StageStatus

__all__ = ["StageLedger"]


class StageLedger:
    """Mutable ordered ledger; enforces one current IN_PROGRESS stage."""

    __slots__ = ("_records", "_current", "_seen")

    def __init__(self) -> None:
        self._records: list[StageRecord] = []
        self._current: StageKind | None = None
        self._seen: set[StageKind] = set()

    def begin(self, kind: StageKind) -> None:
        if self._current is not None:
            raise RuntimeError(f"begin({kind.value}) while {self._current.value} IN_PROGRESS")
        if kind in self._seen:
            raise RuntimeError(f"begin({kind.value}): stage already in ledger")
        self._records.append(StageRecord(kind, StageStatus.IN_PROGRESS))
        self._current = kind
        self._seen.add(kind)

    def complete(self, status: StageStatus) -> None:
        if self._current is None:
            raise RuntimeError("complete() without begin()")
        if status is StageStatus.IN_PROGRESS:
            raise RuntimeError("complete(IN_PROGRESS) is not terminal")
        self._records[-1] = StageRecord(self._current, status)
        self._current = None

    def append_terminal(self, kind: StageKind, status: StageStatus) -> None:
        if self._current is not None:
            raise RuntimeError(f"append_terminal while {self._current.value} IN_PROGRESS")
        if status is StageStatus.IN_PROGRESS:
            raise RuntimeError("append_terminal requires terminal status")
        if kind in self._seen:
            raise RuntimeError(f"append_terminal({kind.value}): duplicate stage")
        self._records.append(StageRecord(kind, status))
        self._seen.add(kind)

    def replace_last(self, kind: StageKind, status: StageStatus) -> None:
        if not self._records:
            raise RuntimeError("replace_last on empty ledger")
        if status is StageStatus.IN_PROGRESS:
            raise RuntimeError("replace_last requires terminal status")
        old = self._records[-1]
        if old.kind is not kind:
            raise RuntimeError(f"replace_last kind mismatch: {old.kind.value} vs {kind.value}")
        self._records[-1] = StageRecord(kind, status)

    def snapshot(self) -> tuple[StageRecord, ...]:
        if self._current is not None:
            raise RuntimeError(f"snapshot while {self._current.value} IN_PROGRESS")
        return tuple(self._records)

    @property
    def current(self) -> StageKind | None:
        return self._current

    def __len__(self) -> int:
        return len(self._records)
