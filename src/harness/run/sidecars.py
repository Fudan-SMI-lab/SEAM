from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from core.run_manifest_models import RunManifestError
from core.run_manifest_paths import copy_real_tree
from core.v3_runtime_report import V3RuntimeReport

from .models import (
    ContinuationRunSummary,
    FinalizationDiagnostic,
    RunSummary,
    SidecarWriteError,
)

atomic_replace = os.replace
artifact_tree_copy = copy_real_tree


def _cleanup_staging(staging: Path) -> OSError | None:
    try:
        shutil.rmtree(staging)
    except FileNotFoundError:
        return None
    except OSError as exc:
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
    try:
        artifact_tree_copy(source, temp_dir, staging)
        _ = staging.rename(destination)
    except RunManifestError as exc:
        cleanup_failure = _cleanup_staging(staging)
        if cleanup_failure is not None:
            raise SidecarWriteError(
                path=str(staging),
                detail=f"artifact staging cleanup failed: {cleanup_failure}",
            ) from exc
        raise SidecarWriteError(
            path=str(source),
            detail=f"artifact copy rejected: {exc}",
        ) from exc
    except (OSError, shutil.Error) as exc:
        cleanup_failure = _cleanup_staging(staging)
        if cleanup_failure is not None:
            raise SidecarWriteError(
                path=str(staging),
                detail=f"artifact staging cleanup failed: {cleanup_failure}",
            ) from exc
        raise
    return str(destination)


def _atomic_write(path: Path, content: bytes) -> None:
    temp_path = path.parent / f".{path.name}.{uuid4().hex}.tmp"
    try:
        with temp_path.open("xb") as handle:
            _ = handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        atomic_replace(temp_path, path)
    except OSError as exc:
        cleanup_detail = ""
        try:
            temp_path.unlink(missing_ok=True)
        except OSError as cleanup_exc:
            cleanup_detail = f"; temporary cleanup failed: {cleanup_exc}"
        raise SidecarWriteError(
            path=str(path),
            detail=f"atomic write interrupted: {exc}{cleanup_detail}",
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
    text = json.dumps(payload, indent=2, ensure_ascii=False, default=str)
    _atomic_write(path, text.replace("\n", os.linesep).encode())
    return str(path)


def write_diagnostics(
    path: Path,
    diagnostics: tuple[FinalizationDiagnostic, ...],
) -> str:
    payload = [
        {
            "stage": item.stage.value,
            "error_type": item.error_type,
            "detail": item.detail,
        }
        for item in diagnostics
    ]
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    _atomic_write(path, text.replace("\n", os.linesep).encode())
    return str(path)
