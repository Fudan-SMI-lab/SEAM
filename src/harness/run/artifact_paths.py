from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from enum import Enum, unique
from hashlib import sha256
from pathlib import Path

from core.compat import override

from core.evidence_limits import EvidenceBudget, MAX_EVIDENCE_FILE_BYTES
from core.run_manifest import RunManifestError
from core.run_manifest_paths import (
    inspect_real_tree,
    read_real_file,
    read_real_tree_file,
)

from .models import FinalizationStage

_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


@unique
class ArtifactPathKind(str, Enum):
    FILE = "file"
    DIRECTORY = "directory"


@dataclass(frozen=True)
class SidecarValidationError(ValueError):
    path: str
    expected: ArtifactPathKind
    detail: str

    @override
    def __str__(self) -> str:
        return f"{self.path}: expected contained {self.expected.value}; {self.detail}"


@dataclass(frozen=True)
class ArtifactFingerprint:
    kind: ArtifactPathKind
    device: int
    inode: int
    digest: str


@dataclass(frozen=True)
class ArtifactReceipt:
    path: str
    canonical_path: str
    fingerprint: ArtifactFingerprint
    stage: FinalizationStage

    @property
    def kind(self) -> ArtifactPathKind:
        return self.fingerprint.kind


@dataclass(frozen=True)
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
    budget = EvidenceBudget()
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
                metadata = entry.stat(follow_symlinks=False)
                budget.charge(
                    path.relative_to(root),
                    metadata.st_size if entry.is_file(follow_symlinks=False) else 0,
                )
                found.append(path)
                if entry.is_dir(follow_symlinks=False):
                    pending.append(path)
    return tuple(sorted(found))


def _fingerprint(
    path: Path,
    kind: ArtifactPathKind,
    container: Path | None = None,
) -> ArtifactFingerprint:
    digest = sha256()
    boundary = container or path.parent
    try:
        if kind is ArtifactPathKind.FILE:
            metadata, content = read_real_file(path, boundary, MAX_EVIDENCE_FILE_BYTES)
            digest.update(content)
            device, inode = metadata.device, metadata.inode
        else:
            tree = inspect_real_tree(path, boundary)
            entries = sorted(
                (*tree.directories, *tree.files, *tree.links),
                key=lambda item: str(item.path),
            )
            file_paths = {identity.path for identity in tree.files}
            link_paths = {identity.path for identity in tree.links}
            for identity in entries:
                digest.update(
                    identity.path.relative_to(tree.root.path).as_posix().encode()
                )
                if identity.path in file_paths:
                    digest.update(b"F")
                    content = read_real_tree_file(tree, identity)
                    digest.update(sha256(content).digest())
                elif identity.path in link_paths:
                    digest.update(b"L")
                    digest.update(identity.link_target.encode())
                else:
                    digest.update(b"D")
            device, inode = tree.root.device, tree.root.inode
    except RunManifestError as exc:
        raise SidecarValidationError(str(path), kind, str(exc)) from exc
    return ArtifactFingerprint(kind, device, inode, digest.hexdigest())


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
        raw_path, str(canonical), _fingerprint(canonical, kind, report), stage
    )


def snapshot_report(report_dir: Path) -> ReportSnapshot:
    report = report_dir.resolve(strict=True)
    entries: list[tuple[str, ArtifactFingerprint]] = []
    for path in _walk_regular_tree(report, reject_links=False):
        kind = ArtifactPathKind.DIRECTORY if path.is_dir() else ArtifactPathKind.FILE
        try:
            fingerprint = _fingerprint(path, kind, report)
        except SidecarValidationError:
            continue
        entries.append((str(path.resolve(strict=True)), fingerprint))
    return ReportSnapshot(tuple(entries))
