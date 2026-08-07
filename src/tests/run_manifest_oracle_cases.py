from __future__ import annotations

import os
import subprocess
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError
from pathlib import Path
from threading import Event, Lock
from uuid import UUID

import pytest

import core.run_manifest_io as run_manifest_io
from core.artifact_store import ArtifactStore
from core.run_manifest import (
    ManifestErrorKind,
    ResourceReference,
    RunId,
    RunManifest,
    RunManifestError,
    RunManifestStore,
    RunStorageContext,
)
from core.run_outcome import PhaseId, TerminalAnchor
from tests.run_manifest_test_support import (
    WORKFLOW_DIGEST,
    child_manifest,
    root_manifest,
    storage_context,
)


def _future_outcome(future: Future[RunManifest]) -> str:
    try:
        _ = future.result(timeout=3)
    except RunManifestError as error:
        return error.kind.value
    return "ok"


def _make_directory_link(link: Path, target: Path) -> None:
    if os.name == "nt":
        _ = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            check=True,
            capture_output=True,
        )
    else:
        link.symlink_to(target, target_is_directory=True)


def _remove_directory_link(link: Path) -> None:
    if os.name == "nt":
        os.rmdir(link)
    else:
        link.unlink()


def test_concurrent_same_revision_writers_cannot_both_succeed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    context = storage_context(tmp_path)
    writer = RunManifestStore.create(context, root_manifest(context))
    current = writer.read()
    left = current.model_copy(update={"revision": 2, "resource_references": ()})
    right = current.model_copy(
        update={
            "revision": 2,
            "resource_references": (
                ResourceReference(kind="environment", reference_id="right"),
            ),
        }
    )
    entered = Event()
    release = Event()
    call_lock = Lock()
    first_call = True
    real_replace = run_manifest_io.atomic_replace

    def gate_first_replace(source: Path, destination: Path) -> None:
        nonlocal first_call
        with call_lock:
            should_wait = first_call
            first_call = False
        if should_wait:
            entered.set()
            assert release.wait(timeout=3)
        real_replace(source, destination)

    monkeypatch.setattr(run_manifest_io, "atomic_replace", gate_first_replace)

    # When
    with ThreadPoolExecutor(max_workers=2) as pool:
        left_future = pool.submit(writer.write, left)
        assert entered.wait(timeout=3)
        right_future = pool.submit(writer.write, right)
        try:
            _ = right_future.result(timeout=0.25)
        except (TimeoutError, RunManifestError) as expected:
            _ = expected
        finally:
            release.set()
        outcomes = (_future_outcome(left_future), _future_outcome(right_future))

    # Then
    assert outcomes.count("ok") == 1, f"same revision outcomes={outcomes}"
    assert writer.read().revision == 2


def test_seal_and_write_race_cannot_finish_with_successful_unsealed_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    context = storage_context(tmp_path)
    writer = RunManifestStore.create(context, root_manifest(context))
    working = ArtifactStore(str(context.workspace_root), "parent-run-001")
    current = writer.read()
    ordinary = current.model_copy(update={"revision": 2, "resource_references": ()})
    entered = Event()
    release = Event()
    real_replace = run_manifest_io.atomic_replace

    def hold_ordinary_replace(source: Path, destination: Path) -> None:
        if b'"evidence_sealed": false' in source.read_bytes():
            entered.set()
            assert release.wait(timeout=3)
        real_replace(source, destination)

    monkeypatch.setattr(run_manifest_io, "atomic_replace", hold_ordinary_replace)

    # When
    with ThreadPoolExecutor(max_workers=2) as pool:
        write_future = pool.submit(writer.write, ordinary)
        assert entered.wait(timeout=3)
        seal_future = pool.submit(writer.seal_working_evidence, working)
        try:
            sealed = seal_future.result(timeout=0.25)
        except TimeoutError:
            release.set()
            sealed = seal_future.result(timeout=3)
        else:
            release.set()
        _ = write_future.result(timeout=3)

    # Then
    assert sealed.evidence_sealed
    assert writer.read().evidence_sealed


def test_retained_writer_cannot_mutate_sealed_parent(tmp_path: Path) -> None:
    # Given
    context = storage_context(tmp_path)
    writer = RunManifestStore.create(context, root_manifest(context))
    working = ArtifactStore(str(context.workspace_root), "parent-run-001")
    sealed = writer.seal_working_evidence(working)
    changed = sealed.model_copy(
        update={
            "revision": sealed.revision + 1,
            "terminal_anchor": TerminalAnchor(phase_id=PhaseId("phase_6_report")),
            "resource_references": (),
        }
    )

    # When / Then
    with pytest.raises(RunManifestError):
        _ = writer.write(changed)
    assert writer.read() == sealed


