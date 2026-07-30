from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from core.atomic_directory import rename_directory_no_replace
from core.atomic_file import atomic_write_bytes_with
from core.run_manifest_models import RunManifestError
from core.owned_directory_lock import (
    DirectoryLockIdentity,
    OwnedDirectoryChangedError,
    close_directory_identity,
    empty_directory_identity,
    release_owned_directory,
)
from core.continuation_lock_identity import (
    fsync_parent,
)
from core.run_manifest_paths import copy_real_tree_into
from core.secret_redaction import JsonValue, redact_json_value
from core.v3_runtime_report import V3RuntimeReport

from .models import (
    ContinuationRunSummary,
    FinalizationDiagnostic,
    RunSummary,
    SidecarWriteError,
)

atomic_replace = os.replace
atomic_directory_rename = rename_directory_no_replace
artifact_tree_copy = copy_real_tree_into


def _cleanup_staging(
    staging: Path,
    identity: DirectoryLockIdentity,
) -> OSError | None:
    try:
        release_owned_directory(staging, identity)
    except FileNotFoundError:
        return None
    except (OSError, OwnedDirectoryChangedError) as exc:
        return exc
    return None


def copy_run_artifacts(temp_dir: Path, output_dir: Path) -> str | None:
    source = temp_dir / ".sm-artifacts"
    if not source.exists():
        return None
    destination = output_dir / ".sm-artifacts"
    if destination.exists():
        raise FileExistsError(destination)
    staging = output_dir / f".sm-artifacts.{uuid4().hex}.tmp"
    staging.mkdir(mode=0o700)
    staging_identity = empty_directory_identity(staging)
    published = False
    try:
        artifact_tree_copy(source, temp_dir, staging)
        atomic_directory_rename(staging, destination)
        published = True
        fsync_parent(destination)
    except RunManifestError as exc:
        cleanup_target = destination if published else staging
        cleanup_failure = _cleanup_staging(cleanup_target, staging_identity)
        if cleanup_failure is not None:
            raise SidecarWriteError(
                path=str(cleanup_target),
                detail=f"artifact publication cleanup failed: {cleanup_failure}",
            ) from exc
        raise SidecarWriteError(
            path=str(source),
            detail=f"artifact copy rejected: {exc}",
        ) from exc
    except (OSError, shutil.Error) as exc:
        cleanup_target = destination if published else staging
        cleanup_failure = _cleanup_staging(cleanup_target, staging_identity)
        if cleanup_failure is not None:
            raise SidecarWriteError(
                path=str(cleanup_target),
                detail=f"artifact publication cleanup failed: {cleanup_failure}",
            ) from exc
        raise
    close_directory_identity(staging_identity)
    return str(destination)


def _atomic_write(path: Path, content: bytes) -> None:
    try:
        atomic_write_bytes_with(path, content, atomic_replace, fsync_parent)
    except OSError as exc:
        raise SidecarWriteError(
            path=str(path),
            detail=f"atomic write interrupted: {exc}",
        ) from exc


def write_json_text(path: Path, serialized_json: str) -> str:
    content = serialized_json.replace("\n", os.linesep).encode()
    _atomic_write(path, content)
    return str(path)


def write_summary(
    path: Path,
    summary: RunSummary,
    continuation: ContinuationRunSummary | None = None,
    runtime_report: V3RuntimeReport | None = None,
) -> str:
    payload = asdict(summary)
    trace_payload = summary.trace._asdict()
    correlation = summary.trace.correlation
    if correlation is not None:
        trace_payload["correlation"] = correlation._asdict()
    else:
        trace_payload.pop("correlation", None)
    payload["trace"] = trace_payload
    if continuation is not None:
        payload["continuation"] = asdict(continuation)
    if runtime_report is not None:
        payload["runtime"] = runtime_report.model_dump(mode="json")
    text = json.dumps(
        redact_json_value(payload),
        indent=2,
        ensure_ascii=False,
        default=str,
    )
    _atomic_write(path, text.replace("\n", os.linesep).encode())
    return str(path)


def write_diagnostics(
    path: Path,
    diagnostics: tuple[FinalizationDiagnostic, ...],
) -> str:
    payload: JsonValue = [
        {
            "stage": item.stage.value,
            "error_type": item.error_type,
            "detail": item.detail,
        }
        for item in diagnostics
    ]
    text = json.dumps(
        redact_json_value(payload),
        indent=2,
        ensure_ascii=False,
    )
    _atomic_write(path, text.replace("\n", os.linesep).encode())
    return str(path)
