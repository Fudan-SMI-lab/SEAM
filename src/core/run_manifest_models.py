from __future__ import annotations

import hashlib
import os
import typing
from enum import Enum, unique
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, ClassVar, Final, Literal, NewType, final

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError
from core.compat import Annotated, Self, override

from core.run_outcome import PhaseId, TerminalAnchor

RUN_MANIFEST_FILENAME: Final = "run-manifest.v1.json"
RUN_MANIFEST_SCHEMA: Final = "seam.run-manifest"
RUN_MANIFEST_SCHEMA_VERSION: Final = 1

RunId = NewType("RunId", str)
Sha256Digest = NewType("Sha256Digest", str)
_RunIdField = Annotated[
    RunId, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
]
_DigestField = Annotated[Sha256Digest, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
_SafeName = Annotated[
    str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
]


class _FrozenModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        frozen=True, extra="forbid", populate_by_name=True
    )


@unique
class ManifestErrorKind(str, Enum):
    DUPLICATE_RUN = "duplicate_run"
    MISSING_MANIFEST = "missing_manifest"
    MALFORMED = "malformed"
    WORKFLOW_MISMATCH = "workflow_mismatch"
    PARENT_MISMATCH = "parent_mismatch"
    READ_ONLY = "read_only"
    VERSION_MISMATCH = "version_mismatch"
    IMMUTABLE_FIELD = "immutable_field"
    EVIDENCE_MUTATION = "evidence_mutation"
    WRITE_INTERRUPTED = "write_interrupted"
    AUTHORITY_BOUNDARY = "authority_boundary"
    CONTAINMENT = "containment"
    CONCURRENT_WRITE = "concurrent_write"
    SEALED = "sealed"


@final
class RunManifestError(Exception):
    __slots__ = ("kind", "detail")

    kind: ManifestErrorKind
    detail: str

    def __init__(self, kind: ManifestErrorKind, detail: str) -> None:
        super().__init__(kind, detail)
        self.kind = kind
        self.detail = detail

    @override
    def __str__(self) -> str:
        return f"{self.kind.value}: {self.detail}"


def _canonical_storage(
    authoritative_root: Path, workspace_root: Path
) -> tuple[Path, Path, Sha256Digest]:
    try:
        workspace = workspace_root.resolve(strict=True)
        authority = authoritative_root.resolve(strict=False)
    except OSError as exc:
        raise RunManifestError(
            ManifestErrorKind.AUTHORITY_BOUNDARY,
            f"storage root is unavailable: {exc}",
        ) from exc
    if not workspace.is_dir():
        raise RunManifestError(
            ManifestErrorKind.AUTHORITY_BOUNDARY,
            "workspace root must be a directory",
        )
    if authority == workspace or workspace in authority.parents:
        raise RunManifestError(
            ManifestErrorKind.AUTHORITY_BOUNDARY,
            "authoritative root must be outside the mutable workspace",
        )
    identity = os.path.normcase(str(workspace)).encode()
    digest = Sha256Digest(hashlib.sha256(identity).hexdigest())
    return authority, workspace, digest


@final
class RunStorageContext:
    __slots__ = ("_authoritative_root", "_workspace_root", "_workspace_digest")

    _authoritative_root: Path
    _workspace_root: Path
    _workspace_digest: Sha256Digest

    def __init__(
        self,
        authoritative_root: Path,
        workspace_root: Path,
        workspace_digest: Sha256Digest,
    ) -> None:
        authority, workspace, expected_digest = _canonical_storage(
            authoritative_root, workspace_root
        )
        if workspace_digest != expected_digest:
            raise RunManifestError(
                ManifestErrorKind.AUTHORITY_BOUNDARY,
                "workspace digest does not match the physical workspace",
            )
        self._authoritative_root = authority
        self._workspace_root = workspace
        self._workspace_digest = expected_digest

    @property
    def authoritative_root(self) -> Path:
        return self._authoritative_root

    @property
    def workspace_root(self) -> Path:
        return self._workspace_root

    @property
    def workspace_digest(self) -> Sha256Digest:
        return self._workspace_digest

    def has_same_storage_as(self, other: RunStorageContext) -> bool:
        return (
            self.authoritative_root == other.authoritative_root
            and self.workspace_root == other.workspace_root
            and self.workspace_digest == other.workspace_digest
        )

    @classmethod
    def bind(cls, authoritative_root: Path, workspace_root: Path) -> RunStorageContext:
        authority, workspace, digest = _canonical_storage(
            authoritative_root, workspace_root
        )
        return cls(
            authoritative_root=authority,
            workspace_root=workspace,
            workspace_digest=digest,
        )


