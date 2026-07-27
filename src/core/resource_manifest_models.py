from __future__ import annotations

import re
import typing
from enum import Enum, unique
from typing import TYPE_CHECKING, ClassVar, Final, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError
from typing_extensions import Annotated, Self, override

RESOURCE_MANIFEST_FILENAME: Final = "resource-manifest.v1.json"
RESOURCE_MANIFEST_SCHEMA: Final = "seam.resource-manifest"
RESOURCE_MANIFEST_SCHEMA_VERSION: Final = 1
_SAFE_NAMESPACE = re.compile(r"(?:host|container:[A-Za-z0-9][A-Za-z0-9_.-]{0,127})\Z")
_SafeId = Annotated[
    str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
]
_FactName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_.-]{0,63}$")]
_Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
_BoundedText = Annotated[str, StringConstraints(min_length=1, max_length=1024)]
_Detail = Annotated[str, StringConstraints(min_length=1, max_length=500)]


class _FrozenModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")


@unique
class FactProvenance(str, Enum):
    CONFIGURED = "configured"
    FRAMEWORK_OBSERVED = "framework_observed"
    AGENT_REPORTED = "agent_reported"
    DERIVED = "derived"


@unique
class FactStatus(str, Enum):
    KNOWN = "known"
    UNKNOWN = "unknown"
    ERROR = "error"


@unique
class EnvironmentType(str, Enum):
    BASE = "base"
    PROJECT_VENV = "project_venv"


@unique
class ResourceManifestErrorKind(str, Enum):
    DUPLICATE_MANIFEST = "duplicate_manifest"
    DUPLICATE_FACT = "duplicate_fact"
    MISSING_MANIFEST = "missing_manifest"
    MALFORMED = "malformed"
    SCHEMA_MISMATCH = "schema_mismatch"
    VERSION_MISMATCH = "version_mismatch"
    RUN_CONTEXT_MISMATCH = "run_context_mismatch"
    DIGEST_MISMATCH = "digest_mismatch"
    STALE_WRITE = "stale_write"
    SEALED = "sealed"
    PROVENANCE_ESCALATION = "provenance_escalation"
    UNSAFE_PATH = "unsafe_path"
    WRITE_INTERRUPTED = "write_interrupted"
    CONCURRENT_WRITE = "concurrent_write"
    AUTHORITY_MISMATCH = "authority_mismatch"


class ResourceManifestError(Exception):
    __slots__ = ("kind", "detail")

    def __init__(self, kind: ResourceManifestErrorKind, detail: str) -> None:
        super().__init__(kind, detail)
        self.kind = kind
        self.detail = detail

    @override
    def __str__(self) -> str:
        return f"{self.kind.value}: {self.detail}"


class ProvenanceFact(_FrozenModel):
    name: _FactName
    if TYPE_CHECKING:
        value: typing.Optional[_BoundedText] = None
        detail: typing.Optional[_Detail] = None
    else:
        value: typing.Optional[_BoundedText] = None
        detail: typing.Optional[_Detail] = None
    provenance: FactProvenance
    namespace: str
    status: FactStatus = FactStatus.KNOWN
    if TYPE_CHECKING:
        authority_tag: typing.Optional[_Digest] = None
    else:
        authority_tag: typing.Optional[_Digest] = None

    @field_validator("namespace")
    @classmethod
    def require_exact_namespace(cls, value: str) -> str:
        if _SAFE_NAMESPACE.fullmatch(value) is None:
            raise PydanticCustomError(
                "unsafe_namespace", "namespace must be host or container:<safe-id>"
            )
        return value

    @model_validator(mode="after")
    def require_consistent_status(self) -> Self:
        if self.status is FactStatus.KNOWN and self.value is None:
            raise PydanticCustomError("missing_value", "known facts require a value")
        if self.status is not FactStatus.KNOWN and self.value is not None:
            raise PydanticCustomError(
                "unexpected_value", "non-known facts have no value"
            )
        if self.status is FactStatus.ERROR and self.detail is None:
            raise PydanticCustomError("missing_error", "error facts require detail")
        return self


