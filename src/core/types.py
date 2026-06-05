"""Core type definitions and data models for migration_utils."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .platform_policy import TargetPlatformConfig

DEFAULT_CONTAINER_WORKDIR = "/workspace"


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
class ExecutionBackendConfig:
    """Optional execution-backend configuration parsed from workflow YAML.

    When *mode* is ``"local"`` (the default), all fields beyond ``mode``
    are ignored and commands run via local ``subprocess``.

    When *mode* is ``"container"``, the ``source`` field distinguishes between
    creating a new container from an image versus attaching to an already
    running container.

    The ``images`` field accepts a list of candidate images for sequential
    creation fallback (``mode: container``) or agent selection (``mode: auto``).
    For backward compatibility, a single ``image`` string is stored as
    ``images=[image]`` when ``images`` is not explicitly provided.
    """

    mode: str = "local"                           # local | container | auto
    source: str = "image"                         # image | existing_container
    runtime: str = "docker"                       # docker | podman
    image: str | None = None                      # source=image only (single, legacy)
    images: list[str] | None = None               # source=image only (list, new)
    container_name: str | None = None             # source=existing_container only
    container_name_prefix: str = "seam-migration"
    devices: list[str] = field(default_factory=list)
    volumes: list[str] = field(default_factory=list)
    env_vars: dict[str, str] = field(default_factory=dict)
    required_env_vars: list[str] = field(default_factory=list)
    required_devices: list[str] = field(default_factory=list)
    container_workdir: str = DEFAULT_CONTAINER_WORKDIR
    network_mode: str | None = None
    runtime_flags: list[str] = field(default_factory=list)
    timeout: int = 7200
    cleanup: bool = True

    @classmethod
    def from_dict(cls, raw: dict[str, object] | None) -> "ExecutionBackendConfig":
        """Build an instance from a parsed YAML dict.

        Returns a local-mode config when *raw* is ``None`` or empty.
        Raises ``ValueError`` for unrecognised *mode* / *source* values or
        when *container_name* is missing for ``source=existing_container``.
        """
        if not raw:
            return cls(mode="local")

        mode = str(raw.get("mode", "local"))
        if mode not in ("local", "container", "auto"):
            raise ValueError(f"Invalid execution_backend.mode: {mode!r}")

        if mode == "local":
            return cls(mode="local")

        source = str(raw.get("source", "image"))
        if source not in ("image", "existing_container"):
            raise ValueError(f"Invalid execution_backend.source: {source!r}")

        if source == "existing_container" and not raw.get("container_name"):
            raise ValueError(
                "execution_backend.container_name is required when source=existing_container"
            )

        # Normalize image candidates with robust type handling.
        # Priority: explicit "images" list > single "image" (string or list).
        raw_images = raw.get("images")
        raw_single = raw.get("image")

        def _normalize_image_value(value: object) -> list[str]:
            """Convert a YAML value to a list of non-empty image strings."""
            if value is None:
                return []
            if isinstance(value, list):
                return [
                    str(item).strip()
                    for item in value
                    if str(item).strip() and str(item).strip() != "None"
                ]
            s = str(value).strip()
            if s and s != "None":
                return [s]
            return []

        # Explicit images list wins over legacy image for candidate resolution.
        resolved_images = _normalize_image_value(raw_images)
        fallback_images = _normalize_image_value(raw_single)

        # If neither was provided, images stays None.
        resolved_images_final: list[str] | None = resolved_images or fallback_images or None

        return cls(
            mode=mode,
            source=source,
            runtime=str(raw.get("runtime", "docker")),
            image=resolved_images_final[0] if resolved_images_final else None,
            images=resolved_images_final,
            container_name=raw.get("container_name"),
            container_name_prefix=str(raw.get("container_name_prefix", "seam-migration")),
            devices=list(raw.get("devices", [])),
            volumes=list(raw.get("volumes", [])),
            env_vars={str(k): str(v) for k, v in raw.get("env_vars", {}).items()},
            required_env_vars=list(raw.get("required_env_vars", [])),
            required_devices=list(raw.get("required_devices", [])),
            container_workdir=str(raw.get("container_workdir", DEFAULT_CONTAINER_WORKDIR)),
            network_mode=raw.get("network_mode"),
            runtime_flags=list(raw.get("runtime_flags", [])),
            timeout=int(raw.get("timeout", 7200)),
            cleanup=bool(raw.get("cleanup", True)),
        )


@dataclass
class ExperienceConfig:
    """Top-level experience retrieval and injection configuration."""

    enabled: bool = True
    phase7_enabled: bool = True


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
    execution_backend: ExecutionBackendConfig | None = None
    experience: ExperienceConfig = field(default_factory=ExperienceConfig)
    target_platform: Any = None  # TargetPlatformConfig | None after platform_policy imported
    rule_migration: dict[str, Any] | None = None  # workflow-level rule_migration strategy override


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
    on_stagnation: str | None = None
    on_reject_exhausted: str | None = None


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
