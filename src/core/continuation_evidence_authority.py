from __future__ import annotations

import hashlib
from pathlib import Path
from typing import NamedTuple

from pydantic import ValidationError

from .continuation_evidence_models import (
    ChildEvidenceRequest,
    ContinuationEvidenceError,
    ContinuationEvidenceErrorKind,
    ContinuationEvidenceRoot,
)
from .continuation_models import ContinuationError, ResolvedTerminalParent
from .continuation_resolver import resolve_terminal_parent
from .run_manifest import (
    EvidenceDigest,
    RunManifest,
    RunManifestError,
    RunManifestStore,
    RunStorageContext,
)
from .run_manifest_paths import digest_inventory

_ROOT_RECEIPT_PATH = "validated/continuation_evidence_root.json"


class ParentEvidenceAuthority(NamedTuple):
    context: RunStorageContext
    store: RunManifestStore
    report_inventory: tuple[EvidenceDigest, ...]


def _error(
    kind: ContinuationEvidenceErrorKind,
    detail: str,
) -> ContinuationEvidenceError:
    return ContinuationEvidenceError(kind=kind, detail=detail)


def _trace_inventory(report_dir: Path) -> tuple[EvidenceDigest, ...]:
    trace_dir = report_dir / "trace"
    if not trace_dir.exists():
        return ()
    return digest_inventory(trace_dir, report_dir)


def verify_external_evidence_root(
    report_dir: Path,
    manifest: RunManifest,
    *,
    required: bool = False,
) -> None:
    matches = tuple(
        item
        for item in manifest.sealed_evidence
        if item.relative_path == _ROOT_RECEIPT_PATH
    )
    if not matches:
        if required:
            raise _error(
                ContinuationEvidenceErrorKind.CHILD_EVIDENCE_DRIFT,
                "continuation child has no sealed evidence root",
            )
        return
    if len(matches) != 1:
        raise _error(
            ContinuationEvidenceErrorKind.CHILD_EVIDENCE_DRIFT,
            "child evidence root receipt is ambiguous",
        )
    evidence = matches[0]
    root_path = report_dir / "sealed-artifacts" / _ROOT_RECEIPT_PATH
    try:
        content = root_path.read_bytes()
        root = ContinuationEvidenceRoot.model_validate_json(content)
        precontinuation = digest_inventory(
            report_dir / "artifacts" / "pre-continuation",
            report_dir,
        )
        trace = _trace_inventory(report_dir)
    except (OSError, RunManifestError, ValidationError) as exc:
        raise _error(
            ContinuationEvidenceErrorKind.CHILD_EVIDENCE_DRIFT,
            "external child evidence root is unavailable or malformed",
        ) from exc
    receipt_matches = (
        len(content) == evidence.size_bytes
        and hashlib.sha256(content).hexdigest() == evidence.digest
    )
    root_matches = (
        root.child_run_id == str(manifest.run_id)
        and root.precontinuation_files == precontinuation
        and root.trace_files == trace
    )
    if not receipt_matches or not root_matches:
        raise _error(
            ContinuationEvidenceErrorKind.CHILD_EVIDENCE_DRIFT,
            "external child evidence changed after sealing",
        )


def verify_parent_evidence(
    parent: ResolvedTerminalParent,
    request: ChildEvidenceRequest,
    expected_inventory: tuple[EvidenceDigest, ...] | None = None,
) -> ParentEvidenceAuthority:
    try:
        summary_path = request.continuation.summary_path.resolve(strict=True)
        if summary_path.name != "summary.json" or summary_path.parent.name != str(
            parent.run_id
        ):
            raise OSError("parent summary no longer identifies the claimed run")
        context = RunStorageContext.bind(
            summary_path.parent.parent,
            parent.output_project,
        )
        resolved = resolve_terminal_parent(summary_path)
        store = RunManifestStore.open_readonly(
            context,
            parent.run_id,
            parent.workflow_digest,
        )
        current = store.read()
        verify_external_evidence_root(
            summary_path.parent,
            current,
            required=current.parent_run_id is not None,
        )
        report_inventory = digest_inventory(
            summary_path.parent, context.authoritative_root
        )
    except (
        ContinuationError,
        ContinuationEvidenceError,
        OSError,
        RunManifestError,
    ) as exc:
        raise _error(
            ContinuationEvidenceErrorKind.PARENT_EVIDENCE_DRIFT,
            "authoritative parent evidence failed byte and digest verification",
        ) from exc
    if resolved != parent or current != parent.run_manifest:
        raise _error(
            ContinuationEvidenceErrorKind.PARENT_EVIDENCE_DRIFT,
            "authoritative parent evidence no longer matches the claimed parent",
        )
    if expected_inventory is not None and report_inventory != expected_inventory:
        raise _error(
            ContinuationEvidenceErrorKind.PARENT_EVIDENCE_DRIFT,
            "authoritative parent report bytes changed during continuation",
        )
    return ParentEvidenceAuthority(context, store, report_inventory)
