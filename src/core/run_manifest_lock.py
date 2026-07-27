from __future__ import annotations

from pathlib import Path
from types import TracebackType
from typing import Literal, final

from .run_manifest_models import ManifestErrorKind, RunManifestError


@final
class RunFileLock:
    def __init__(self, run_dir: Path) -> None:
        self._lock_dir = run_dir / ".manifest-write.lock"

    def __enter__(self) -> None:
        try:
            self._lock_dir.mkdir()
        except FileExistsError as exc:
            raise RunManifestError(
                ManifestErrorKind.CONCURRENT_WRITE,
                "another process owns the run lock",
            ) from exc

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        self._lock_dir.rmdir()
        return False