class EnvironmentRecord(_FrozenModel):
    environment_id: _SafeId
    facts: Annotated[
        typing.Tuple[ProvenanceFact, ...], Field(min_length=1, max_length=32)
    ]

    @model_validator(mode="after")
    def reject_duplicate_fact_sources(self) -> Self:
        keys = tuple(
            (fact.name, fact.provenance, fact.namespace) for fact in self.facts
        )
        if len(keys) != len(set(keys)):
            raise PydanticCustomError(
                "duplicate_fact", "environment facts must be unique"
            )
        return self


class ProbeReceipt(_FrozenModel):
    probe_id: _SafeId
    environment_id: _SafeId
    namespace: str
    status: FactStatus
    verified_facts: Annotated[typing.Tuple[ProvenanceFact, ...], Field(max_length=16)]
    if TYPE_CHECKING:
        detail: typing.Optional[_Detail] = None
        authority_tag: typing.Optional[_Digest] = None
    else:
        detail: typing.Optional[_Detail] = None
        authority_tag: typing.Optional[_Digest] = None

    @field_validator("namespace")
    @classmethod
    def require_exact_namespace(cls, value: str) -> str:
        if _SAFE_NAMESPACE.fullmatch(value) is None:
            raise PydanticCustomError(
                "unsafe_namespace", "probe receipt requires an exact namespace"
            )
        return value

    @model_validator(mode="after")
    def require_observed_receipt(self) -> Self:
        if len(self.verified_facts) != len(set(self.verified_facts)):
            raise PydanticCustomError("duplicate_fact", "receipt facts must be unique")
        valid = all(
            fact.provenance is FactProvenance.FRAMEWORK_OBSERVED
            and fact.namespace == self.namespace
            and fact.status is self.status
            for fact in self.verified_facts
        )
        if not valid:
            raise PydanticCustomError(
                "invalid_receipt", "receipt facts must be observed"
            )
        if self.status is FactStatus.UNKNOWN and self.verified_facts:
            raise PydanticCustomError(
                "invalid_receipt", "unknown probes cannot verify observed facts"
            )
        if self.status is not FactStatus.UNKNOWN and not self.verified_facts:
            raise PydanticCustomError(
                "invalid_receipt", "completed probes must record observed facts"
            )
        if self.status is FactStatus.ERROR and self.detail is None:
            raise PydanticCustomError("missing_error", "failed probes require detail")
        return self


class Phase5EnvironmentReference(_FrozenModel):
    attempt_id: _SafeId
    environment_reference: ProvenanceFact


class ResourceManifestIdentity(_FrozenModel):
    run_id: _SafeId
    workflow_digest: _Digest
    workspace_digest: _Digest


class ResourceManifest(_FrozenModel):
    schema_id: Literal["seam.resource-manifest"] = Field(
        default=RESOURCE_MANIFEST_SCHEMA, alias="schema", serialization_alias="schema"
    )
    schema_version: Literal[1] = RESOURCE_MANIFEST_SCHEMA_VERSION
    run_id: _SafeId
    workflow_digest: _Digest
    workspace_digest: _Digest
    revision: Annotated[int, Field(ge=1)]
    sealed: bool
    facts: Annotated[typing.Tuple[ProvenanceFact, ...], Field(max_length=96)]
    environments: Annotated[
        typing.Tuple[EnvironmentRecord, ...], Field(max_length=8)
    ] = ()
    phase5_environment_references: Annotated[
        typing.Tuple[Phase5EnvironmentReference, ...], Field(max_length=32)
    ] = ()
    probe_receipts: Annotated[
        typing.Tuple[ProbeReceipt, ...], Field(max_length=32)
    ] = ()


class ResourceManifestUpdate(_FrozenModel):
    expected_revision: Annotated[int, Field(ge=0)]
    facts: Annotated[typing.Tuple[ProvenanceFact, ...], Field(max_length=32)] = ()
    environments: Annotated[
        typing.Tuple[EnvironmentRecord, ...], Field(max_length=8)
    ] = ()
    phase5_environment_references: Annotated[
        typing.Tuple[Phase5EnvironmentReference, ...], Field(max_length=16)
    ] = ()
    probe_receipts: Annotated[
        typing.Tuple[ProbeReceipt, ...], Field(max_length=16)
    ] = ()
