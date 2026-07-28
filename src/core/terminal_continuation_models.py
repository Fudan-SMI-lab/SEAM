from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum, unique
from pathlib import Path
from typing import Final

from typing_extensions import override

from .continuation_environment import ContinuationEnvironmentEligibility
from .continuation_evidence import PreparedChildEvidence
from .continuation_hydration_models import ContinuationHydration
from .continuation_hydration_models import ParentAcceptedAttemptReference
from .continuation_models import ResolvedTerminalParent
from .review_policy import ReviewCliOverrides
from .resource_retention import ContainerRetention
from .types import WorkflowDefinition

_MAX_PROMPT_CONTEXT_BYTES: Final = 8192


@unique
class TerminalContinuationErrorKind(str, Enum):
    ACCEPTED_ATTEMPT_AMBIGUOUS = "accepted_attempt_ambiguous"
    ACCEPTED_ATTEMPT_MALFORMED = "accepted_attempt_malformed"
    RESOURCE_CONTEXT_AMBIGUOUS = "resource_context_ambiguous"
    PROMPT_CONTEXT_TOO_LARGE = "prompt_context_too_large"


@dataclass(frozen=True, slots=True)
class TerminalContinuationError(Exception):
    kind: TerminalContinuationErrorKind
    detail: str

    @override
    def __str__(self) -> str:
        return f"{self.kind.value}: {self.detail}"


@dataclass(frozen=True, slots=True)
class ContinuationPromptFacts:
    parent_run_id: str
    child_run_id: str
    anchor_phase_id: str
    inherited_phase_ids: tuple[str, ...]
    resource_eligibility: str
    attachment_mode: str

    def render(self) -> str:
        payload = {
            "schema": "seam.continuation-context",
            "version": 1,
            "lineage": {
                "parent_run_id": self.parent_run_id,
                "child_run_id": self.child_run_id,
            },
            "anchor_phase_id": self.anchor_phase_id,
            "inherited_phase_ids": list(self.inherited_phase_ids),
            "resource_eligibility": self.resource_eligibility,
            "attachment_mode": self.attachment_mode,
        }
        rendered = json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
        if len(rendered.encode("utf-8")) > _MAX_PROMPT_CONTEXT_BYTES:
            raise TerminalContinuationError(
                TerminalContinuationErrorKind.PROMPT_CONTEXT_TOO_LARGE,
                "typed continuation context exceeds the prompt bound",
            )
        return f"SEAM_CONTINUATION_CONTEXT={rendered}"


@dataclass(frozen=True, slots=True)
class PreparedTerminalContinuation:
    parent: ResolvedTerminalParent
    workflow: WorkflowDefinition
    hydration: ContinuationHydration
    eligibility: ContinuationEnvironmentEligibility
    evidence: PreparedChildEvidence
    prompt_facts: ContinuationPromptFacts


@dataclass(frozen=True, slots=True)
class V3ServerRunOptions:
    base_url: str | None
    auto_start: bool
    port: int


@dataclass(frozen=True, slots=True)
class V3ReviewRunOptions:
    max_phase5_iter: int
    enabled: bool
    overrides: ReviewCliOverrides | None


@dataclass(frozen=True, slots=True)
class V3InvocationOptions:
    keep_temp_dir: bool
    agent_name: str | None
    user_constraints: str
    framework_config_path: str | None
    container_retention: ContainerRetention = ContainerRetention.RETAIN


@dataclass(frozen=True, slots=True)
class V3OpenCodeOptions:
    readiness: str
    message_timeout: int


@dataclass(frozen=True, slots=True)
class TerminalContinuationRunRequest:
    summary_path: Path
    server: V3ServerRunOptions
    review: V3ReviewRunOptions
    invocation: V3InvocationOptions
    opencode: V3OpenCodeOptions


@dataclass(frozen=True, slots=True)
class TerminalEnvironmentVerificationRequest:
    parent: ResolvedTerminalParent
    hydration: ContinuationHydration
    workflow: WorkflowDefinition
    child_run_id: str
    parent_accepted_attempt: ParentAcceptedAttemptReference | None
