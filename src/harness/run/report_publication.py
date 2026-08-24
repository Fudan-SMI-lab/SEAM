"""Publish validated Phase 6 reports into the migrated project."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

from core.run_outcome import TerminalOutcome

from .models import EMPTY_ARTIFACT_UPDATE, RunArtifactUpdate


class PhaseOutputStore(Protocol):
    artifact_dir: str

    def load_phase_output(self, phase_id: str) -> dict[str, Any] | None: ...


class ReportPublicationError(RuntimeError):
    """Raised when a passing run cannot publish its user-facing reports."""


@dataclass(frozen=True)
class PublishedReport:
    name: str
    source: str
    destination: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class ReportPublisher:
    artifact_store: PhaseOutputStore
    project_dir: Path

    def __call__(self, outcome: TerminalOutcome) -> RunArtifactUpdate:
        if outcome not in {
            TerminalOutcome.PASSED,
            TerminalOutcome.PASSED_WITH_REVIEWS,
        }:
            return EMPTY_ARTIFACT_UPDATE
        destination = self.publish()
        return RunArtifactUpdate(
            directory_paths=(("migration_reports_dir", str(destination)),)
        )

    def publish(self) -> Path:
        phase_output = self.artifact_store.load_phase_output("phase_6_report")
        if not isinstance(phase_output, dict):
            raise ReportPublicationError(
                "Validated phase_6_report output is unavailable"
            )
        raw_paths = phase_output.get("report_paths")
        if not isinstance(raw_paths, list) or not raw_paths:
            raise ReportPublicationError("Phase 6 did not provide report paths")

        source_root = (
            Path(self.artifact_store.artifact_dir).resolve() / "reports"
        ).resolve()
        sources: list[Path] = []
        seen_names: set[str] = set()
        for raw_path in raw_paths:
            if not isinstance(raw_path, str) or not raw_path.strip():
                raise ReportPublicationError("Phase 6 contains an invalid report path")
            raw_source = Path(raw_path).expanduser()
            if raw_source.is_symlink():
                raise ReportPublicationError(
                    f"Canonical report must not be a symlink: {raw_source}"
                )
            source = raw_source.resolve()
            if not source.is_relative_to(source_root):
                raise ReportPublicationError(
                    f"Report path escapes the canonical report directory: {source}"
                )
            if not source.is_file():
                raise ReportPublicationError(
                    f"Canonical report is missing or not a regular file: {source}"
                )
            if source.name in seen_names:
                raise ReportPublicationError(
                    f"Phase 6 contains duplicate report name: {source.name}"
                )
            seen_names.add(source.name)
            sources.append(source)

        if "SUMMARY_REPORT.md" not in seen_names:
            raise ReportPublicationError(
                "Phase 6 report bundle is missing SUMMARY_REPORT.md"
            )

        project_root = self.project_dir.resolve()
        destination_root = project_root / "migration_reports"
        if destination_root.is_symlink():
            raise ReportPublicationError(
                f"Migration report directory must not be a symlink: {destination_root}"
            )
        destination_root.mkdir(parents=True, exist_ok=True)
        if not destination_root.resolve().is_relative_to(project_root):
            raise ReportPublicationError(
                f"Migration report directory escapes the project: {destination_root}"
            )
        published: list[PublishedReport] = []
        for source in sources:
            destination = destination_root / source.name
            _atomic_copy(source, destination)
            published.append(
                PublishedReport(
                    name=source.name,
                    source=str(source),
                    destination=str(destination),
                    size_bytes=destination.stat().st_size,
                    sha256=_sha256(destination),
                )
            )

        manifest_path = destination_root / "report_manifest.json"
        _atomic_write_json(
            manifest_path,
            {
                "schema_version": "1.0",
                "canonical_report_dir": str(source_root),
                "published_report_dir": str(destination_root),
                "reports": [asdict(report) for report in published],
            },
        )
        return destination_root


def _atomic_copy(source: Path, destination: Path) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as target, source.open("rb") as source_file:
            shutil.copyfileobj(source_file, target)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary_name, destination)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def _atomic_write_json(path: Path, value: object) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
