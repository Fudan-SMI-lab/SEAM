from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path
from typing import BinaryIO, NamedTuple

from core.continuation_lock_identity import (
    LockIdentity,
    lock_identity,
    read_verified_bytes,
    release_owned_file,
)
from core.evidence_limits import MAX_EVIDENCE_FILE_BYTES
from core.phase5_attempt_models import (
    ArtifactFileReceipt,
    AttemptReceiptError,
    AttemptReceiptErrorKind,
    Sha256Digest,
)


class WrittenArtifact(NamedTuple):
    path: Path
    identity: LockIdentity
    content: bytes


def _exclusive_descriptor(path: Path) -> tuple[int, LockIdentity]:
    if path.parent.is_symlink() or path.is_symlink():
        raise AttemptReceiptError(AttemptReceiptErrorKind.UNSAFE_PATH, str(path))
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    return descriptor, lock_identity(os.fstat(descriptor))


def write_text_exclusive(path: Path, content: str) -> WrittenArtifact:
    encoded = content.encode("utf-8")
    descriptor, identity = _exclusive_descriptor(path)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            _ = handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError:
        _release_best_effort(path, identity)
        raise
    return WrittenArtifact(path, identity, encoded)


def copy_file_exclusive(source: Path, destination: Path) -> None:
    content = read_verified_bytes(source, MAX_EVIDENCE_FILE_BYTES)
    descriptor, identity = _exclusive_descriptor(destination)
    try:
        with os.fdopen(descriptor, "wb") as destination_handle:
            _ = destination_handle.write(content)
            destination_handle.flush()
            os.fsync(destination_handle.fileno())
    except OSError:
        _release_best_effort(destination, identity)
        raise


def read_bounded_descriptor(source: BinaryIO, maximum_bytes: int) -> bytes:
    initial = os.fstat(source.fileno())
    if not stat.S_ISREG(initial.st_mode) or initial.st_size > maximum_bytes:
        raise OSError("captured output is unsafe or exceeds its limit")
    _ = source.seek(0)
    content = source.read(maximum_bytes + 1)
    final = os.fstat(source.fileno())
    stable = (
        initial.st_dev,
        initial.st_ino,
        initial.st_mode,
        initial.st_size,
        initial.st_mtime_ns,
        initial.st_ctime_ns,
    ) == (
        final.st_dev,
        final.st_ino,
        final.st_mode,
        final.st_size,
        final.st_mtime_ns,
        final.st_ctime_ns,
    )
    if len(content) > maximum_bytes or len(content) != initial.st_size or not stable:
        raise OSError("captured output changed during bounded read")
    return content


def rollback_created(artifacts: list[WrittenArtifact]) -> tuple[OSError, ...]:
    errors: list[OSError] = []
    for artifact in reversed(artifacts):
        try:
            release_owned_file(artifact.path, artifact.identity, artifact.content)
        except OSError as exc:
            errors.append(exc)
    return tuple(errors)


def artifact_receipt(artifact: WrittenArtifact) -> ArtifactFileReceipt:
    return ArtifactFileReceipt(
        path=str(Path(os.path.abspath(artifact.path))),
        sha256=sha256_content(artifact.content),
        size_bytes=len(artifact.content),
        complete=True,
    )


def sha256_content(content: bytes) -> Sha256Digest:
    return Sha256Digest(hashlib.sha256(content).hexdigest())


def _release_best_effort(path: Path, identity: LockIdentity) -> None:
    try:
        release_owned_file(path, identity)
    except OSError:
        return
