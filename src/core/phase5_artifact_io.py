from __future__ import annotations

import os
import shutil
from pathlib import Path

from core.phase5_attempt_models import AttemptReceiptError, AttemptReceiptErrorKind


def _exclusive_descriptor(path: Path) -> int:
    if path.parent.is_symlink() or path.is_symlink():
        raise AttemptReceiptError(AttemptReceiptErrorKind.UNSAFE_PATH, str(path))
    return os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)


def write_text_exclusive(path: Path, content: str) -> None:
    descriptor = _exclusive_descriptor(path)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            _ = handle.write(content)
    except OSError:
        path.unlink(missing_ok=True)
        raise


def copy_file_exclusive(source: Path, destination: Path) -> None:
    with source.open("rb") as source_handle:
        descriptor = _exclusive_descriptor(destination)
        try:
            with os.fdopen(descriptor, "wb") as destination_handle:
                _ = shutil.copyfileobj(source_handle, destination_handle)
        except OSError:
            destination.unlink(missing_ok=True)
            raise


def rollback_created(paths: list[Path]) -> None:
    for path in reversed(paths):
        path.unlink(missing_ok=True)
