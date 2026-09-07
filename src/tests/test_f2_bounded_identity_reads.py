from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path
from typing import NoReturn

import pytest

from core import (
    continuation_evidence_authority,
    continuation_evidence_io,
    run_manifest,
    run_manifest_rollback,
)
from core.continuation import claim_terminal_parent
from core.continuation_evidence import prepare_child_evidence, seal_child_evidence
from core.continuation_evidence_models import (
    ContinuationEvidenceError,
    ContinuationEvidenceRoot,
    PreparedChildEvidence,
)
from core.continuation_lock_identity import read_verified_bytes
from core.evidence_limits import MAX_EVIDENCE_FILE_BYTES
from core.resource_retention import ContainerRetention, resolve_v3_container_retention
from core.resource_retention_manifest import (
    RetentionManifestRequest,
    create_retention_manifest,
)
from core.run_manifest_io import ManifestPayload
from core.run_manifest_models import (
    EvidenceDigest,
    ManifestErrorKind,
    RunManifest,
    RunManifestError,
    Sha256Digest,
)
from core.types import WorkflowDefinition
from tests.continuation_evidence_security_cases import continuation_request
from tests.run_manifest_test_support import root_manifest, storage_context
from tests.terminal_run_continuation_test_support import create_parent_run

_WORKFLOW_LIMIT = 2 * 1024 * 1024


def _sealed_child(tmp_path: Path) -> tuple[PreparedChildEvidence, RunManifest]:
    parent = create_parent_run(tmp_path)
    request = continuation_request(parent.summary_path)
    with claim_terminal_parent(request.continuation) as resolved:
        prepared = prepare_child_evidence(resolved, request)
        sealed = seal_child_evidence(prepared)
    return prepared, sealed


def _root_path(prepared: PreparedChildEvidence) -> Path:
    return (
        prepared.namespace.report_dir
        / "sealed-artifacts"
        / "validated"
        / "continuation_evidence_root.json"
    )


def _retention_request(tmp_path: Path, workflow_path: Path) -> RetentionManifestRequest:
    workspace = tmp_path / "workspace"
    report_dir = tmp_path / "reports" / "bounded-run"
    workspace.mkdir(exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)
    workflow = WorkflowDefinition(
        name="bounded-retention", version="1.0", phases=[], terminals=["complete"]
    )
    return RetentionManifestRequest(
        report_dir=report_dir,
        run_id="bounded-run",
        requested_workflow=workflow_path,
        effective_workflow=workflow_path,
        workspace=workspace,
        requested_backend="local",
        endpoint="http://127.0.0.1:4096",
        server_process_id=None,
        policy=resolve_v3_container_retention(
            workflow, ContainerRetention.RETAIN, "bounded-run"
        ),
        backend=None,
    )


def test_external_root_rejects_oversize_before_json_parse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared, sealed = _sealed_child(tmp_path)
    root = _root_path(prepared)
    _ = root.write_bytes(b" " * (MAX_EVIDENCE_FILE_BYTES + 1))

    def parser_must_not_run(_payload: bytes) -> ContinuationEvidenceRoot:
        pytest.fail("oversized continuation root reached JSON parsing")

    monkeypatch.setattr(
        ContinuationEvidenceRoot, "model_validate_json", parser_must_not_run
    )

    with pytest.raises(ContinuationEvidenceError):
        continuation_evidence_authority.verify_external_evidence_root(
            prepared.namespace.report_dir, sealed
        )


def test_external_root_rejects_path_swap_during_verified_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared, sealed = _sealed_child(tmp_path)
    root = _root_path(prepared)
    replacement = root.with_suffix(".replacement")
    _ = replacement.write_bytes(root.read_bytes())
    original_lstat = Path.lstat
    calls = 0

    def swap_on_final_lstat(path: Path) -> os.stat_result:
        nonlocal calls
        if path == root:
            calls += 1
            if calls == 2:
                _ = root.replace(root.with_suffix(".original"))
                _ = replacement.replace(root)
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", swap_on_final_lstat)

    with pytest.raises(ContinuationEvidenceError):
        continuation_evidence_authority.verify_external_evidence_root(
            prepared.namespace.report_dir, sealed
        )


def test_record_verification_rejects_oversize_without_whole_file_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "record.json"
    _ = path.write_bytes(b"x" * (MAX_EVIDENCE_FILE_BYTES + 1))
    receipt = EvidenceDigest(
        relative_path=path.name,
        digest=Sha256Digest("a" * 64),
        size_bytes=MAX_EVIDENCE_FILE_BYTES + 1,
    )
    original_read = Path.read_bytes

    def reject_whole_file_read(target: Path) -> bytes:
        if target == path:
            pytest.fail("oversized evidence used Path.read_bytes")
        return original_read(target)

    monkeypatch.setattr(Path, "read_bytes", reject_whole_file_read)

    assert continuation_evidence_io.verify_record(path, receipt) is False


