from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

import core.run_manifest_io as run_manifest_io
from core.artifact_store import ArtifactStore
from core.run_manifest import (
    RUN_MANIFEST_FILENAME,
    EvidenceDigest,
    ManifestErrorKind,
    ResourceReference,
    RunId,
    RunManifest,
    RunManifestError,
    RunManifestStore,
    Sha256Digest,
)
from core.run_outcome import PhaseId
from tests.run_manifest_test_support import (
    OTHER_DIGEST,
    WORKFLOW_DIGEST,
    child_manifest,
    root_manifest,
    sealed_parent,
    storage_context,
)


def test_external_manifest_seals_working_evidence_and_creates_child_lineage(
    tmp_path: Path,
) -> None:
    # Given
    context, parent_reader, parent = sealed_parent(tmp_path)

    # When
    child_writer = RunManifestStore.create(
        context, child_manifest(parent), parent=parent_reader
    )
    child = child_writer.read()

    # Then
    parent_path = context.authoritative_root / parent.run_id / RUN_MANIFEST_FILENAME
    assert (context.authoritative_root / parent.run_id / "sealed-artifacts").is_dir()
    assert (context.workspace_root / ".sm-artifacts" / parent.run_id).is_dir()
    assert child.parent_run_id == parent.run_id
    assert child.lineage_root_run_id == parent.run_id
    assert child.terminal_anchor.phase_id == PhaseId("phase_5_validation")
    assert child.workflow_digest == WORKFLOW_DIGEST
    assert child.inherited_canonical[0].digest == Sha256Digest("d" * 64)
    assert child.shared_workspace.kind == "lineage_shared_mutable"
    assert child.resource_references[0].reference_id == "resource-child"
    assert child.parent_evidence_digests == parent.sealed_evidence
    manifest_text = parent_path.read_text(encoding="utf-8")
    assert str(context.authoritative_root) not in manifest_text
    assert str(context.workspace_root) not in manifest_text


def test_manifest_models_are_immutable_and_reject_path_traversal(
    tmp_path: Path,
) -> None:
    # Given / When / Then
    context = storage_context(tmp_path)
    manifest = root_manifest(context)
    with pytest.raises(ValidationError):
        _ = root_manifest(context, RunId("../escape"))
    with pytest.raises(ValidationError):
        _ = EvidenceDigest(
            relative_path="../parent/run-manifest.json",
            digest=WORKFLOW_DIGEST,
            size_bytes=1,
        )
    with pytest.raises(ValidationError):
        _ = ResourceReference(kind="environment", reference_id="../container")
    with pytest.raises(ValidationError):
        _ = RunManifest.model_validate_json(
            manifest.model_dump_json().replace('"revision":1', '"revision":"PASS"')
        )
    with pytest.raises(ValidationError):
        setattr(manifest.shared_workspace, "kind", "immutable")
    with pytest.raises(RunManifestError) as traversal:
        _ = RunManifestStore.open_readonly(context, RunId("../escape"), WORKFLOW_DIGEST)
    assert traversal.value.kind is ManifestErrorKind.MALFORMED


def test_duplicate_namespace_and_readonly_parent_writes_are_refused(
    tmp_path: Path,
) -> None:
    # Given
    context, parent_reader, parent = sealed_parent(tmp_path)

    # When / Then
    with pytest.raises(RunManifestError) as duplicate:
        _ = RunManifestStore.create(context, root_manifest(context))
    assert duplicate.value.kind is ManifestErrorKind.DUPLICATE_RUN
    changed = parent.model_copy(update={"revision": parent.revision + 1})
    with pytest.raises(RunManifestError) as readonly:
        _ = parent_reader.write(changed)
    assert readonly.value.kind is ManifestErrorKind.READ_ONLY
    writable = RunManifestStore.create(
        context, root_manifest(context, RunId("writable-parent"))
    )
    working = ArtifactStore(str(context.workspace_root), "writable-parent")
    sealed = writable.seal_working_evidence(working)
    assert writable.seal_working_evidence(working) == sealed
    child = RunManifestStore.create(context, child_manifest(sealed), parent=writable)
    assert child.read().parent_run_id == sealed.run_id
    skipped_revision = sealed.model_copy(update={"revision": sealed.revision + 2})
    with pytest.raises(RunManifestError) as version:
        _ = writable.write(skipped_revision)
    assert version.value.kind is ManifestErrorKind.READ_ONLY


