from __future__ import annotations

from pathlib import PurePath

import pytest

import core.evidence_limits as limits


def test_evidence_entry_limit_is_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(limits, "MAX_EVIDENCE_ENTRIES", 1)
    budget = limits.EvidenceBudget()
    budget.charge(PurePath("one"))

    with pytest.raises(OSError, match="entry limit"):
        budget.charge(PurePath("two"))


def test_evidence_depth_limit_is_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(limits, "MAX_EVIDENCE_DEPTH", 1)

    with pytest.raises(OSError, match="depth limit"):
        limits.EvidenceBudget().charge(PurePath("one/two"))


def test_evidence_file_limit_is_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(limits, "MAX_EVIDENCE_FILE_BYTES", 4)

    with pytest.raises(OSError, match="file byte limit"):
        limits.EvidenceBudget().charge(PurePath("file"), 5)


def test_evidence_aggregate_limit_is_enforced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(limits, "MAX_EVIDENCE_TOTAL_BYTES", 4)
    budget = limits.EvidenceBudget()
    budget.charge(PurePath("one"), 2)

    with pytest.raises(OSError, match="aggregate byte limit"):
        budget.charge(PurePath("two"), 3)
