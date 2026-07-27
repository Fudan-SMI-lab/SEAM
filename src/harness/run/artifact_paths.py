from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from enum import Enum, unique
from hashlib import sha256
from pathlib import Path

from typing_extensions import override

from .models import FinalizationStage

_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


@unique
class ArtifactPathKind(str, Enum):
    FILE = "file"
    DIRECTORY = "directory"


@dataclass(frozen=True, slots=True)
class SidecarValidationError(ValueError):
    path: str
    expected: ArtifactPathKind
    detail: str

    @override
    def __str__(self) -> str:
        return f"{self.path}: expected contained {self.expected.value}; {self.detail}"


@dataclass(frozen=True, slots=True)
class ArtifactFingerprint:
    kind: ArtifactPathKind
    device: int
    inode: int
    digest: str


@dataclass(frozen=True, slots=True)
class ArtifactReceipt:
    path: str
    canonical_path: str
    fingerprint: ArtifactFingerprint
    stage: FinalizationStage

    @property
    def kind(self) -> ArtifactPathKind:
        return self.fingerprint.kind


@dataclass(frozen=True, slots=True)
class ReportSnapshot:
    entries: tuple[tuple[str, ArtifactFingerprint], ...]

    def fingerprint_for(self, canonical_path: str) -> ArtifactFingerprint | None:
        return dict(self.entries).get(canonical_path)


def _is_link_or_reparse(path: Path) -> bool:
    metadata = path.lstat()
    attributes = getattr(metadata, "st_file_attributes", 0)
    return stat.S_ISLNK(metadata.st_mode) or bool(attributes & _REPARSE_POINT)


def _walk_regular_tree(
    root: Path,
    *,
    reject_links: bool = True,
) -> tuple[Path, ...]:
    found: list[Path] = []
    pending = [root]
    while pending:
        parent = pending.pop()
        with os.scandir(parent) as entries:
            for entry in entries:
                path = Path(entry.path)
                if _is_link_or_reparse(path):
                    if reject_links:
                        raise SidecarValidationError(
                            str(path),
                            ArtifactPathKind.DIRECTORY,
                            "link or reparse point",
                        )
                    continue
                found.append(path)
                if entry.is_dir(follow_symlinks=False):
                    pending.append(path)
    return tuple(sorted(found))


def _fingerprint(path: Path, kind: ArtifactPathKind) -> ArtifactFingerprint:
    metadata = path.stat()
    digest = sha256()
    if kind is ArtifactPathKind.FILE:
        digest.update(path.read_bytes())
    else:
        for child in _walk_regular_tree(path):
            digest.update(child.relative_to(path).as_posix().encode())
            if child.is_file():
                digest.update(b"F")
                digest.update(sha256(child.read_bytes()).digest())
            elif child.is_dir():
                digest.update(b"D")
            else:
                raise SidecarValidationError(
                    str(child), kind, "unsupported filesystem kind"
                )
    return ArtifactFingerprint(
        kind, metadata.st_dev, metadata.st_ino, digest.hexdigest()
    )


def validate_path_receipt(
    report_dir: Path,
    raw_path: str,
    kind: ArtifactPathKind,
    stage: FinalizationStage,
) -> ArtifactReceipt:
    try:
        report_lexical = Path(os.path.abspath(report_dir))
        report = report_dir.resolve(strict=True)
        lexical = Path(os.path.abspath(raw_path))
        relative = lexical.relative_to(report_lexical)
        if not relative.parts:
            raise SidecarValidationError(
                raw_path, kind, "run report root is not an artifact"
            )
        current = report_lexical
        for part in relative.parts:
            current /= part
            if _is_link_or_reparse(current):
                raise SidecarValidationError(raw_path, kind, "link or reparse point")
        canonical = lexical.resolve(strict=True)
    except SidecarValidationError:
        raise
    except (OSError, ValueError) as exc:
        raise SidecarValidationError(raw_path, kind, str(exc)) from exc
    if report not in canonical.parents:
        raise SidecarValidationError(raw_path, kind, "path escapes run report")
    kind_checks = {
        ArtifactPathKind.FILE: Path.is_file,
        ArtifactPathKind.DIRECTORY: Path.is_dir,
    }
    if not kind_checks[kind](canonical):
        raise SidecarValidationError(raw_path, kind, "path has wrong kind")
    return ArtifactReceipt(
        raw_path, str(canonical), _fingerprint(canonical, kind), stage
    )


def snapshot_report(report_dir: Path) -> ReportSnapshot:
    report = report_dir.resolve(strict=True)
    entries: list[tuple[str, ArtifactFingerprint]] = []
    for path in _walk_regular_tree(report, reject_links=False):
        kind = ArtifactPathKind.DIRECTORY if path.is_dir() else ArtifactPathKind.FILE
        try:
            fingerprint = _fingerprint(path, kind)
        except SidecarValidationError:
            continue
        entries.append((str(path.resolve(strict=True)), fingerprint))
    return ReportSnapshot(tuple(entries))