def test_record_verification_rejects_path_swap_during_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "record.json"
    content = b'{"complete":true}'
    _ = path.write_bytes(content)
    replacement = tmp_path / "replacement.json"
    _ = replacement.write_bytes(content)
    receipt = EvidenceDigest(
        relative_path=path.name,
        digest=Sha256Digest(hashlib.sha256(content).hexdigest()),
        size_bytes=len(content),
    )
    original_lstat = Path.lstat
    calls = 0

    def swap_on_final_lstat(target: Path) -> os.stat_result:
        nonlocal calls
        if target == path:
            calls += 1
            if calls == 2:
                _ = path.replace(tmp_path / "original.json")
                _ = replacement.replace(path)
        return original_lstat(target)

    monkeypatch.setattr(Path, "lstat", swap_on_final_lstat)

    assert continuation_evidence_io.verify_record(path, receipt) is False


def test_retention_workflow_rejects_oversize_before_hash_allocation(
    tmp_path: Path,
) -> None:
    workflow_path = tmp_path / "workflow.yaml"
    _ = workflow_path.write_bytes(b"x" * (_WORKFLOW_LIMIT + 1))

    with pytest.raises(OSError):
        _ = create_retention_manifest(_retention_request(tmp_path, workflow_path))


def test_retention_workflow_rejects_path_swap_during_hash_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow_path = tmp_path / "workflow.yaml"
    content = b"name: bounded-retention\n"
    _ = workflow_path.write_bytes(content)
    replacement = tmp_path / "workflow-replacement.yaml"
    _ = replacement.write_bytes(content)
    original_lstat = Path.lstat
    calls = 0

    def swap_on_final_lstat(path: Path) -> os.stat_result:
        nonlocal calls
        if path == workflow_path:
            calls += 1
            if calls == 2:
                _ = workflow_path.replace(tmp_path / "workflow-original.yaml")
                _ = replacement.replace(workflow_path)
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", swap_on_final_lstat)

    with pytest.raises(OSError):
        _ = create_retention_manifest(_retention_request(tmp_path, workflow_path))


def test_allocation_owner_rejects_oversize_without_text_allocation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = storage_context(tmp_path)
    owner_path = context.authoritative_root / "parent-run-001" / ".allocation-owner"

    def fail_after_oversize(_path: Path, _payload: ManifestPayload) -> NoReturn:
        _ = owner_path.write_bytes(b"x" * 129)
        raise RunManifestError(ManifestErrorKind.WRITE_INTERRUPTED, "forced")

    def text_read_must_not_run(
        path: Path,
        encoding: str | None = None,
        errors: str | None = None,
    ) -> str:
        _ = encoding, errors
        if path == owner_path:
            pytest.fail("oversized allocation owner used Path.read_text")
        return ""

    monkeypatch.setattr(run_manifest, "atomic_write", fail_after_oversize)
    monkeypatch.setattr(Path, "read_text", text_read_must_not_run)

    with pytest.raises(RunManifestError) as raised:
        _ = run_manifest.RunManifestStore.create(context, root_manifest(context))
    assert raised.value.kind is ManifestErrorKind.WRITE_INTERRUPTED
    assert owner_path.parent.is_dir()


def test_allocation_cleanup_preserves_directory_swapped_after_owner_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = storage_context(tmp_path)
    run_dir = context.authoritative_root / "parent-run-001"
    successor = run_dir / "successor.txt"

    def fail_write(_path: Path, _payload: ManifestPayload) -> NoReturn:
        raise RunManifestError(ManifestErrorKind.WRITE_INTERRUPTED, "forced")

    def swap_after_read(path: Path, limit: int) -> bytes:
        content = read_verified_bytes(path, limit)
        shutil.rmtree(run_dir)
        run_dir.mkdir()
        _ = (run_dir / ".allocation-owner").write_bytes(b"successor")
        _ = successor.write_text("preserve", encoding="utf-8")
        return content

    monkeypatch.setattr(run_manifest, "atomic_write", fail_write)
    monkeypatch.setattr(run_manifest_rollback, "read_verified_bytes", swap_after_read)

    with pytest.raises((RunManifestError, OSError)):
        _ = run_manifest.RunManifestStore.create(context, root_manifest(context))
    assert successor.read_text(encoding="utf-8") == "preserve"
