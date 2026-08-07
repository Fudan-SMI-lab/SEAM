from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path
from typing import Dict, Final, NamedTuple

from pydantic import JsonValue, TypeAdapter, ValidationError
import yaml

from core.continuation_hydration_models import (
    CanonicalJsonObject,
    ContinuationHydrationError,
    ContinuationHydrationErrorKind,
    ContinuationHydrationRequest,
)
from core.continuation_models import (
    ContinuationError,
    ContinuationErrorKind,
    RunSummaryDocument,
)
from core.continuation_paths import read_explicit_summary_snapshot
from core.continuation_resolver import resolve_authority
from core.continuation_workflow_snapshot import (
    WorkflowSnapshotError,
    load_workflow_snapshot,
    read_workflow_snapshot,
)
from core.run_manifest import EvidenceDigest, Sha256Digest
from core.types import WorkflowDefinition

_MAX_CANONICAL_BYTES: Final = 16 * 1024 * 1024
_CANONICAL_ADAPTER: Final = TypeAdapter(Dict[str, JsonValue])


class HydrationAuthority(NamedTuple):
    run_dir: Path
    summary: RunSummaryDocument
    workflow: WorkflowDefinition


def _error(
    kind: ContinuationHydrationErrorKind, detail: str
) -> ContinuationHydrationError:
    return ContinuationHydrationError(kind, detail)


def _digest(content: bytes) -> Sha256Digest:
    return Sha256Digest(hashlib.sha256(content).hexdigest())


def _require_summary(
    request: ContinuationHydrationRequest,
) -> tuple[Path, RunSummaryDocument]:
    try:
        snapshot = read_explicit_summary_snapshot(request.summary_path)
    except (ContinuationError, OSError) as exc:
        raise _error(
            ContinuationHydrationErrorKind.AUTHORITY_MISMATCH,
            "parent summary is unavailable",
        ) from exc
    if snapshot.digest != request.parent.summary_digest:
        raise _error(
            ContinuationHydrationErrorKind.AUTHORITY_MISMATCH,
            "parent summary changed after resolution",
        )
    canonical_summary = snapshot.path
    summary = snapshot.document
    try:
        summary_project = Path(summary.temp_dir).resolve(strict=True)
        summary_workflow = Path(summary.workflow_path).resolve(strict=True)
        summary_report = Path(summary.output_dir).resolve(strict=True)
    except OSError as exc:
        raise _error(
            ContinuationHydrationErrorKind.AUTHORITY_MISMATCH,
            "parent summary paths changed after resolution",
        ) from exc
    parent = request.parent
    identity_matches = (
        summary.run_id == str(parent.run_id)
        and summary.overall_status == parent.status.value
        and summary_project == parent.output_project
        and summary_workflow == parent.workflow_path
        and summary_report == canonical_summary.parent
        and canonical_summary.parent.name == str(parent.run_id)
        and parent.terminal_anchor == parent.run_manifest.terminal_anchor
    )
    if not identity_matches:
        raise _error(
            ContinuationHydrationErrorKind.AUTHORITY_MISMATCH,
            "resolved parent and summary snapshot do not agree",
        )
    return canonical_summary.parent, summary


def _load_workflow(request: ContinuationHydrationRequest) -> WorkflowDefinition:
    parent = request.parent
    try:
        content = read_workflow_snapshot(parent.workflow_path)
    except WorkflowSnapshotError as exc:
        raise _error(
            ContinuationHydrationErrorKind.WORKFLOW_DIGEST_MISMATCH,
            "pinned workflow is unavailable",
        ) from exc
    actual_digest = _digest(content)
    if (
        actual_digest != parent.workflow_digest
        or actual_digest != parent.run_manifest.workflow_digest
    ):
        raise _error(
            ContinuationHydrationErrorKind.WORKFLOW_DIGEST_MISMATCH,
            "pinned workflow digest changed after parent resolution",
        )
    try:
        workflow = load_workflow_snapshot(content, str(parent.workflow_path))
    except (
        OSError,
        RecursionError,
        TypeError,
        UnicodeError,
        ValueError,
        yaml.YAMLError,
    ) as exc:
        kind = (
            ContinuationHydrationErrorKind.AMBIGUOUS_ANCHOR
            if "Duplicate phase id" in str(exc)
            else ContinuationHydrationErrorKind.UNKNOWN_ANCHOR
        )
        raise _error(kind, f"pinned workflow topology is invalid: {exc}") from exc
    return workflow


