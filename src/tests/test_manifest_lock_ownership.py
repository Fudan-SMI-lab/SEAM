import shutil
from pathlib import Path

import pytest

from core import resource_manifest_lock, run_manifest_lock
from core.resource_manifest_lock import ResourceManifestLock
from core.resource_manifest_models import ResourceManifestError
from core.run_manifest_lock import RunFileLock
from core.run_manifest_models import RunManifestError


def test_run_lock_release_preserves_replacement_owner(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    lock = RunFileLock(run_dir)
    lock.__enter__()
    lock_path = run_dir / ".manifest-write.lock"
    shutil.rmtree(lock_path)
    lock_path.mkdir()
    _ = (lock_path / "owner").write_text("replacement", encoding="ascii")

    with pytest.raises(RunManifestError):
        _ = lock.__exit__(None, None, None)

    assert lock_path.is_dir()


def test_resource_lock_release_preserves_replacement_owner(tmp_path: Path) -> None:
    lock = ResourceManifestLock(tmp_path)
    lock.__enter__()
    lock_path = tmp_path / ".resource-manifest.lock"
    shutil.rmtree(lock_path)
    lock_path.mkdir()
    _ = (lock_path / "owner").write_text("replacement", encoding="ascii")

    with pytest.raises(ResourceManifestError):
        _ = lock.__exit__(None, None, None)

    assert lock_path.is_dir()


def test_run_lock_release_preserves_successor_swapped_after_owner_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    lock = RunFileLock(run_dir)
    lock.__enter__()
    lock_path = run_dir / ".manifest-write.lock"
    real_read = run_manifest_lock.read_verified_bytes

    def swap_after_read(path: Path, limit: int) -> bytes:
        content = real_read(path, limit)
        shutil.rmtree(lock_path)
        lock_path.mkdir()
        _ = (lock_path / "owner").write_bytes(b"successor")
        return content

    monkeypatch.setattr(run_manifest_lock, "read_verified_bytes", swap_after_read)

    with pytest.raises(RunManifestError):
        _ = lock.__exit__(None, None, None)
    assert (lock_path / "owner").read_bytes() == b"successor"


def test_resource_lock_release_preserves_successor_swapped_after_owner_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock = ResourceManifestLock(tmp_path)
    lock.__enter__()
    lock_path = tmp_path / ".resource-manifest.lock"
    real_read = resource_manifest_lock.read_verified_bytes

    def swap_after_read(path: Path, limit: int) -> bytes:
        content = real_read(path, limit)
        shutil.rmtree(lock_path)
        lock_path.mkdir()
        _ = (lock_path / "owner").write_bytes(b"successor")
        return content

    monkeypatch.setattr(resource_manifest_lock, "read_verified_bytes", swap_after_read)

    with pytest.raises(ResourceManifestError):
        _ = lock.__exit__(None, None, None)
    assert (lock_path / "owner").read_bytes() == b"successor"
