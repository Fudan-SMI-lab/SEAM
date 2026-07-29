from __future__ import annotations

from pathlib import Path

import pytest

from core import continuation_resource, continuation_workflow_snapshot
from core import resource_manifest_paths


def test_snapshot_rejects_non_string_workflow_name() -> None:
    content = b"""\
name: [not, a, string]
version: 1
phases:
  - {id: phase_0_detect, type: builtin, operation: noop}
terminals: [complete]
"""

    with pytest.raises(continuation_workflow_snapshot.WorkflowSnapshotError):
        _ = continuation_workflow_snapshot.load_workflow_snapshot(
            content, "malformed.yaml"
        )


def test_snapshot_delegates_to_verified_two_megabyte_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "workflow.yaml"
    _ = path.write_bytes(b"workflow")
    observed: list[tuple[Path, int]] = []

    def verified(target: Path, limit: int) -> bytes:
        observed.append((target, limit))
        return b"verified"

    monkeypatch.setattr(
        continuation_workflow_snapshot, "read_verified_bytes", verified, raising=False
    )

    assert continuation_workflow_snapshot.read_workflow_snapshot(path) == b"verified"
    assert observed == [(path, 2 * 1024 * 1024)]


def test_existing_resource_capability_uses_verified_bounded_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report_dir = tmp_path / "runs" / "run-1" / "report"
    report_dir.mkdir(parents=True)
    capability = continuation_resource.resource_capability_path(report_dir)
    capability.parent.mkdir(parents=True)
    _ = capability.write_bytes(b"x" * 32)
    observed: list[tuple[Path, int]] = []

    def verified(target: Path, limit: int) -> bytes:
        observed.append((target, limit))
        return b"x" * 32

    monkeypatch.setattr(
        continuation_resource, "read_verified_bytes", verified, raising=False
    )

    assert continuation_resource.read_existing_resource_secret(report_dir) == b"x" * 32
    assert observed == [(capability.resolve(), 32)]


def test_capture_capability_uses_verified_bounded_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "capture.key"
    _ = path.write_bytes(b"x" * 32)
    observed: list[tuple[Path, int]] = []

    def verified(target: Path, limit: int) -> bytes:
        observed.append((target, limit))
        return b"x" * 32

    monkeypatch.setattr(
        resource_manifest_paths, "read_verified_bytes", verified, raising=False
    )

    assert resource_manifest_paths.read_capture_capability(path) == b"x" * 32
    assert observed == [(path, 32)]