def test_invalid_model_copy_cannot_replace_readable_manifest(tmp_path: Path) -> None:
    # Given
    context = storage_context(tmp_path)
    writer = RunManifestStore.create(context, root_manifest(context))
    prior = writer.read()
    invalid = prior.model_copy(update={"revision": 2, "schema_version": 2})

    # When / Then
    with pytest.raises(RunManifestError) as malformed:
        _ = writer.write(invalid)
    assert malformed.value.kind is ManifestErrorKind.MALFORMED
    assert writer.read() == prior


def test_initial_manifest_cannot_claim_seal_without_real_directory(
    tmp_path: Path,
) -> None:
    # Given
    context = storage_context(tmp_path)
    false_seal = root_manifest(context).model_copy(update={"evidence_sealed": True})

    # When / Then
    with pytest.raises(RunManifestError):
        _ = RunManifestStore.create(context, false_seal)
    assert not (context.authoritative_root / "parent-run-001").exists()


def test_authority_and_child_workspace_must_be_physically_bound(tmp_path: Path) -> None:
    # Given
    workspace_a = tmp_path / "workspace-a"
    local_reports = workspace_a / "e2e-reports"
    local_working = ArtifactStore(str(workspace_a), "parent-run-001")

    # When / Then
    with pytest.raises(RunManifestError):
        _ = RunStorageContext.bind(local_reports, workspace_a)
    reports = tmp_path / "external-reports"
    parent_context = RunStorageContext.bind(reports, workspace_a)
    parent_writer = RunManifestStore.create(
        parent_context, root_manifest(parent_context)
    )
    parent = parent_writer.seal_working_evidence(local_working)
    parent_reader = RunManifestStore.open_readonly(
        parent_context, parent.run_id, WORKFLOW_DIGEST
    )
    foreign = ArtifactStore(str(tmp_path / "workspace-b"), "child-run-001")
    child_context = RunStorageContext.bind(reports, Path(foreign.base_dir))
    with pytest.raises(RunManifestError):
        _ = RunManifestStore.create(
            child_context, child_manifest(parent), parent=parent_reader
        )


def test_readonly_namespace_rejects_windows_junction_escape(tmp_path: Path) -> None:
    # Given
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside_context = RunStorageContext.bind(tmp_path / "outside", workspace)
    outside_writer = RunManifestStore.create(
        outside_context, root_manifest(outside_context)
    )
    assert outside_writer.read().run_id == RunId("parent-run-001")
    authority = tmp_path / "authority"
    authority.mkdir()
    authority_context = RunStorageContext.bind(authority, workspace)
    junction = authority / "parent-run-001"
    _make_directory_link(
        junction, outside_context.authoritative_root / "parent-run-001"
    )

    # When / Then
    try:
        with pytest.raises(RunManifestError):
            _ = RunManifestStore.open_readonly(
                authority_context, RunId("parent-run-001"), WORKFLOW_DIGEST
            )
    finally:
        _remove_directory_link(junction)


def test_working_evidence_rejects_windows_junction_escape(tmp_path: Path) -> None:
    # Given
    context = storage_context(tmp_path)
    working = ArtifactStore(str(context.workspace_root), "parent-run-001")
    outside = tmp_path / "outside-evidence"
    outside.mkdir()
    _ = (outside / "escaped.json").write_text('{"status":"success"}', encoding="utf-8")
    validated = Path(working.validated_dir)
    os.rmdir(validated)
    _make_directory_link(validated, outside)
    writer = RunManifestStore.create(context, root_manifest(context))

    # When / Then
    try:
        with pytest.raises(RunManifestError):
            _ = writer.seal_working_evidence(working)
    finally:
        _remove_directory_link(validated)


@pytest.mark.parametrize("stage", ["before_temp", "after_temp"])
def test_interrupted_initial_creation_rolls_back_owned_namespace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
) -> None:
    # Given
    context = storage_context(tmp_path)

    def fail_before_temp() -> UUID:
        raise OSError("before temp creation")

    def fail_after_temp(source: Path, destination: Path) -> None:
        raise OSError(f"after temp creation: {source} -> {destination}")

    if stage == "before_temp":
        monkeypatch.setattr(run_manifest_io, "uuid4", fail_before_temp)
    else:
        monkeypatch.setattr(run_manifest_io, "atomic_replace", fail_after_temp)

    # When / Then
    with pytest.raises(RunManifestError):
        _ = RunManifestStore.create(context, root_manifest(context))
    assert not (context.authoritative_root / "parent-run-001").exists()
    monkeypatch.undo()
    assert RunManifestStore.create(context, root_manifest(context)).read().revision == 1
