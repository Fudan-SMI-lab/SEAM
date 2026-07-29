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

from .resource_manifest_models import (
    ResourceManifest,
    ResourceManifestError,
    ResourceManifestErrorKind,
)
from .resource_manifest_fact_projection import (
    build_backend_facts as build_backend_facts,
    build_opencode_facts as build_opencode_facts,
    capture_launcher_facts as capture_launcher_facts,
)
from .resource_manifest_validation import validate_manifest_structure

atomic_replace = os.replace


class ResourceManifestPayload(NamedTuple):
    manifest: ResourceManifest
    content: bytes


def validated_payload(manifest: ResourceManifest) -> ResourceManifestPayload:
    content = (manifest.model_dump_json(indent=2, by_alias=True) + "\n").encode()
    try:
        parsed = ResourceManifest.model_validate_json(content)
    except ValidationError as exc:
        raise ResourceManifestError(
            ResourceManifestErrorKind.MALFORMED, str(exc)
        ) from exc
    return ResourceManifestPayload(parsed, content)


def read_resource_manifest(path: Path) -> ResourceManifest:
    try:
        content = read_verified_bytes(path, 1024 * 1024)
    except BoundedReadError as exc:
        if exc.kind is not BoundedReadErrorKind.MISSING:
            raise ResourceManifestError(
                ResourceManifestErrorKind.MALFORMED, str(exc)
            ) from exc
        raise ResourceManifestError(
            ResourceManifestErrorKind.MISSING_MANIFEST,
            f"missing resource manifest: {path.name}",
        ) from exc
    try:
        manifest = ResourceManifest.model_validate_json(content)
        validate_manifest_structure(manifest)
        return manifest
    except ValidationError as exc:
        detail = str(exc)
        if "schema_version" in detail:
            kind = ResourceManifestErrorKind.VERSION_MISMATCH
        elif "schema" in detail:
            kind = ResourceManifestErrorKind.SCHEMA_MISMATCH
        else:
            kind = ResourceManifestErrorKind.MALFORMED
        raise ResourceManifestError(kind, detail) from exc


def atomic_write(path: Path, payload: ResourceManifestPayload) -> None:
    temp_path = path.parent / f".{path.name}.{uuid4().hex}.tmp"
    try:
        with temp_path.open("xb") as handle:
            _ = handle.write(payload.content)
            handle.flush()
            os.fsync(handle.fileno())
        atomic_replace(temp_path, path)
        fsync_parent(path)
    except OSError as exc:
        cleanup_detail = ""
        try:
            temp_path.unlink()
        except FileNotFoundError:
            cleanup_detail = "; staging file already absent"
        except OSError as cleanup_exc:
            cleanup_detail = f"; staging cleanup failed: {cleanup_exc}"
        raise ResourceManifestError(
            ResourceManifestErrorKind.WRITE_INTERRUPTED,
            f"resource manifest write interrupted: {exc}{cleanup_detail}",
        ) from exc
