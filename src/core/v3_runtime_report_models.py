from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, unique
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from core.phase5_attempt_receipt import Phase5AttemptAuthority
from core.resource_manifest import (
    FactProvenance,
    FactStatus,
    ResourceManifestStore,
)
from core.run_outcome import RunOutcome


class _FrozenModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")


class RuntimeFact(_FrozenModel):
    name: str
    value: str | None
    provenance: FactProvenance
    namespace: str
    status: FactStatus
    detail: str | None = None


class RuntimeEnvironmentReport(_FrozenModel):
    environment_id: str
    facts: tuple[RuntimeFact, ...]


@unique
class RuntimeAccessKind(str, Enum):
    UNAVAILABLE = "unavailable"
    BASE_ENVIRONMENT = "base_environment"
    PROJECT_VENV = "project_venv"
    RETAINED_CONTAINER = "retained_container"


class RuntimeAccessReport(_FrozenModel):
    available: bool
    kind: RuntimeAccessKind
    entry_command: str | None = None
    activation_command: str | None = None
    detail: str
    provenance: FactProvenance | None = None


class RuntimeReplayReport(_FrozenModel):
    available: bool
    reason: str | None
    accepted_attempt_id: str | None
    validation_command: str | None
    command: str | None
    cwd: str | None
    nondeterminism_notice: str
    auto_execute: bool = False


class V3RuntimeReport(_FrozenModel):
    schema_version: str = "1.0"
    manifest_path: str | None
    outcome_status: str
    execution: tuple[RuntimeFact, ...]
    launcher: tuple[RuntimeFact, ...]
    environments: tuple[RuntimeEnvironmentReport, ...]
    active_environment_id: str | None
    container: tuple[RuntimeFact, ...]
    retention: tuple[RuntimeFact, ...]
    opencode: tuple[RuntimeFact, ...]
    access: RuntimeAccessReport
    replay: RuntimeReplayReport
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AcceptedReplaySource:
    receipt_path: Path
    authority: Phase5AttemptAuthority


@dataclass(frozen=True, slots=True)
class RuntimeReportRequest:
    manifest_store: ResourceManifestStore | None
    outcome: RunOutcome | None
    expected_run_id: str
    accepted_receipt: AcceptedReplaySource | None
