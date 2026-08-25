from __future__ import annotations

from pathlib import Path
from typing import NamedTuple, final


class PathIdentity(NamedTuple):
    path: Path
    device: int
    inode: int
    mode: int
    attributes: int
    size: int
    modified_ns: int
    changed_ns: int


class LinkIdentity(NamedTuple):
    path: Path
    device: int
    inode: int
    mode: int
    attributes: int
    size: int
    modified_ns: int
    changed_ns: int
    link_target: str


@final
class RealTree:
    __slots__ = ("_root", "_directories", "_files", "_links")

    def __init__(
        self,
        root: PathIdentity,
        directories: tuple[PathIdentity, ...],
        files: tuple[PathIdentity, ...],
        links: tuple[LinkIdentity, ...] = (),
    ) -> None:
        self._root = root
        self._directories = directories
        self._files = files
        self._links = links

    @property
    def root(self) -> PathIdentity:
        return self._root

    @property
    def directories(self) -> tuple[PathIdentity, ...]:
        return self._directories

    @property
    def files(self) -> tuple[PathIdentity, ...]:
        return self._files

    @property
    def links(self) -> tuple[LinkIdentity, ...]:
        return self._links
