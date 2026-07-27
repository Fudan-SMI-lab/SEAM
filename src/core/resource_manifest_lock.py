from __future__ import annotations

from pathlib import Path
from types import TracebackType
import typing
from typing import Literal

from .resource_manifest_models import (
    ResourceManifestError,
    ResourceManifestErrorKind,
)


class ResourceManifestLock:
    def __init__(self, report_dir: Path) -> None:
        self._path = report_dir / ".resource-manifest.lock"

    def __enter__(self) -> None:
        try:
            self._path.mkdir()
        except FileExistsError as exc:
            raise ResourceManifestError(
                ResourceManifestErrorKind.CONCURRENT_WRITE,
                "another process owns the resource manifest lock",
            ) from exc

    def __exit__(
        self,
        exc_type: typing.Optional[typing.Type[BaseException]],
        exc_value: typing.Optional[BaseException],
        traceback: typing.Optional[TracebackType],
    ) -> Literal[False]:
        self._path.rmdir()
        return False