class EvidenceDigest(_FrozenModel):
    relative_path: str
    digest: _DigestField
    size_bytes: Annotated[int, Field(ge=0)]
    # Backward compatible file type field: records sealed before this field
    # existed are ordinary files (symlink entries are only written once the
    # recording path distinguishes them), so the default preserves loading of
    # old run-manifests while new records participate in tuple equality.
    kind: Literal["file", "link"] = "file"

    @field_validator("relative_path")
    @classmethod
    def require_safe_relative_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        path_is_safe = not (
            path.is_absolute() or not path.parts or ".." in path.parts or "\\" in value
        )
        if not path_is_safe:
            raise PydanticCustomError(
                "unsafe_relative_path",
                "relative path must remain inside sealed evidence",
            )
        return value


class CanonicalReference(_FrozenModel):
    phase_id: PhaseId
    artifact_name: _SafeName
    digest: _DigestField


class ResourceReference(_FrozenModel):
    kind: _SafeName
    reference_id: _SafeName


class SharedWorkspaceMarker(_FrozenModel):
    kind: Literal["lineage_shared_mutable"] = "lineage_shared_mutable"
    workspace_digest: _DigestField


class RunManifest(_FrozenModel):
    schema_id: Literal["seam.run-manifest"] = Field(
        default=RUN_MANIFEST_SCHEMA, alias="schema", serialization_alias="schema"
    )
    schema_version: Literal[1] = RUN_MANIFEST_SCHEMA_VERSION
    run_id: _RunIdField
    if TYPE_CHECKING:
        parent_run_id: _RunIdField | None
        inherited_canonical: tuple[CanonicalReference, ...]
        resource_references: tuple[ResourceReference, ...]
        parent_evidence_digests: tuple[EvidenceDigest, ...]
        sealed_evidence: tuple[EvidenceDigest, ...]
    else:
        parent_run_id: typing.Optional[_RunIdField]
        inherited_canonical: typing.Tuple[CanonicalReference, ...]
        resource_references: typing.Tuple[ResourceReference, ...]
        parent_evidence_digests: typing.Tuple[EvidenceDigest, ...]
        sealed_evidence: typing.Tuple[EvidenceDigest, ...]
    lineage_root_run_id: _RunIdField
    revision: Annotated[int, Field(ge=1)]
    terminal_anchor: TerminalAnchor
    workflow_digest: _DigestField
    shared_workspace: SharedWorkspaceMarker
    evidence_sealed: bool

    @model_validator(mode="after")
    def require_consistent_lineage(self) -> Self:
        is_root = self.parent_run_id is None
        if is_root and self.lineage_root_run_id != self.run_id:
            raise PydanticCustomError(
                "invalid_lineage", "a root run must be its own lineage root"
            )
        if is_root and self.parent_evidence_digests:
            raise PydanticCustomError(
                "invalid_lineage", "a root run cannot inherit parent evidence"
            )
        if self.parent_run_id == self.run_id:
            raise PydanticCustomError(
                "invalid_lineage", "a child run cannot be its own parent"
            )
        if not self.evidence_sealed and self.sealed_evidence:
            raise PydanticCustomError(
                "invalid_seal", "unsealed evidence cannot carry a digest inventory"
            )
        paths = tuple(item.relative_path for item in self.sealed_evidence)
        if len(paths) != len(set(paths)):
            raise PydanticCustomError(
                "duplicate_evidence", "sealed evidence paths must be unique"
            )
        return self
