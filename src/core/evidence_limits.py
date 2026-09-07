from __future__ import annotations

from pathlib import PurePath
from typing import Final, final

MAX_EVIDENCE_ENTRIES: Final = 100_000
MAX_EVIDENCE_DEPTH: Final = 128
MAX_EVIDENCE_FILE_BYTES: Final = 64 * 1024 * 1024
MAX_EVIDENCE_TOTAL_BYTES: Final = 512 * 1024 * 1024


@final
class EvidenceBudget:
    __slots__ = ("entries", "total_bytes")
    entries: int
    total_bytes: int

    def __init__(self) -> None:
        self.entries = 0
        self.total_bytes = 0

    def charge(self, relative_path: PurePath, size_bytes: int = 0) -> None:
        self.entries += 1
        self.total_bytes += size_bytes
        if self.entries > MAX_EVIDENCE_ENTRIES:
            raise OSError("evidence entry limit exceeded")
        if len(relative_path.parts) > MAX_EVIDENCE_DEPTH:
            raise OSError("evidence depth limit exceeded")
        if size_bytes > MAX_EVIDENCE_FILE_BYTES:
            raise OSError("evidence file byte limit exceeded")
        if self.total_bytes > MAX_EVIDENCE_TOTAL_BYTES:
            raise OSError("evidence aggregate byte limit exceeded")
