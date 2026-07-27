from __future__ import annotations

import ast
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from typing import Final

import pytest

import core.run_manifest_paths as run_manifest_paths
from core.artifact_store import ArtifactStore
from core.run_manifest import (
    ManifestErrorKind,
    RunManifestError,
    RunManifestStore,
    RunStorageContext,
)
from tests.run_manifest_test_support import root_manifest, storage_context

_TASK2_PRODUCTION_FILES: Final[tuple[str, ...]] = (
    "run_manifest.py",
    "run_manifest_io.py",
    "run_manifest_lock.py",
    "run_manifest_models.py",
    "run_manifest_paths.py",
    "run_manifest_validation.py",
)


def _make_directory_link(link: Path, target: Path) -> None:
    if os.name == "nt":
        _ = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            check=True,
            capture_output=True,
        )
    else:
        link.symlink_to(target, target_is_directory=True)


def test_direct_context_construction_rejects_authority_inside_workspace(
    tmp_path: Path,
) -> None:
    # Given
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    valid = RunStorageContext.bind(tmp_path / "external", workspace)

    # When / Then
    with pytest.raises(RunManifestError) as boundary:
        _ = RunStorageContext(
            workspace / "e2e-reports",
            workspace,
            valid.workspace_digest,
        )
    assert boundary.value.kind is ManifestErrorKind.AUTHORITY_BOUNDARY


def test_junction_swap_after_inspection_never_seals_outside_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    context = storage_context(tmp_path)
    working = ArtifactStore(str(context.workspace_root), "parent-run-001")
    validated = Path(working.validated_dir)
    inside_file = validated / "origin.json"
    _ = inside_file.write_text('{"origin":"inside-safe"}', encoding="utf-8")
    outside = tmp_path / "outside-evidence"
    outside.mkdir()
    _ = (outside / inside_file.name).write_text(
        '{"origin":"outside-secret"}', encoding="utf-8"
    )
    writer = RunManifestStore.create(context, root_manifest(context))
    inspected = Event()
    release = Event()
    real_inspect = run_manifest_paths.inspect_real_tree

    def gate_after_inspection(
        root: Path, container: Path
    ) -> run_manifest_paths.RealTree:
        tree = real_inspect(root, container)
        if root == Path(working.artifact_dir):
            inspected.set()
            assert release.wait(timeout=3)
        return tree

    monkeypatch.setattr(run_manifest_paths, "inspect_real_tree", gate_after_inspection)

    # When
    linked = False
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(writer.seal_working_evidence, working)
            assert inspected.wait(timeout=3)
            inside_file.unlink()
            os.rmdir(validated)
            _make_directory_link(validated, outside)
            linked = True
            release.set()
            with pytest.raises(RunManifestError) as escaped:
                _ = future.result(timeout=3)
    finally:
        release.set()
        if linked:
            os.rmdir(validated)

    # Then
    assert escaped.value.kind is ManifestErrorKind.CONTAINMENT
    assert not writer.read().evidence_sealed
    assert not (
        context.authoritative_root / "parent-run-001" / "sealed-artifacts"
    ).exists()


def test_task2_sources_use_only_python38_syntax_and_runtime_apis() -> None:
    # Given
    core_dir = Path(__file__).parents[1] / "core"
    violations: list[str] = []

    # When
    for filename in _TASK2_PRODUCTION_FILES:
        source = (core_dir / filename).read_text(encoding="utf-8")
        _ = ast.parse(source, filename=filename, feature_version=(3, 8))
        compact = "".join(source.split())
        if "@dataclass(frozen=True,slots=True)" in compact:
            violations.append(f"{filename}: dataclass slots require Python 3.10")
        if ".is_junction(" in compact:
            violations.append(f"{filename}: Path.is_junction requires Python 3.12")

    # Then
    assert not violations, "\n".join(violations)
