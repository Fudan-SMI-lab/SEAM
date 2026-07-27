from __future__ import annotations

from enum import Enum, unique
from pathlib import Path
from typing import ClassVar, Literal, NamedTuple, final

from pydantic import BaseModel, ConfigDict, Field
from typing_extensions import override

from .artifact_store import ArtifactStore
from .continuation_models import ContinuationRequest, ResolvedTerminalParent
from .run_manifest import (
    CanonicalReference,
    EvidenceDigest,
    RunManifest,
    RunManifestStore,
    RunStorageContext,
)


class _FrozenModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")


@unique
class ContinuationEvidenceErrorKind(str, Enum):
    PARENT_EVIDENCE_DRIFT = "parent_evidence_drift"
    NAMESPACE_EXISTS = "namespace_exists"
    NAMESPACE_FAILED = "namespace_failed"
    SNAPSHOT_FAILED = "snapshot_failed"
    ARCHIVE_FAILED = "archive_failed"
    INHERITED_REFERENCE_INVALID = "inherited_reference_invalid"
    WORKING_NAMESPACE_EXISTS = "working_namespace_exists"
    CHILD_EVIDENCE_DRIFT = "child_evidence_drift"


@final
class ContinuationEvidenceError(Exception):
    __slots__ = ("kind", "detail")

    def __init__(self, kind: ContinuationEvidenceErrorKind, detail: str) -> None:
        super().__init__(kind, detail)
        self.kind = kind
        self.detail = detail

    @override
    def __str__(self) -> str:
        return f"{self.kind.value}: {self.detail}"


class ChildEvidenceRequest(_FrozenModel):
    continuation: ContinuationRequest
    inherited_canonical: tuple[CanonicalReference, ...]


class ProjectBaseline(_FrozenModel):
    schema_id: Literal["seam.continuation-project-baseline"] = Field(
        default="seam.continuation-project-baseline",
        alias="schema",
        serialization_alias="schema",
    )
    schema_version: Literal[1] = 1
    parent_run_id: str
    child_run_id: str
    workspace_kind: Literal["lineage_shared_mutable"] = "lineage_shared_mutable"
    complete: Literal[True] = True
    files: tuple[EvidenceDigest, ...]
    links: tuple[EvidenceDigest, ...]


class MigrationReportArchive(_FrozenModel):
    schema_id: Literal["seam.continuation-migration-report-archive"] = Field(
        default="seam.continuation-migration-report-archive",
        alias="schema",
        serialization_alias="schema",
    )
    schema_version: Literal[1] = 1
    parent_run_id: str
    child_run_id: str
    source: Literal["migration_reports"] = "migration_reports"
    complete: Literal[True] = True
    files: tuple[EvidenceDigest, ...]


class ContinuationEvidenceRoot(_FrozenModel):
    schema_id: Literal["seam.continuation-evidence-root"] = Field(
        default="seam.continuation-evidence-root",
        alias="schema",
        serialization_alias="schema",
    )
    schema_version: Literal[1] = 1
    child_run_id: str
    complete: Literal[True] = True
    precontinuation_files: tuple[EvidenceDigest, ...]
    trace_files: tuple[EvidenceDigest, ...]


class ProjectSnapshot(NamedTuple):
    files: tuple[EvidenceDigest, ...]
    links: tuple[EvidenceDigest, ...]


class ChildEvidenceNamespace(NamedTuple):
    report_dir: Path
    trace_dir: Path
    artifact_dir: Path
    precontinuation_dir: Path
    baseline_path: Path
    migration_archive_dir: Path
    migration_archive_manifest_path: Path


class PreparedChildEvidence(NamedTuple):
    request: ChildEvidenceRequest
    parent: ResolvedTerminalParent
    context: RunStorageContext
    parent_store: RunManifestStore
    child_store: RunManifestStore
    artifact_store: ArtifactStore
    namespace: ChildEvidenceNamespace
    project_baseline: ProjectBaseline
    migration_archive: MigrationReportArchive
    baseline_receipt: EvidenceDigest
    archive_manifest_receipt: EvidenceDigest
    parent_report_inventory: tuple[EvidenceDigest, ...]


class VerifiedChildEvidence(NamedTuple):
    namespace: ChildEvidenceNamespace
    parent_manifest: RunManifest
    child_manifest: RunManifest
