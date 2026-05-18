"""Core type definitions and data models for migration_utils."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


@dataclass
class RuntimeSkillsConfig:
    """Explicit runtime skill configuration declared in workflow YAML."""

    include: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    merge: str = "append"
    missing: str = "warn"
    inject_full: bool = False
    exclude_dynamic_duplicates: bool = True


class SessionLifecycle(str, Enum):
    """Lifecycle modes for OpenCode sessions."""

    PERSISTENT = "persistent"
    REUSABLE = "reusable"
    EPHEMERAL = "ephemeral"


@dataclass
class SessionRecord:
    """Record of a managed OpenCode session."""

    role: str
    session_id: str
    agent: str
    lifecycle: SessionLifecycle
    created_at: float


@dataclass
class PhaseDefinition:
    """Declarative definition of a workflow phase."""

    id: str
    name: str
    prompt_template: str
    output_schema: dict[str, object] | str
    validator: object | None = None
    transitions: dict[str, str] = field(default_factory=dict)
    type: str = "llm"
    agent: str | None = None
    timeout: int | None = None
    condition: str | None = None
    input_mapping: dict[str, str] = field(default_factory=dict)
    output_as: str | None = None
    max_iterations: int | None = None
    sub_workflow: str | None = None
    validate_only: bool = False
    hooks: PhaseHooks | None = None
    transition: TransitionDefinition | None = None
    on_failure: str = "continue"
    handler: str | None = None             # For orchestration type handler path
    retrieve_experience: bool = False      # Enable experience retrieval for this phase
    experience_query: dict[str, Any] | None = None   # Query configuration for experience retrieval
    runtime_skills: RuntimeSkillsConfig | None = None
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class PhaseResult:
    """Outcome of executing a single phase."""

    status: str
    failure_kind: str | None = None


@dataclass
class RepairContext:
    """Tracking state for the iterative repair loop in Phase 5."""

    repair_role: str
    max_iterations: int
    iteration_count: int = 0
    last_error: str | None = None
    history: list[object] = field(default_factory=list)


@dataclass
class WorkflowDefinition:
    """Top-level workflow descriptor loaded from YAML."""

    name: str
    version: str | int
    description: str = ""
    globals: dict[str, object] | None = None
    phases: list[PhaseDefinition] = field(default_factory=list)
    terminals: list[str] = field(default_factory=list)
    agents: dict[str, dict[str, Any]] = field(default_factory=dict)
    sub_workflows: dict[str, SubWorkflowDefinition] = field(default_factory=dict)
    hooks: dict[str, list[HookDefinition]] = field(default_factory=dict)


class PhaseType(str, Enum):
    """Enum of supported phase execution types."""

    LLM = "llm"
    SHELL = "shell"
    BUILTIN = "builtin"
    PYTHON = "python"
    REVIEW = "review"
    DISPATCH = "dispatch"
    LOOP = "loop"


@dataclass
class PhaseHooks:
    """Hook callbacks for phase lifecycle events."""

    pre_execute: list[str] = field(default_factory=list)
    post_execute: list[str] = field(default_factory=list)
    on_error: list[str] = field(default_factory=list)


@dataclass
class HookResult:
    """Result of executing a single hook."""

    operation: str
    success: bool
    context: dict[str, Any] = field(default_factory=dict)


@dataclass
class HookDefinition:
    """Declarative hook configuration."""

    operation: str
    type: str = "builtin"
    save_as: str | None = None
    critical: bool = False
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class TransitionDefinition:
    """Fine-grained transition rules for a phase outcome."""

    on_success: str | None = None
    on_failure: str | None = None
    on_skip: str | None = None


@dataclass
class SubWorkflowDefinition:
    """A sub-workflow e.g. loop body, used inside a parent workflow."""

    id: str
    type: str = "loop"
    max_iterations: int | str = 5
    stagnation_threshold: int | str = 3
    review_gate_enabled: bool | str = False
    max_review_iterations: int | str = 3
    stop_conditions: list[dict[str, Any]] = field(default_factory=list)
    phases: list[dict[str, Any]] = field(default_factory=list)
    blocks: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class LoopState:
    """Tracking state for the iterative loop sub-workflow execution."""

    stagnation_count: int = 0
    review_reject_count: int = 0
    last_error_signature: str = ""
    iteration: int = 0
    status: str = "running"
    exit_code: int | None = None
    variables: dict[str, object] = field(default_factory=dict)
