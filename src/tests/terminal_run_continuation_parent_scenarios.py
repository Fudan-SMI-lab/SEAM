from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TypeAlias

from pydantic import JsonValue

from core.run_manifest import Sha256Digest

SummaryPayload: TypeAlias = dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class ParentRun:
    summary_path: Path
    report_dir: Path
    reports_root: Path
    project_dir: Path
    workflow_path: Path
    run_manifest_path: Path
    workflow_digest: Sha256Digest


@dataclass(frozen=True, slots=True)
class ParentPhaseFixture:
    phase_id: str
    status: str


@dataclass(frozen=True, slots=True)
class ParentCanonicalFixture:
    phase_id: str
    value: dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class ParentRunScenario:
    workflow_bytes: bytes
    phases: tuple[ParentPhaseFixture, ...]
    canonical_outputs: tuple[ParentCanonicalFixture, ...]
