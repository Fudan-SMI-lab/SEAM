from __future__ import annotations

import json
import os
import subprocess
from hashlib import sha256
from pathlib import Path
from typing import Final

import pytest

from core.run_manifest import ManifestErrorKind, RunManifestError
from core.run_manifest_paths import inspect_real_tree
from harness.run.v3_snapshot import persist_python_snapshot

_SPARSE_MODEL_BYTES: Final = 65 * 1024 * 1024
_PYTHON_SOURCE: Final = "ANSWER = 42\n"


def _write_sparse_file(path: Path, size_bytes: int) -> None:
    with path.open("wb") as handle:
        _ = handle.seek(size_bytes - 1)
        _ = handle.write(b"\0")


def _make_project_tree(project: Path) -> tuple[Path, Path]:
    project.mkdir()
    model = project / "model.pt"
    _write_sparse_file(model, _SPARSE_MODEL_BYTES)
    module = project / "module.py"
    _ = module.write_text(_PYTHON_SOURCE, encoding="utf-8")
    return model, module


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


def test_default_inspection_enforces_file_byte_budget_on_model_file(
    tmp_path: Path,
) -> None:
    # Given
    project = tmp_path / "project"
    _make_project_tree(project)

    # When / Then
    with pytest.raises(OSError, match="evidence file byte limit exceeded"):
        _ = inspect_real_tree(project, tmp_path)


def test_budget_suffixes_scope_byte_charging_without_skipping_inspection(
    tmp_path: Path,
) -> None:
    # Given
    project = tmp_path / "project"
    _make_project_tree(project)

    # When
    tree = inspect_real_tree(project, tmp_path, budget_suffixes=frozenset({".py"}))

    # Then
    assert {identity.path.name for identity in tree.files} == {
        "model.pt",
        "module.py",
    }


def test_python_snapshot_ignores_oversized_model_bytes(tmp_path: Path) -> None:
    # Given
    project = tmp_path / "project"
    _, module = _make_project_tree(project)
    output = tmp_path / "snapshot.json"

    # When
    result = persist_python_snapshot(project, output)

    # Then
    assert result.file_count == 1
    payload: dict[str, dict[str, str]] = json.loads(
        output.read_text(encoding="utf-8")
    )
    assert list(payload) == ["module.py"]
    entry = payload["module.py"]
    assert set(entry) == {"sha256", "content"}
    assert entry["sha256"] == sha256(module.read_bytes()).hexdigest()


def test_budget_suffixes_still_reject_non_budgeted_reparse_entry(
    tmp_path: Path,
) -> None:
    # Given
    project = tmp_path / "project"
    _make_project_tree(project)
    outside = tmp_path / "outside"
    outside.mkdir()
    _directory_link(project / "linked.pt", outside)

    # When / Then
    with pytest.raises(RunManifestError) as failure:
        _ = inspect_real_tree(
            project, tmp_path, budget_suffixes=frozenset({".py"})
        )
    assert failure.value.kind is ManifestErrorKind.CONTAINMENT
