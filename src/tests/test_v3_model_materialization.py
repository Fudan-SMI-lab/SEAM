"""Failing-first tests for V3 model materialization (plan todo 3).

`symlink_large_files` must materialize model-extension and >50 MiB files as
regular byte-equal copies (shutil.copy2), never as symlinks, while preserving
the existing-target skip, the parent mkdir, and the EXCLUDED_SNAPSHOT_DIRS
filter. The helper keeps its historical name and return-count contract.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tests.e2e.e2e_test_v3 import EXCLUDED_SNAPSHOT_DIRS, symlink_large_files

MODEL_PAYLOAD = b"\x89PT-model-weights\r\n\x1a\n" * 64
OVERSIZED_BYTES = 50 * 1024 * 1024 + 1


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_sparse(path: Path, size: int) -> None:
    with path.open("wb") as handle:
        handle.seek(size - 1)
        handle.write(b"\0")


class TestModelExtensionMaterialization:
    def test_model_file_is_materialized_as_regular_byte_equal_copy(
        self, tmp_path: Path
    ) -> None:
        # Given a source tree with a small model-extension file
        source = tmp_path / "source"
        (source / "models").mkdir(parents=True)
        model = source / "models" / "yolo11n.pt"
        model.write_bytes(MODEL_PAYLOAD)
        project = tmp_path / "project"
        project.mkdir()

        # When the helper materializes large files into the project dir
        materialized = symlink_large_files(project, source)

        # Then the target is a regular byte-equal file, never a symlink
        target = project / "models" / "yolo11n.pt"
        assert materialized == 1
        assert target.is_file()
        assert not target.is_symlink()
        assert target.read_bytes() == MODEL_PAYLOAD


class TestOversizedFileMaterialization:
    def test_oversized_non_model_file_is_materialized_as_regular_byte_equal_copy(
        self, tmp_path: Path
    ) -> None:
        # Given a sparse >50 MiB file without a model extension
        source = tmp_path / "source"
        source.mkdir()
        big = source / "activations.dat"
        _write_sparse(big, OVERSIZED_BYTES)
        project = tmp_path / "project"
        project.mkdir()

        # When the helper materializes large files into the project dir
        materialized = symlink_large_files(project, source)

        # Then the target is a regular byte-equal file, never a symlink
        target = project / "activations.dat"
        assert materialized == 1
        assert target.is_file()
        assert not target.is_symlink()
        assert target.stat().st_size == OVERSIZED_BYTES
        assert _sha256(target) == _sha256(big)
        big.unlink()
        target.unlink()


class TestExistingTargetSkip:
    def test_existing_target_is_skipped_and_left_untouched(
        self, tmp_path: Path
    ) -> None:
        # Given a pre-existing target with sentinel content
        source = tmp_path / "source"
        source.mkdir()
        model = source / "yolo11n.pt"
        model.write_bytes(MODEL_PAYLOAD)
        project = tmp_path / "project"
        project.mkdir()
        target = project / "yolo11n.pt"
        sentinel = b"pre-existing target must stay untouched"
        target.write_bytes(sentinel)

        # When the helper runs over the source tree
        materialized = symlink_large_files(project, source)

        # Then nothing is materialized and the sentinel bytes are unchanged
        assert materialized == 0
        assert not target.is_symlink()
        assert target.read_bytes() == sentinel


class TestExcludedSnapshotDirSkip:
    def test_files_under_excluded_snapshot_dirs_are_skipped(
        self, tmp_path: Path
    ) -> None:
        # Given model files nested under every excluded snapshot directory
        source = tmp_path / "source"
        for excluded in sorted(EXCLUDED_SNAPSHOT_DIRS):
            folder = source / excluded
            folder.mkdir(parents=True)
            (folder / "weights.pt").write_bytes(MODEL_PAYLOAD)
        project = tmp_path / "project"
        project.mkdir()

        # When the helper runs over the source tree
        materialized = symlink_large_files(project, source)

        # Then nothing is materialized and the project dir stays empty
        assert materialized == 0
        assert list(project.rglob("*")) == []
