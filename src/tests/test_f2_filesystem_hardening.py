from __future__ import annotations

import os
import subprocess
from builtins import open as builtin_open
from pathlib import Path
from typing import BinaryIO

import pytest

from harness.run.models import SidecarWriteError
from harness.run.artifact_paths import (
    ArtifactPathKind,
    SidecarValidationError,
    _fingerprint,
)
from harness.run.sidecars import copy_run_artifacts
from harness.run.v3_lifecycle import persist_python_snapshot


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
            pytest.skip(created.stderr or created.stdout)
        return
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlink unavailable: {exc}")


def test_artifact_copy_rejects_nested_link_before_external_bytes(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    artifacts = source / ".sm-artifacts"
    outside = tmp_path / "outside"
    output = tmp_path / "output"
    artifacts.mkdir(parents=True)
    outside.mkdir()
    output.mkdir()
    sentinel = "OUTSIDE_SENTINEL"
    _ = (outside / "secret.txt").write_text(sentinel, encoding="utf-8")
    _directory_link(artifacts / "linked", outside)

    with pytest.raises(SidecarWriteError):
        _ = copy_run_artifacts(source, output)

    assert not (output / ".sm-artifacts").exists()
    assert not list(output.glob(".sm-artifacts.*.tmp"))


def test_python_snapshot_rejects_linked_directory_before_publication(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    output = tmp_path / "snapshot.json"
    project.mkdir()
    outside.mkdir()
    _ = (outside / "outside.py").write_text(
        "OUTSIDE_SENTINEL = True\n", encoding="utf-8"
    )
    _directory_link(project / "linked", outside)

    with pytest.raises(SidecarWriteError):
        _ = persist_python_snapshot(project, output)

    assert not output.exists()


def test_file_fingerprint_rejects_same_size_identity_swap_before_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claim = tmp_path / "claim.json"
    outside = tmp_path / "outside.json"
    _ = claim.write_text("owned", encoding="utf-8")
    _ = outside.write_text("other", encoding="utf-8")
    swapped = False

    def swap_before_open(
        path: Path,
        mode: str = "r",
        buffering: int = -1,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ) -> BinaryIO:
        nonlocal swapped
        assert mode == "rb"
        assert buffering == -1
        assert encoding is None
        assert errors is None
        assert newline is None
        if path == claim and not swapped:
            swapped = True
            _ = path.unlink()
            _ = outside.replace(path)
        return builtin_open(path, "rb")

    monkeypatch.setattr(Path, "open", swap_before_open)

    with pytest.raises(SidecarValidationError):
        _ = _fingerprint(claim, ArtifactPathKind.FILE)