@pytest.mark.parametrize(
    "mutation",
    [
        {"workflow_digest": OTHER_DIGEST},
        {"lineage_root_run_id": RunId("wrong-root")},
        {"parent_evidence_digests": ()},
    ],
)
def test_child_creation_rejects_parent_contract_mismatch(
    tmp_path: Path,
    mutation: dict[str, Sha256Digest | RunId | tuple[EvidenceDigest, ...]],
) -> None:
    # Given
    context, parent_reader, parent = sealed_parent(tmp_path)
    child = child_manifest(parent).model_copy(update=mutation)

    # When / Then
    with pytest.raises(RunManifestError) as mismatch:
        _ = RunManifestStore.create(context, child, parent=parent_reader)
    assert mismatch.value.kind is ManifestErrorKind.PARENT_MISMATCH


@pytest.mark.parametrize(
    ("replacement", "expected_kind"),
    [
        (
            ('"schema": "seam.run-manifest"', '"schema": "misleading-success"'),
            ManifestErrorKind.MALFORMED,
        ),
        (('"schema_version": 1', '"schema_version": 2'), ManifestErrorKind.MALFORMED),
        (
            (
                '"workflow_digest": "' + "a" * 64 + '"',
                '"workflow_digest": "' + "b" * 64 + '"',
            ),
            ManifestErrorKind.WORKFLOW_MISMATCH,
        ),
    ],
)
def test_open_rejects_schema_version_and_workflow_hash_mismatch(
    tmp_path: Path,
    replacement: tuple[str, str],
    expected_kind: ManifestErrorKind,
) -> None:
    # Given
    context = storage_context(tmp_path)
    _ = RunManifestStore.create(context, root_manifest(context))
    path = context.authoritative_root / "parent-run-001" / RUN_MANIFEST_FILENAME
    payload = path.read_text(encoding="utf-8").replace(*replacement)
    _ = path.write_text(payload, encoding="utf-8")

    # When / Then
    with pytest.raises(RunManifestError) as invalid:
        _ = RunManifestStore.open_readonly(
            context, RunId("parent-run-001"), WORKFLOW_DIGEST
        )
    assert invalid.value.kind is expected_kind


def test_interrupted_and_repeated_writes_preserve_previous_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    context = storage_context(tmp_path)
    writer = RunManifestStore.create(context, root_manifest(context))
    previous = writer.read()
    updated = previous.model_copy(
        update={
            "revision": 2,
            "resource_references": (
                ResourceReference(kind="environment", reference_id="resource-updated"),
            ),
        }
    )
    stale = context.authoritative_root / previous.run_id / ".run-manifest.stale.tmp"
    _ = stale.write_text('{"revision":"PASS"', encoding="utf-8")
    real_replace = run_manifest_io.atomic_replace

    def interrupt_replace(source: Path, destination: Path) -> None:
        raise OSError(f"interrupted {source.name} -> {destination.name}")

    monkeypatch.setattr(run_manifest_io, "atomic_replace", interrupt_replace)

    # When / Then
    for _ in range(2):
        with pytest.raises(RunManifestError) as interrupted:
            _ = writer.write(updated)
        assert interrupted.value.kind is ManifestErrorKind.WRITE_INTERRUPTED
        assert writer.read() == previous
    monkeypatch.setattr(run_manifest_io, "atomic_replace", real_replace)
    assert writer.write(updated) == updated
    assert stale.is_file()


def test_parent_digest_mutation_and_child_inventory_rewrite_are_refused(
    tmp_path: Path,
) -> None:
    # Given
    context, parent_reader, parent = sealed_parent(tmp_path)
    child_writer = RunManifestStore.create(
        context, child_manifest(parent), parent=parent_reader
    )
    parent_manifest_path = (
        context.authoritative_root / parent.run_id / RUN_MANIFEST_FILENAME
    )
    parent_manifest_bytes = parent_manifest_path.read_bytes()

    # When / Then
    child = child_writer.read()
    rewritten = child.model_copy(update={"revision": 2, "parent_evidence_digests": ()})
    with pytest.raises(RunManifestError) as immutable:
        _ = child_writer.write(rewritten)
    assert immutable.value.kind is ManifestErrorKind.IMMUTABLE_FIELD
    sealed_file = (
        context.authoritative_root
        / parent.run_id
        / "sealed-artifacts"
        / "validated"
        / "phase_3_entry_script_canonical.json"
    )
    _ = sealed_file.write_text('{"status":"misleading success"}', encoding="utf-8")
    with pytest.raises(RunManifestError) as drift:
        _ = parent_reader.read()
    assert drift.value.kind is ManifestErrorKind.EVIDENCE_MUTATION
    assert parent_manifest_path.read_bytes() == parent_manifest_bytes
