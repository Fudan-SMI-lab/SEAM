from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

from core.artifact_store import ArtifactStore

pytestmark = pytest.mark.integration


def _directory_link(link: Path, target: Path) -> None:
    if os.name == "nt":
        created = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            encoding="mbcs",
            errors="replace",
            check=False,
        )
        if created.returncode != 0:
            pytest.skip(f"junction unavailable: {created.stderr or created.stdout}")
        return
    link.symlink_to(target, target_is_directory=True)


def test_artifact_store_persists_outputs_journal_and_checkpoint(tmp_path: Path) -> None:
    store = ArtifactStore(str(tmp_path), "run-a")
    phase_output = {"status": "ok", "count": 2}
    checkpoint = {"current_phase": "phase_3", "attempt": 2}

    raw_path = store.save_phase_output("phase_0_env_detect", phase_output, attempt=1)
    canonical_path = store.mark_validated("phase_0_env_detect", phase_output)
    journal_path = store.write_journal(
        {"phase_id": "phase_0_env_detect", "status": "succeeded"}
    )
    checkpoint_path = store.save_checkpoint(checkpoint)

    assert Path(store.raw_dir).is_dir()
    assert Path(store.validated_dir).is_dir()
    assert Path(raw_path).is_file()
    assert Path(canonical_path).is_file()
    assert Path(journal_path).is_file()
    assert Path(checkpoint_path).is_file()
    assert store.load_phase_output("phase_0_env_detect") == phase_output
    assert store.get_journal() == [
        {"phase_id": "phase_0_env_detect", "status": "succeeded"}
    ]
    assert store.load_checkpoint() == checkpoint


def test_artifact_store_get_latest_run_id_uses_real_directories(tmp_path: Path) -> None:
    first_store = ArtifactStore(str(tmp_path), "run-old")
    second_store = ArtifactStore(str(tmp_path), "run-new")

    old_time = time.time() - 10
    os.utime(first_store.artifact_dir, (old_time, old_time))
    os.utime(second_store.artifact_dir, None)

    assert ArtifactStore.get_latest_run_id(str(tmp_path)) == "run-new"


def test_artifact_store_exclusive_creation_rejects_reused_run_id(
    tmp_path: Path,
) -> None:
    first = ArtifactStore.create_exclusive(str(tmp_path), "child-run-001")
    sentinel = Path(first.raw_dir) / "sentinel.txt"
    _ = sentinel.write_bytes(b"first child")

    with pytest.raises(FileExistsError):
        _ = ArtifactStore.create_exclusive(str(tmp_path), "child-run-001")

    assert sentinel.read_bytes() == b"first child"


def test_artifact_store_exclusive_creation_rejects_linked_artifact_root(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    _ = sentinel.write_bytes(b"outside")
    _directory_link(tmp_path / ".sm-artifacts", outside)

    with pytest.raises(OSError):
        _ = ArtifactStore.create_exclusive(str(tmp_path), "child-run-001")

    assert sentinel.read_bytes() == b"outside"
    assert not (outside / "child-run-001").exists()
