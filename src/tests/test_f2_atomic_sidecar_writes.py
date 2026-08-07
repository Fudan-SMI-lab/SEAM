from __future__ import annotations

import shutil
from pathlib import Path
from typing import NoReturn

import pytest

from core.run_outcome import TerminalOutcome
from harness import run
from harness.run import sidecars, v3_lifecycle
from harness.run.models import SidecarWriteError
from tests.e2e import e2e_observer
from tests.e2e.e2e_observer import TelemetryObserver
from tests.test_agent_io_logger import FakeSessionManager


def _interrupt_publication(path: Path, _content: bytes) -> NoReturn:
    assert not path.exists()
    raise OSError("forced atomic publication interruption")


def test_traceback_interruption_never_exposes_final_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        v3_lifecycle,
        "atomic_write_bytes",
        _interrupt_publication,
        raising=False,
    )
    context = run.EvidenceContext(
        output_dir=tmp_path,
        temp_dir=None,
        traceback_text="Traceback: forced",
        phase_results=(),
    )
    persister = run.EvidencePersister(context, run.TelemetrySidecars(), lambda _: None)

    with pytest.raises(OSError, match="forced atomic publication interruption"):
        _ = persister(TerminalOutcome.FAILED)

    assert not (tmp_path / "traceback.txt").exists()


def test_telemetry_interruption_never_exposes_final_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        e2e_observer,
        "atomic_write_bytes",
        _interrupt_publication,
        raising=False,
    )
    observer = TelemetryObserver(FakeSessionManager(), tmp_path)

    with pytest.raises(OSError, match="forced atomic publication interruption"):
        _ = observer.save_metrics()

    assert not (tmp_path / "telemetry.json").exists()


def test_artifact_parent_sync_failure_rolls_back_published_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    temp_dir = tmp_path / "temp"
    source = temp_dir / ".sm-artifacts"
    output_dir = tmp_path / "output"
    source.mkdir(parents=True)
    output_dir.mkdir()
    _ = (source / "artifact.txt").write_text("artifact", encoding="utf-8")

    def fail_sync(path: Path) -> NoReturn:
        assert path == output_dir / ".sm-artifacts"
        raise OSError("forced directory sync failure")

    monkeypatch.setattr(sidecars, "fsync_parent", fail_sync)

    with pytest.raises(OSError, match="forced directory sync failure"):
        _ = sidecars.copy_run_artifacts(temp_dir, output_dir)

    assert not (output_dir / ".sm-artifacts").exists()
    assert not list(output_dir.glob(".sm-artifacts.*.tmp"))


def test_artifact_sync_cleanup_preserves_published_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    temp_dir = tmp_path / "temp"
    source = temp_dir / ".sm-artifacts"
    output_dir = tmp_path / "output"
    destination = output_dir / ".sm-artifacts"
    source.mkdir(parents=True)
    output_dir.mkdir()
    _ = (source / "artifact.txt").write_text("artifact", encoding="utf-8")
    sentinel = destination / "replacement.txt"

    def replace_and_fail(_path: Path) -> NoReturn:
        shutil.rmtree(destination)
        destination.mkdir()
        _ = sentinel.write_text("preserve", encoding="utf-8")
        raise OSError("forced directory sync failure")

    monkeypatch.setattr(sidecars, "fsync_parent", replace_and_fail)

    with pytest.raises(SidecarWriteError, match="cleanup failed"):
        _ = sidecars.copy_run_artifacts(temp_dir, output_dir)

    assert sentinel.read_text(encoding="utf-8") == "preserve"


def test_artifact_publication_preserves_concurrent_empty_preplant(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    temp_dir = tmp_path / "temp"
    source = temp_dir / ".sm-artifacts"
    output_dir = tmp_path / "output"
    destination = output_dir / ".sm-artifacts"
    source.mkdir(parents=True)
    output_dir.mkdir()
    _ = (source / "artifact.txt").write_text("artifact", encoding="utf-8")
    original_copy = sidecars.artifact_tree_copy
    original_rename = Path.rename

    def copy_then_preplant(source_path: Path, container: Path, staging: Path) -> None:
        original_copy(source_path, container, staging)
        destination.mkdir()

    def emulate_linux_replace(path: Path, target: Path) -> Path:
        if target == destination:
            destination.rmdir()
        return original_rename(path, target)

    monkeypatch.setattr(sidecars, "artifact_tree_copy", copy_then_preplant)
    monkeypatch.setattr(Path, "rename", emulate_linux_replace)

    with pytest.raises(FileExistsError):
        _ = sidecars.copy_run_artifacts(temp_dir, output_dir)
    assert destination.is_dir()
