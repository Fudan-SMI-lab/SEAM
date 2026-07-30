from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, unique
from pathlib import Path
from typing import ClassVar, Optional, Tuple

from pydantic import BaseModel, ConfigDict, StringConstraints
from typing_extensions import Annotated

from core.phase5_attempt_receipt import Phase5AttemptAuthority
from core.resource_manifest import (
    FactProvenance,
    FactStatus,
    ResourceManifestStore,
)
from core.replay import ReplayUnavailableReason
from core.run_outcome import AcceptedAttemptId, RunOutcome, TerminalOutcome

BoundedReportText = Annotated[str, StringConstraints(max_length=4096)]


class _FrozenModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")


class RuntimeFact(_FrozenModel):
    name: str
    value: Optional[str]
    provenance: FactProvenance
    namespace: str
    status: FactStatus
    detail: Optional[str] = None


class RuntimeEnvironmentReport(_FrozenModel):
    environment_id: str
    facts: Tuple[RuntimeFact, ...]


@unique
class RuntimeAccessKind(str, Enum):
    UNAVAILABLE = "unavailable"
    BASE_ENVIRONMENT = "base_environment"
    PROJECT_VENV = "project_venv"
    RETAINED_CONTAINER = "retained_container"


class RuntimeAccessReport(_FrozenModel):
    available: bool
    kind: RuntimeAccessKind
    entry_command: Optional[str] = None
    activation_command: Optional[str] = None
    detail: str
    provenance: Optional[FactProvenance] = None


class RuntimeReplayReport(_FrozenModel):
    available: bool
    reason: Optional[ReplayUnavailableReason]
    accepted_attempt_id: Optional[AcceptedAttemptId]
    validation_command: Optional[BoundedReportText]
    command: Optional[BoundedReportText]
    cwd: Optional[BoundedReportText]
    nondeterminism_notice: BoundedReportText
    auto_execute: bool = False


@unique
class RuntimeOutcomeStatus(str, Enum):
    UNAVAILABLE = "unavailable"
    PASSED = TerminalOutcome.PASSED.value
    PASSED_WITH_REVIEWS = TerminalOutcome.PASSED_WITH_REVIEWS.value
    FAILED = TerminalOutcome.FAILED.value


class V3RuntimeReport(_FrozenModel):
    schema_version: str = "1.0"
    manifest_path: Optional[str]
    outcome_status: RuntimeOutcomeStatus
    execution: Tuple[RuntimeFact, ...]
    launcher: Tuple[RuntimeFact, ...]
    environments: Tuple[RuntimeEnvironmentReport, ...]
    active_environment_id: Optional[str]
    container: Tuple[RuntimeFact, ...]
    retention: Tuple[RuntimeFact, ...]
    opencode: Tuple[RuntimeFact, ...]
    access: RuntimeAccessReport
    replay: RuntimeReplayReport
    diagnostics: Tuple[str, ...] = ()


@dataclass(frozen=True)
class AcceptedReplaySource:
    receipt_path: Path
    authority: Phase5AttemptAuthority


@dataclass(frozen=True)
class RuntimeReportRequest:
    manifest_store: ResourceManifestStore | None
    outcome: RunOutcome | None
    expected_run_id: str
    accepted_receipt: AcceptedReplaySource | None
