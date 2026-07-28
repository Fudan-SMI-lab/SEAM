from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple, cast

import pytest

import core.continuation_evidence_io as evidence_io
import core.run_manifest_paths as run_manifest_paths

_TRANSIENT_WINDOWS_DIRECTORY_ATTRIBUTE = 0x10000000


class _EvidenceIdentity(NamedTuple):
    device: int
    inode: int
    mode: int
    attributes: int
    size: int
    modified_ns: int


def test_non_reparse_directory_attribute_churn_preserves_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "empty-evidence"
    root.mkdir()
    real_identity = cast(
        Callable[[Path], run_manifest_paths.PathIdentity],
        getattr(run_manifest_paths, "_path_identity"),
    )
    root_observations = 0

    def changing_identity(path: Path) -> run_manifest_paths.PathIdentity:
        nonlocal root_observations
        identity = real_identity(path)
        if path == root:
            root_observations += 1
            if root_observations in (2, 3):
                return identity._replace(
                    attributes=(
                        identity.attributes | _TRANSIENT_WINDOWS_DIRECTORY_ATTRIBUTE
                    )
                )
        return identity

    monkeypatch.setattr(run_manifest_paths, "_path_identity", changing_identity)

    assert run_manifest_paths.digest_inventory(root, tmp_path) == ()
    assert root_observations >= 4


def test_project_snapshot_accepts_non_reparse_directory_attribute_churn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "empty-project"
    workspace.mkdir()
    real_identity = cast(
        Callable[[os.stat_result], _EvidenceIdentity],
        getattr(evidence_io, "_identity"),
    )
    observations = 0

    def changing_identity(
        metadata: os.stat_result,
    ) -> tuple[int, int, int, int, int, int]:
        nonlocal observations
        identity = real_identity(metadata)
        observations += 1
        if observations in (1, 2):
            return (
                identity[0],
                identity[1],
                identity[2],
                identity[3] | _TRANSIENT_WINDOWS_DIRECTORY_ATTRIBUTE,
                identity[4],
                identity[5],
            )
        return identity

    monkeypatch.setattr(evidence_io, "_identity", changing_identity)

    snapshot = evidence_io.snapshot_project_baseline(workspace)

    assert snapshot.files == ()
    assert snapshot.links == ()
    assert observations >= 3
