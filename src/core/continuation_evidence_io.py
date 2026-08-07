from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path
from typing import NamedTuple, Protocol

from .continuation_evidence_models import ChildEvidenceNamespace, ProjectSnapshot
from .atomic_file import atomic_create_bytes
from .continuation_lock_identity import fsync_parent, read_verified_bytes
from .evidence_limits import MAX_EVIDENCE_FILE_BYTES
from .run_manifest import EvidenceDigest, Sha256Digest
from .run_manifest_inventory import digest_inventory
from .run_manifest_paths import copy_real_tree
from .evidence_limits import EvidenceBudget

_WINDOWS_REPARSE_POINT = 0x400


class JsonEvidenceRecord(Protocol):
    def model_dump_json(self, *, by_alias: bool, indent: int) -> str: ...


def allocate_child_evidence_namespace(
    report_dir: Path,
) -> ChildEvidenceNamespace:
    trace_dir = report_dir / "trace"
    artifact_dir = report_dir / "artifacts"
    precontinuation_dir = artifact_dir / "pre-continuation"
    artifact_dir.mkdir()
    fsync_parent(artifact_dir)
    precontinuation_dir.mkdir()
    fsync_parent(precontinuation_dir)
    return ChildEvidenceNamespace(
        report_dir=report_dir,
        trace_dir=trace_dir,
        artifact_dir=artifact_dir,
        precontinuation_dir=precontinuation_dir,
        baseline_path=precontinuation_dir / "project-baseline.json",
        migration_archive_dir=precontinuation_dir / "migration-reports",
        migration_archive_manifest_path=(
            precontinuation_dir / "migration-reports.manifest.json"
        ),
    )


def _identity(metadata: os.stat_result) -> tuple[int, int, int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        getattr(metadata, "st_file_attributes", 0),
        metadata.st_size,
        metadata.st_mtime_ns,
        0 if os.name == "nt" else metadata.st_ctime_ns,
    )


def _identities_match(
    expected: tuple[int, int, int, int, int, int, int],
    actual: tuple[int, int, int, int, int, int, int],
) -> bool:
    return (
        expected[:3] == actual[:3]
        and expected[3] & _WINDOWS_REPARSE_POINT == actual[3] & _WINDOWS_REPARSE_POINT
        and expected[4:] == actual[4:]
    )


class _ProjectDirectory(NamedTuple):
    path: Path
    identity: tuple[int, int, int, int, int, int, int]


def _is_link(metadata: os.stat_result) -> bool:
    return stat.S_ISLNK(metadata.st_mode) or bool(
        getattr(metadata, "st_file_attributes", 0) & _WINDOWS_REPARSE_POINT
    )


def _require_directories(directories: tuple[_ProjectDirectory, ...]) -> None:
    for directory in directories:
        metadata = directory.path.lstat()
        if (
            _is_link(metadata)
            or not stat.S_ISDIR(metadata.st_mode)
            or not _identities_match(directory.identity, _identity(metadata))
        ):
            raise OSError(
                f"project directory changed during snapshot: {directory.path}"
            )


def _digest_entry(
    path: Path,
    relative_path: str,
    directories: tuple[_ProjectDirectory, ...],
) -> EvidenceDigest:
    _require_directories(directories)
    before = path.lstat()
    with path.open("rb") as handle:
        if not _identities_match(
            _identity(before), _identity(os.fstat(handle.fileno()))
        ):
            raise OSError(f"project file changed before read: {relative_path}")
        content = handle.read(before.st_size + 1)
        after = os.fstat(handle.fileno())
        _require_directories(directories)
    expected = _identity(before)
    if not _identities_match(expected, _identity(after)) or not _identities_match(
        expected, _identity(path.lstat())
    ):
        raise OSError(f"project file changed during read: {relative_path}")
    if len(content) != before.st_size:
        raise OSError(f"project file changed size during read: {relative_path}")
    return EvidenceDigest(
        relative_path=relative_path,
        digest=Sha256Digest(hashlib.sha256(content).hexdigest()),
        size_bytes=len(content),
    )


def snapshot_project_baseline(workspace: Path) -> ProjectSnapshot:
    root = Path(os.path.abspath(workspace))
    root_metadata = root.lstat()
    root_directory = _ProjectDirectory(root, _identity(root_metadata))
    _require_directories((root_directory,))
    pending: list[tuple[_ProjectDirectory, ...]] = [(root_directory,)]
    files: list[EvidenceDigest] = []
    links: list[EvidenceDigest] = []
    budget = EvidenceBudget()
    while pending:
        directories = pending.pop()
        _require_directories(directories)
        directory = directories[-1].path
        for entry in directory.iterdir():
            _require_directories(directories)
            metadata = entry.lstat()
            relative_path = entry.relative_to(root).as_posix()
            budget.charge(
                Path(relative_path),
                metadata.st_size if stat.S_ISREG(metadata.st_mode) else 0,
            )
            if _is_link(metadata):
                target = os.readlink(entry).encode("utf-8", errors="surrogatepass")
                if not _identities_match(_identity(metadata), _identity(entry.lstat())):
                    raise OSError(f"project link changed during snapshot: {entry}")
                links.append(
                    EvidenceDigest(
                        relative_path=relative_path,
                        digest=Sha256Digest(hashlib.sha256(target).hexdigest()),
                        size_bytes=len(target),
                    )
                )
            elif stat.S_ISDIR(metadata.st_mode):
                pending.append(
                    (*directories, _ProjectDirectory(entry, _identity(metadata)))
                )
            elif stat.S_ISREG(metadata.st_mode):
                files.append(_digest_entry(entry, relative_path, directories))
            else:
                raise OSError(f"unsupported project entry: {relative_path}")
        _require_directories(directories)
    return ProjectSnapshot(
        files=tuple(sorted(files, key=lambda item: item.relative_path)),
        links=tuple(sorted(links, key=lambda item: item.relative_path)),
    )


def archive_migration_reports(
    workspace: Path,
    destination: Path,
) -> tuple[EvidenceDigest, ...]:
    source = workspace / "migration_reports"
    if source.exists():
        expected = digest_inventory(source, workspace)
        copy_real_tree(source, workspace, destination)
        current = digest_inventory(source, workspace)
        if current != expected:
            raise OSError("migration_reports changed during archival")
    else:
        destination.mkdir()
        expected = ()
    archived = digest_inventory(destination, destination.parent)
    if archived != expected:
        raise OSError("migration_reports archive digest mismatch")
    return archived


def write_exclusive_record(
    path: Path,
    record: JsonEvidenceRecord,
) -> EvidenceDigest:
    content = (record.model_dump_json(by_alias=True, indent=2) + "\n").encode("utf-8")
    atomic_create_bytes(path, content)
    return EvidenceDigest(
        relative_path=path.name,
        digest=Sha256Digest(hashlib.sha256(content).hexdigest()),
        size_bytes=len(content),
    )


def verify_record(path: Path, receipt: EvidenceDigest) -> bool:
    try:
        content = read_verified_bytes(path, MAX_EVIDENCE_FILE_BYTES)
    except OSError:
        return False
    return (
        path.name == receipt.relative_path
        and len(content) == receipt.size_bytes
        and hashlib.sha256(content).hexdigest() == receipt.digest
    )
