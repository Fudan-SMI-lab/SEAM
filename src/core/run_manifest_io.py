from __future__ import annotations

import os
from pathlib import Path
from typing import NamedTuple
from uuid import uuid4

from pydantic import ValidationError

from .continuation_lock_identity import (
    BoundedReadError,
    BoundedReadErrorKind,
    fsync_parent,
    read_verified_bytes,
)

from .run_manifest_models import (
    ManifestErrorKind,
    RunManifest,
    RunManifestError,
)

atomic_replace = os.replace
_MAX_MANIFEST_BYTES = 1024 * 1024


class ManifestPayload(NamedTuple):
    manifest: RunManifest
    content: bytes


def validated_payload(manifest: RunManifest) -> ManifestPayload:
    content = (manifest.model_dump_json(indent=2, by_alias=True) + "\n").encode()
    try:
        parsed = RunManifest.model_validate_json(content)
    except ValidationError as exc:
        raise RunManifestError(ManifestErrorKind.MALFORMED, str(exc)) from exc
    return ManifestPayload(manifest=parsed, content=content)


def read_manifest(path: Path) -> RunManifest:
    try:
        content = read_verified_bytes(path, _MAX_MANIFEST_BYTES)
    except BoundedReadError as exc:
        if exc.kind is not BoundedReadErrorKind.MISSING:
            raise RunManifestError(ManifestErrorKind.MALFORMED, str(exc)) from exc
        raise RunManifestError(
            ManifestErrorKind.MISSING_MANIFEST, f"missing manifest: {path.name}"
        ) from exc
    try:
        return RunManifest.model_validate_json(content)
    except ValidationError as exc:
        raise RunManifestError(ManifestErrorKind.MALFORMED, str(exc)) from exc


def atomic_write(path: Path, payload: ManifestPayload) -> None:
    temp_path: Path | None = None
    try:
        temp_path = path.parent / f".{path.name}.{uuid4().hex}.tmp"
        with temp_path.open("xb") as handle:
            _ = handle.write(payload.content)
            handle.flush()
            os.fsync(handle.fileno())
        atomic_replace(temp_path, path)
        fsync_parent(path)
    except OSError as exc:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()
        raise RunManifestError(
            ManifestErrorKind.WRITE_INTERRUPTED, f"manifest write interrupted: {exc}"
        ) from exc
