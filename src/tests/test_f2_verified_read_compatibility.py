from __future__ import annotations

import os
from pathlib import Path

import pytest

from core.continuation_lock_identity import BoundedReadError, read_verified_bytes


def test_verified_read_rejects_same_inode_same_size_final_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "bounded.bin"
    _ = path.write_bytes(b"safe")
    original_lstat = Path.lstat
    calls = 0

    def mutate_on_final_lstat(target: Path) -> os.stat_result:
        nonlocal calls
        if target == path:
            calls += 1
            if calls == 2:
                previous = original_lstat(path)
                _ = path.write_bytes(b"evil")
                os.utime(
                    path,
                    ns=(previous.st_atime_ns, previous.st_mtime_ns + 1_000_000),
                )
        return original_lstat(target)

    monkeypatch.setattr(Path, "lstat", mutate_on_final_lstat)

    with pytest.raises(BoundedReadError):
        _ = read_verified_bytes(path, 4)


def test_verified_read_rejects_mutation_between_lstat_and_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "bounded.bin"
    _ = path.write_bytes(b"safe")
    original_open = os.open
    mutated = False

    def mutate_before_open(target: Path, flags: int) -> int:
        nonlocal mutated
        if target == path and not mutated:
            mutated = True
            previous = path.stat()
            _ = path.write_bytes(b"evil")
            os.utime(
                path,
                ns=(previous.st_atime_ns, previous.st_mtime_ns + 1_000_000),
            )
        return original_open(target, flags)

    monkeypatch.setattr(os, "open", mutate_before_open)

    with pytest.raises(BoundedReadError):
        _ = read_verified_bytes(path, 4)


def test_run_manifest_paths_preserves_inventory_compatibility_export(
    tmp_path: Path,
) -> None:
    from core import run_manifest_paths

    root = tmp_path / "evidence"
    root.mkdir()

    assert run_manifest_paths.digest_inventory(root, tmp_path) == ()
