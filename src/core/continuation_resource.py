from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

from .continuation_models import ContinuationError, ContinuationErrorKind
from .continuation_paths import PathKind, canonical_existing_path
from .resource_manifest import (
    RESOURCE_MANIFEST_FILENAME,
    ResourceManifest,
    ResourceManifestError,
    ResourceManifestErrorKind,
    ResourceManifestIdentity,
)
from .resource_manifest_authority import (
    _ResourceCaptureAuthority,
    require_manifest_authority,
)
from .resource_manifest_io import read_resource_manifest

_CAPABILITY_SIZE = 32


def _error(detail: str) -> ContinuationError:
    return ContinuationError(ContinuationErrorKind.AUTHORITY_INVALID, detail)


def _capability_path(report_dir: Path) -> Path:
    authority_root = report_dir.parent.parent / ".seam-resource-authority"
    identity = hashlib.sha256(os.fspath(report_dir).encode()).hexdigest()
    return authority_root / f"{identity}.key"


def _read_existing_secret(report_dir: Path) -> bytes:
    capability = canonical_existing_path(
        _capability_path(report_dir),
        PathKind.FILE,
        ContinuationErrorKind.AUTHORITY_INVALID,
    )
    try:
        metadata = capability.lstat()
        if not stat.S_ISREG(metadata.st_mode):
            raise _error("resource authority capability is not a regular file")
        if os.name != "nt" and metadata.st_mode & 0o077:
            raise _error("resource authority capability permissions are too broad")
        secret = capability.read_bytes()
    except ContinuationError:
        raise
    except OSError as exc:
        raise _error("resource authority capability is unreadable") from exc
    if len(secret) != _CAPABILITY_SIZE:
        raise _error("resource authority capability has an invalid length")
    return secret


def open_existing_resource_manifest(
    report_dir: Path,
    identity: ResourceManifestIdentity,
) -> ResourceManifest:
    secret = _read_existing_secret(report_dir)
    authority = _ResourceCaptureAuthority(identity, secret)
    try:
        manifest = read_resource_manifest(report_dir / RESOURCE_MANIFEST_FILENAME)
        if manifest.run_id != identity.run_id:
            raise ResourceManifestError(
                ResourceManifestErrorKind.RUN_CONTEXT_MISMATCH,
                "resource manifest run_id differs from its report namespace",
            )
        if manifest.workflow_digest != identity.workflow_digest:
            raise ResourceManifestError(
                ResourceManifestErrorKind.DIGEST_MISMATCH,
                "resource manifest workflow digest differs from its pinned value",
            )
        if manifest.workspace_digest != identity.workspace_digest:
            raise ResourceManifestError(
                ResourceManifestErrorKind.RUN_CONTEXT_MISMATCH,
                "resource manifest workspace digest differs from its run context",
            )
        require_manifest_authority(manifest, authority)
    except ResourceManifestError as exc:
        raise _error(
            f"authoritative resource manifest is invalid: {exc.kind.value}"
        ) from exc
    return manifest
