import os
from pathlib import Path

import pytest

import core.continuation_lock_identity as lock_identity
from core.continuation_lock_identity import (
    BoundedReadError,
    BoundedReadErrorKind,
    read_verified_bytes,
)
from core.resource_manifest_io import read_resource_manifest
from core.resource_manifest_models import ResourceManifest, ResourceManifestError
from core.run_manifest_io import read_manifest
from core.run_manifest_models import RunManifest, RunManifestError


def test_run_manifest_oversize_is_rejected_before_parsing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "run-manifest.json"
    _ = path.write_bytes(b" " * (1024 * 1024 + 1))

    def parser_must_not_run(_payload: bytes) -> RunManifest:
        pytest.fail("oversized run manifest reached the parser")

    monkeypatch.setattr(RunManifest, "model_validate_json", parser_must_not_run)

    with pytest.raises(RunManifestError):
        _ = read_manifest(path)


def test_resource_manifest_oversize_is_rejected_before_parsing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "resource-manifest.json"
    _ = path.write_bytes(b" " * (1024 * 1024 + 1))

    def parser_must_not_run(_payload: bytes) -> ResourceManifest:
        pytest.fail("oversized resource manifest reached the parser")

    monkeypatch.setattr(ResourceManifest, "model_validate_json", parser_must_not_run)

    with pytest.raises(ResourceManifestError):
        _ = read_resource_manifest(path)


def test_verified_read_rejects_same_inode_size_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "receipt.json"
    _ = path.write_bytes(b"receipt")
    real_fstat = os.fstat
    calls = 0

    def changed_final_stat(descriptor: int) -> os.stat_result:
        nonlocal calls
        calls += 1
        metadata = real_fstat(descriptor)
        if calls == 2:
            values = list(metadata)
            values[6] += 1
            return os.stat_result(values)
        return metadata

    monkeypatch.setattr(lock_identity.os, "fstat", changed_final_stat)

    with pytest.raises(BoundedReadError) as captured:
        _ = read_verified_bytes(path, 1024)

    assert captured.value.kind is BoundedReadErrorKind.CHANGED
