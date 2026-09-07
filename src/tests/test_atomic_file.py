from __future__ import annotations

from pathlib import Path

import pytest

from core import atomic_file


def test_atomic_write_failure_preserves_previous_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    destination = tmp_path / "artifact.json"
    _ = destination.write_bytes(b"previous")

    def interrupt(_source: Path, _destination: Path) -> None:
        raise OSError("replace interrupted")

    monkeypatch.setattr(atomic_file, "atomic_replace", interrupt)

    with pytest.raises(OSError, match="replace interrupted"):
        atomic_file.atomic_write_bytes(destination, b"replacement")

    assert destination.read_bytes() == b"previous"
    assert not list(tmp_path.glob(".artifact.json.*.tmp"))
