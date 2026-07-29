from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, unique
from pathlib import Path
import typing
from typing import TYPE_CHECKING, Literal, final

from typing_extensions import TypeAlias, override

from .resource_manifest_models import EnvironmentType, ResourceManifest

if TYPE_CHECKING:
    from .continuation_lock_context import ActiveProjectOwnerLock


@unique
class ParentPhase2State(str, Enum):
    FAILED_BEFORE_TARGET = "failed_before_target"
    TARGET_ESTABLISHED = "target_established"


@unique
class AnchorRelation(str, Enum):
    AT_OR_BEFORE_PHASE2 = "at_or_before_phase2"
    AFTER_PHASE2 = "after_phase2"


@unique
class ContinuationEnvironmentErrorKind(str, Enum):
    ENVIRONMENT_MISSING = "environment_missing"
    ENVIRONMENT_MISMATCH = "environment_mismatch"
    INTERPRETER_UNAVAILABLE = "interpreter_unavailable"
    BACKEND_MISMATCH = "backend_mismatch"
    CONTAINER_MISSING = "container_missing"
    CONTAINER_MISMATCH = "container_mismatch"
    OWNERSHIP_AMBIGUOUS = "ownership_ambiguous"


@final
class ContinuationEnvironmentError(Exception):
    __slots__ = ("kind", "field", "detail")

    def __init__(
        self,
        kind: ContinuationEnvironmentErrorKind,
        field: str,
        detail: str,
    ) -> None:
        super().__init__(kind, field, detail)
        self.kind = kind
        self.field = field
        self.detail = detail

    @override
    def __str__(self) -> str:
        return f"{self.kind.value}: {self.field}: {self.detail}"


@dataclass(frozen=True)
class BindMount:
    source: Path
    destination: str


@dataclass(frozen=True)
class ContainerRequirements:
    name: str
    workdir: str
    devices: tuple[str, ...]
    project_mount_destination: str


@dataclass(frozen=True)
class ContainerObservation:
    runtime: str
    container_id: str
    name: str
    running: bool
    image_identity: str
    image_reference: str
    workdir: str
    devices: tuple[str, ...]
    bind_mounts: tuple[BindMount, ...]
    ownership_token: str | None
    ownership_label: str | None


@dataclass(frozen=True)
class RetainedContainerProbeRequest:
    runtime: Literal["docker", "podman"]
    container_id: str
    expected_ownership_token_sha256: str | None
    expected_ownership_label: str | None


@dataclass(frozen=True)
class RetainedEnvironmentProbeRequest:
    interpreter_path: str
    runtime: Literal["docker", "podman"] | None = None
    container_id: str | None = None
    timeout_seconds: int = 30


@dataclass(frozen=True)
class EnvironmentFingerprint:
    environment_type: EnvironmentType
    namespace: str
    container_id: str | None
    interpreter_realpath: str
    sys_executable: str
    sys_prefix: str
    sys_base_prefix: str
    python_implementation: str
    python_version: str
    platform_system: str
    platform_architecture: str
    package_inventory_hash: str
    interpreter_available: bool
    interpreter_executable: bool

    def identity(self) -> tuple[str | None, ...]:
        return (
            self.environment_type.value,
            self.namespace,
            self.container_id,
            self.interpreter_realpath,
            self.sys_executable,
            self.sys_prefix,
            self.sys_base_prefix,
            self.python_implementation,
            self.python_version,
            self.platform_system,
            self.platform_architecture,
            self.package_inventory_hash,
        )


@dataclass(frozen=True)
class ContinuationEnvironmentRequest:
    resource_manifest: ResourceManifest
    output_project: Path
    target_environment_id: str | None
    parent_phase2_state: ParentPhase2State
    anchor_relation: AnchorRelation
    child_run_id: str
    child_lineage_root_run_id: str
    container_requirements: ContainerRequirements | None
    observed_container: ContainerObservation | None
    observed_environment: EnvironmentFingerprint | None
    owner_lock: ActiveProjectOwnerLock | None


@dataclass(frozen=True)
class ExistingContainerAttachment:
    mode: Literal["existing_container"]
    runtime: str
    container_id: str
    container_name: str
    owner_kind: Literal["framework", "user", "external"]
    original_owner_run_id: str | None
    lineage_root_run_id: str | None
    ownership_token: str | None
    ownership_label: str | None


@final
@dataclass(frozen=True)
class _FrameworkContainerDeleteProof:
    original_owner_run_id: str
    lineage_root_run_id: str
    ownership_token: str
    ownership_label: str


@dataclass(frozen=True)
class FrameworkContainerDeleteEligible:
    original_owner_run_id: str
    lineage_root_run_id: str
    ownership_token: str
    ownership_label: str
    _proof: _FrameworkContainerDeleteProof | None = field(
        default=None, repr=False, compare=False
    )


def _verified_framework_container_delete_eligibility(
    original_owner_run_id: str,
    lineage_root_run_id: str,
    ownership_token: str,
    ownership_label: str,
) -> FrameworkContainerDeleteEligible:
    return FrameworkContainerDeleteEligible(
        original_owner_run_id,
        lineage_root_run_id,
        ownership_token,
        ownership_label,
        _FrameworkContainerDeleteProof(
            original_owner_run_id,
            lineage_root_run_id,
            ownership_token,
            ownership_label,
        ),
    )


def framework_container_delete_eligibility_is_verified(
    eligibility: FrameworkContainerDeleteEligible,
) -> bool:
    proof = eligibility._proof
    return proof is not None and (
        eligibility.original_owner_run_id,
        eligibility.lineage_root_run_id,
        eligibility.ownership_token,
        eligibility.ownership_label,
    ) == (
        proof.original_owner_run_id,
        proof.lineage_root_run_id,
        proof.ownership_token,
        proof.ownership_label,
    )


@dataclass(frozen=True)
class ContainerDeleteForbidden:
    reason: str


if TYPE_CHECKING:
    ContainerDeletion: TypeAlias = (
        FrameworkContainerDeleteEligible | ContainerDeleteForbidden
    )
else:
    ContainerDeletion: TypeAlias = typing.Union[
        FrameworkContainerDeleteEligible, ContainerDeleteForbidden
    ]


@dataclass(frozen=True)
class RetainedEnvironmentEligible:
    environment: EnvironmentFingerprint
    attachment: ExistingContainerAttachment | None
    deletion: ContainerDeletion


@dataclass(frozen=True)
class Phase2EstablishmentEligible:
    reason: Literal["parent_failed_before_target_environment"] = (
        "parent_failed_before_target_environment"
    )
    attachment: ExistingContainerAttachment | None = None
    deletion: ContainerDeletion = field(
        default_factory=lambda: ContainerDeleteForbidden(
            "local environment has no container"
        )
    )


if TYPE_CHECKING:
    ContinuationEnvironmentEligibility: TypeAlias = (
        RetainedEnvironmentEligible | Phase2EstablishmentEligible
    )
else:
    ContinuationEnvironmentEligibility: TypeAlias = typing.Union[
        RetainedEnvironmentEligible, Phase2EstablishmentEligible
    ]