def require_hydration_authority(
    request: ContinuationHydrationRequest,
) -> HydrationAuthority:
    run_dir, summary = _require_summary(request)
    workflow = _load_workflow(request)
    try:
        current = resolve_authority(request.summary_path)
    except ContinuationError as exc:
        kind = (
            ContinuationHydrationErrorKind.CANONICAL_DIGEST_MISMATCH
            if exc.kind is ContinuationErrorKind.AUTHORITY_INVALID
            else ContinuationHydrationErrorKind.AUTHORITY_MISMATCH
        )
        raise _error(kind, f"parent authority revalidation failed: {exc}") from exc
    if current.parent != request.parent or current.authoritative_root != run_dir.parent:
        raise _error(
            ContinuationHydrationErrorKind.AUTHORITY_MISMATCH,
            "resolved parent authority changed before hydration",
        )
    return HydrationAuthority(run_dir, summary, workflow)


def load_verified_bytes(
    sealed_root: Path,
    evidence: EvidenceDigest,
) -> bytes:
    if evidence.size_bytes > _MAX_CANONICAL_BYTES:
        raise _error(
            ContinuationHydrationErrorKind.MALFORMED_CANONICAL_OUTPUT,
            f"sealed evidence exceeds the hydration byte limit: {evidence.relative_path}",
        )
    path = sealed_root / Path(evidence.relative_path)
    try:
        canonical_root = sealed_root.resolve(strict=True)
        canonical_path = path.resolve(strict=True)
        if canonical_root not in canonical_path.parents:
            raise _error(
                ContinuationHydrationErrorKind.AUTHORITY_MISMATCH,
                f"sealed evidence escaped its authority root: {evidence.relative_path}",
            )
        current = canonical_path
        while current != canonical_root:
            metadata = current.lstat()
            attributes = getattr(metadata, "st_file_attributes", 0)
            if stat.S_ISLNK(metadata.st_mode) or bool(attributes & 0x400):
                raise _error(
                    ContinuationHydrationErrorKind.AUTHORITY_MISMATCH,
                    f"sealed evidence has a linked component: {evidence.relative_path}",
                )
            current = current.parent
        inspected = canonical_path.lstat()
        with canonical_path.open("rb") as handle:
            opened = os.fstat(handle.fileno())
            identity = (opened.st_dev, opened.st_ino, opened.st_mode, opened.st_size)
            expected = (
                inspected.st_dev,
                inspected.st_ino,
                inspected.st_mode,
                inspected.st_size,
            )
            if identity != expected or not stat.S_ISREG(opened.st_mode):
                raise _error(
                    ContinuationHydrationErrorKind.AUTHORITY_MISMATCH,
                    f"sealed evidence handle changed: {evidence.relative_path}",
                )
            if opened.st_size != evidence.size_bytes:
                raise _error(
                    ContinuationHydrationErrorKind.CANONICAL_DIGEST_MISMATCH,
                    f"sealed evidence size changed: {evidence.relative_path}",
                )
            content = handle.read(evidence.size_bytes + 1)
            after_handle = os.fstat(handle.fileno())
        after_path = canonical_path.lstat()
        after_identity = (
            after_handle.st_dev,
            after_handle.st_ino,
            after_handle.st_mode,
            after_handle.st_size,
        )
        path_identity = (
            after_path.st_dev,
            after_path.st_ino,
            after_path.st_mode,
            after_path.st_size,
        )
        if after_identity != identity or path_identity != identity:
            raise _error(
                ContinuationHydrationErrorKind.AUTHORITY_MISMATCH,
                f"sealed evidence changed while reading: {evidence.relative_path}",
            )
    except OSError as exc:
        raise _error(
            ContinuationHydrationErrorKind.MISSING_CANONICAL_OUTPUT,
            f"sealed evidence is unavailable: {evidence.relative_path}",
        ) from exc
    if len(content) != evidence.size_bytes or _digest(content) != evidence.digest:
        raise _error(
            ContinuationHydrationErrorKind.CANONICAL_DIGEST_MISMATCH,
            f"sealed evidence digest changed: {evidence.relative_path}",
        )
    return content


def load_canonical_json(
    sealed_root: Path,
    evidence: EvidenceDigest,
) -> tuple[CanonicalJsonObject, bytes]:
    content = load_verified_bytes(sealed_root, evidence)
    try:
        return _CANONICAL_ADAPTER.validate_json(content), content
    except ValidationError as exc:
        raise _error(
            ContinuationHydrationErrorKind.MALFORMED_CANONICAL_OUTPUT,
            f"canonical output is not a JSON object: {evidence.relative_path}",
        ) from exc
