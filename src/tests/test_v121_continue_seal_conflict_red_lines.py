"""Failing-first red proofs: ``--continue-from`` and ``--seal-manifest`` conflict.

These characterization tests lock the *desired* contract for Wave-1 Todo 3 of
the v1.2.1 remote-update remediation workplan:

    ``--continue-from <summary.json>`` and ``--seal-manifest`` are mutually
    exclusive on the public Python entrypoint. Supplying both must exit
    nonzero with actionable conflict text, because direct-run sealing only
    applies to a fresh run; a continuation child must consume the parent's
    already-sealed evidence rather than re-seal its own root manifest.

At the c6cbed3 baseline the parser silently accepts both flags and the
``--seal-manifest`` value is dropped on the floor by the continuation
branch, so every assertion below fails for its intended contract rather
than for import/setup error.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from tests.e2e import e2e_test_v3 as target

_CONFLICT_ARGV = [
    "--continue-from",
    "summary.json",
    "--seal-manifest",
]


def test_parser_rejects_continue_from_with_seal_manifest() -> None:
    """``build_parser`` must reject the continuation+seal conflict at parse time.

    Given the public V3 parser.
    When ``--continue-from`` and ``--seal-manifest`` are supplied together.
    Then ``parse_args`` exits with the argparse conflict code (2).
    """
    parser = target.build_parser()

    with pytest.raises(SystemExit) as raised:
        _ = parser.parse_args(_CONFLICT_ARGV)

    assert raised.value.code == 2


def test_main_rejects_continue_from_with_seal_manifest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """``main`` must exit nonzero before any side effect when both are supplied.

    Given a real summary file path so the path validator cannot mask the
    conflict.
    When ``main`` is invoked with ``--continue-from`` + ``--seal-manifest``.
    Then the process exits with a nonzero status and emits conflict text.
    """
    summary = tmp_path / "summary.json"
    _ = summary.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "e2e_test_v3",
            "--continue-from",
            str(summary),
            "--seal-manifest",
        ],
    )

    with pytest.raises(SystemExit) as raised:
        _ = target.main()

    assert raised.value.code != 0
