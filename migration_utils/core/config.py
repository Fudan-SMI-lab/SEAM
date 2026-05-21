"""Workflow Config Loader — parses YAML workflow definitions."""

from pathlib import Path
from typing import Any

import yaml

from .paths import resolve_relative_path
from .types import (
    ExecutionBackendConfig,
    ExperienceConfig,
    PhaseDefinition,
    RuntimeSkillsConfig,
    WorkflowDefinition,
    PhaseHooks,
    TransitionDefinition,
    HookDefinition,
    SubWorkflowDefinition,
)

VALID_PHASE_TYPES = {"llm", "shell", "builtin", "python", "review", "dispatch", "loop", "orchestration"}
VALID_RUNTIME_SKILL_MERGE = {"append", "replace", "none"}
VALID_RUNTIME_SKILL_MISSING = {"warn", "error", "ignore"}


def load_workflow(path: str) -> WorkflowDefinition:
    """Parse a YAML workflow file and return a validated WorkflowDefinition.

    Args:
        path: Path to the workflow YAML file (absolute or relative).

    Returns:
        WorkflowDefinition with all fields populated and validated.

    Raises:
        ValueError: If required fields are missing or transitions are invalid.
        FileNotFoundError: If the YAML file does not exist.
    """
    yaml_path = Path(path)
    if not yaml_path.is_absolute():
        yaml_path = resolve_relative_path(yaml_path)
    if not yaml_path.exists():
        raise FileNotFoundError(f"Workflow file not found: {path}")

    raw = _read_yaml(yaml_path)
    _validate_top_level(raw, path)
    globals_cfg = raw.get("globals", {})
    terminals = _parse_terminals(raw["terminals"])
    phases = _parse_phases(raw["phases"])
    _validate_transitions(phases, terminals)
    _validate_phase_types(phases)

    agents = _parse_agents(raw.get("agents", {}))
    sub_workflows = _parse_sub_workflows(raw.get("sub_workflows", {}))
    hooks = _parse_hooks(raw.get("hooks", {}))
    execution_backend = _parse_execution_backend(raw.get("execution_backend"))
    experience = _parse_experience(raw.get("experience"))

    return WorkflowDefinition(
        name=raw["name"],
        version=str(raw["version"]),
        description=raw.get("description", ""),
        globals=globals_cfg,
        phases=phases,
        terminals=terminals,
        agents=agents,
        sub_workflows=sub_workflows,
        hooks=hooks,
        execution_backend=execution_backend,
        experience=experience,
    )


# ── Internal helpers ──────────────────────────────────────────────

def _read_yaml(path: Path) -> dict[str, Any]:
    """Read and parse a YAML file."""
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"Workflow YAML must contain a top-level mapping, got {type(data).__name__}")
    return data


def _validate_top_level(raw: dict[str, Any], path: str) -> None:
    """Check that required top-level fields exist."""
    missing = [f for f in ("name", "version", "phases", "terminals") if f not in raw or raw[f] is None]
    if missing:
        raise ValueError(f"Workflow '{path}' missing required fields: {', '.join(missing)}")

    if not isinstance(raw["phases"], list) or len(raw["phases"]) == 0:
        raise ValueError(f"Workflow '{path}': 'phases' must be a non-empty list")

    if not isinstance(raw["terminals"], (list, dict)) or len(raw["terminals"]) == 0:
        raise ValueError(f"Workflow '{path}': 'terminals' must be a non-empty list or dict")


def _parse_terminals(raw_terminals: Any) -> list[str]:
    """Normalize terminals to a flat list of IDs.

    Accepts both list and dict forms:
        terminals: [complete, failed]           → ["complete", "failed"]
        terminals: {complete: "done", …}        → ["complete", …]
    """
    if isinstance(raw_terminals, list):
        result = []
        for t in raw_terminals:
            if isinstance(t, dict):
                tid = t.get("id")
                if tid:
                    result.append(str(tid))
                else:
                    result.append(str(t))
            else:
                result.append(str(t))
        return result
    if isinstance(raw_terminals, dict):
        return [str(k) for k in raw_terminals.keys()]
    raise ValueError(f"terminals must be a list or dict, got {type(raw_terminals).__name__}")


def _parse_hooks(raw_hooks: dict[str, list[dict[str, Any]]]) -> dict[str, list[HookDefinition]]:
    """Parse workflow-level hooks configuration.

    Expected YAML shape:
        hooks:
          workflow_start:
            - type: builtin
              operation: snapshot_project
              save_as: before_snapshot
          workflow_end: [...]
    """
    result: dict[str, list[HookDefinition]] = {}
    if not raw_hooks:
        return result

    for hook_point, hook_list in raw_hooks.items():
        if not isinstance(hook_list, list):
            raise ValueError(
                f"hooks.{hook_point} must be a list of hook configs, got {type(hook_list).__name__}")

        parsed: list[HookDefinition] = []
        for i, raw_hook in enumerate(hook_list):
            if not isinstance(raw_hook, dict):
                raise ValueError(f"hooks.{hook_point}[{i}] must be a mapping, got {type(raw_hook).__name__}")

            operation = raw_hook.get("operation")
            if not operation:
                raise ValueError(f"hooks.{hook_point}[{i}] missing required field 'operation'")

            parsed.append(HookDefinition(
                operation=str(operation),
                type=str(raw_hook.get("type", "builtin")),
                save_as=raw_hook.get("save_as"),
                critical=bool(raw_hook.get("critical", False)),
                params=raw_hook.get("params", {}) if isinstance(raw_hook.get("params"), dict) else {},
            ))
        result[hook_point] = parsed

    return result


def _parse_phase_hooks(raw_hooks: dict[str, Any] | None) -> PhaseHooks | None:
    """Parse per-phase hooks into a PhaseHooks object.

    Expected YAML shape:
        hooks:
          pre_execute: [hook_a, hook_b]
          post_execute: [hook_c]
          on_error: [hook_d]
    """
    if not raw_hooks:
        return None

    return PhaseHooks(
        pre_execute=[str(h) for h in raw_hooks.get("pre_execute", [])],
        post_execute=[str(h) for h in raw_hooks.get("post_execute", [])],
        on_error=[str(h) for h in raw_hooks.get("on_error", [])],
    )


def _parse_string_list(value: Any, location: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{location} must be a list of skill names, got {type(value).__name__}")
    result: list[str] = []
    for i, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{location}[{i}] must be a non-empty string")
        result.append(item.strip())
    return result


def _parse_runtime_skills(raw: Any, location: str) -> RuntimeSkillsConfig | None:
    if raw is None:
        return None

    if isinstance(raw, list):
        return RuntimeSkillsConfig(include=_parse_string_list(raw, location))

    if not isinstance(raw, dict):
        raise ValueError(f"{location} must be a list or mapping, got {type(raw).__name__}")

    merge = str(raw.get("merge", "append"))
    if merge not in VALID_RUNTIME_SKILL_MERGE:
        raise ValueError(f"{location}.merge must be one of {sorted(VALID_RUNTIME_SKILL_MERGE)}, got '{merge}'")

    missing = str(raw.get("missing", "warn"))
    if missing not in VALID_RUNTIME_SKILL_MISSING:
        raise ValueError(f"{location}.missing must be one of {sorted(VALID_RUNTIME_SKILL_MISSING)}, got '{missing}'")

    return RuntimeSkillsConfig(
        include=_parse_string_list(raw.get("include", []), f"{location}.include"),
        exclude=_parse_string_list(raw.get("exclude", []), f"{location}.exclude"),
        merge=merge,
        missing=missing,
        inject_full=bool(raw.get("inject_full", False)),
        exclude_dynamic_duplicates=bool(raw.get("exclude_dynamic_duplicates", True)),
    )


def _parse_transition_def(raw: dict[str, Any]) -> TransitionDefinition | None:
    """Parse a TransitionDefinition from a raw dict.

    Expected YAML shape:
        transition:
          on_success: next_phase
          on_failure: failed
          on_skip: skipped
          on_stagnation: error_recovery
          on_reject_exhausted: cleanup
    """
    if not raw:
        return None

    on_success = raw.get("on_success")
    on_failure = raw.get("on_failure")
    on_skip = raw.get("on_skip")
    on_stagnation = raw.get("on_stagnation")
    on_reject_exhausted = raw.get("on_reject_exhausted")

    if on_success is None and on_failure is None and on_skip is None and on_stagnation is None and on_reject_exhausted is None:
        return None

    return TransitionDefinition(
        on_success=str(on_success) if on_success is not None else None,
        on_failure=str(on_failure) if on_failure is not None else None,
        on_skip=str(on_skip) if on_skip is not None else None,
        on_stagnation=str(on_stagnation) if on_stagnation is not None else None,
        on_reject_exhausted=str(on_reject_exhausted) if on_reject_exhausted is not None else None,
    )


def _parse_phases(raw_phases: list[dict[str, Any]]) -> list[PhaseDefinition]:
    """Convert raw phase dicts into PhaseDefinition objects with ALL new fields."""
    seen_ids: set[str] = set()
    phases: list[PhaseDefinition] = []

    for i, raw in enumerate(raw_phases):
        pid = raw.get("id")
        if not pid:
            raise ValueError(f"Phase at index {i} missing required field 'id'")
        if pid in seen_ids:
            raise ValueError(f"Duplicate phase id: '{pid}'")
        seen_ids.add(pid)

        name = raw.get("name", pid)
        prompt_template = raw.get("prompt_template", "")
        output_schema = raw.get("output_schema", {})
        validator = raw.get("validator", None)

        # Existing transitions dict (backward compat)
        transitions = raw.get("transitions", {})
        if not isinstance(transitions, dict):
            transitions = {}

        # New fields
        phase_type = str(raw.get("type", "llm"))
        agent = raw.get("agent", None)
        timeout = raw.get("timeout", None)
        condition = raw.get("condition", None)
        input_mapping = raw.get("input_mapping", {})
        if not isinstance(input_mapping, dict):
            input_mapping = {}
        output_as = raw.get("output_as", None)
        max_iterations = raw.get("max_iterations", None)
        sub_workflow = raw.get("sub_workflow", None)
        validate_only = bool(raw.get("validate_only", False))
        on_failure = str(raw.get("on_failure", "continue"))

        # New transition object (transition.on_success, on_failure, on_skip)
        transition = _parse_transition_def(raw.get("transition", {}))

        # Phase-level hooks
        hooks = _parse_phase_hooks(raw.get("hooks", None))

        # Experience memory fields
        handler = raw.get("handler", None)
        retrieve_experience = bool(raw.get("retrieve_experience", False))
        experience_query = raw.get("experience_query", None)
        runtime_skills = _parse_runtime_skills(raw.get("runtime_skills", None), f"phases[{pid}].runtime_skills")
        params = raw.get("params", {})
        if not isinstance(params, dict):
            params = {}
        else:
            params = dict(params)

        if raw.get("operation") is not None:
            params["operation"] = raw.get("operation")

        phases.append(
            PhaseDefinition(
                id=pid,
                name=name,
                prompt_template=prompt_template,
                output_schema=output_schema if isinstance(output_schema, dict) else {},
                validator=validator,
                transitions=transitions,
                type=phase_type,
                agent=agent,
                timeout=timeout,
                condition=condition,
                input_mapping=input_mapping,
                output_as=output_as,
                max_iterations=max_iterations,
                sub_workflow=sub_workflow,
                validate_only=validate_only,
                hooks=hooks,
                transition=transition,
                on_failure=on_failure,
                handler=handler,
                retrieve_experience=retrieve_experience,
                experience_query=experience_query,
                runtime_skills=runtime_skills,
                params=params,
            )
        )

    return phases


def _parse_agents(raw_agents: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Parse agent definitions from YAML.

    Expected YAML shape:
        agents:
          main_engineer:
            role: main_engineer
            lifecycle: persistent
          error_analyzer:
            role: error_analyzer
            lifecycle: persistent

    Returns dict[str, dict] where each value contains at least 'role' and 'lifecycle'.
    """
    result: dict[str, dict[str, Any]] = {}
    if not raw_agents:
        return result

    for agent_id, agent_cfg in raw_agents.items():
        if not isinstance(agent_cfg, dict):
            raise ValueError(
                f"agents.{agent_id} must be a mapping, got {type(agent_cfg).__name__}")

        role = agent_cfg.get("role")
        if not role:
            raise ValueError(f"agents.{agent_id} missing required field 'role'")

        lifecycle = agent_cfg.get("lifecycle", "ephemeral")
        runtime_skills = _parse_runtime_skills(agent_cfg.get("runtime_skills", None), f"agents.{agent_id}.runtime_skills")

        parsed_cfg = {
            "role": str(role),
            "lifecycle": str(lifecycle),
            **{k: v for k, v in agent_cfg.items() if k not in ("role", "lifecycle", "runtime_skills")},
        }
        if runtime_skills is not None:
            parsed_cfg["runtime_skills"] = runtime_skills
        result[agent_id] = parsed_cfg

    return result


def _parse_sub_workflows(raw_sub_wfs: dict[str, dict[str, Any]]) -> dict[str, SubWorkflowDefinition]:
    """Parse sub-workflow definitions from YAML.

    Expected YAML shape:
        sub_workflows:
          repair_loop:
            id: repair_loop
            type: loop
            max_iterations: 5
            stagnation_threshold: 3
            review_gate_enabled: false
            max_review_iterations: 3
            stop_conditions:
              - condition: "exit_code == 0"
                status: success
            phases: [...]
            blocks: {}

    Returns dict[str, SubWorkflowDefinition] with raw values preserved (no global resolution).
    """
    result: dict[str, SubWorkflowDefinition] = {}
    if not raw_sub_wfs:
        return result

    for sw_id, sw_cfg in raw_sub_wfs.items():
        if not isinstance(sw_cfg, dict):
            raise ValueError(
                f"sub_workflows.{sw_id} must be a mapping, got {type(sw_cfg).__name__}")

        sw_id_field = sw_cfg.get("id", sw_id)
        if not sw_id_field:
            raise ValueError(f"sub_workflows.{sw_id} missing required field 'id'")

        stop_conditions = sw_cfg.get("stop_conditions", [])
        if not isinstance(stop_conditions, list):
            stop_conditions = []

        sub_phases = sw_cfg.get("phases", [])
        if not isinstance(sub_phases, list):
            sub_phases = []

        blocks = sw_cfg.get("blocks", {})
        if not isinstance(blocks, dict):
            blocks = {}

        result[str(sw_id_field)] = SubWorkflowDefinition(
            id=str(sw_id_field),
            type=str(sw_cfg.get("type", "loop")),
            max_iterations=sw_cfg.get("max_iterations", 5),
            stagnation_threshold=sw_cfg.get("stagnation_threshold", 3),
            review_gate_enabled=sw_cfg.get("review_gate_enabled", False),
            max_review_iterations=sw_cfg.get("max_review_iterations", 3),
            stop_conditions=stop_conditions,
            phases=sub_phases,
            blocks=blocks,
        )

    return result


def _parse_execution_backend(raw: Any) -> ExecutionBackendConfig | None:
    """Parse optional top-level ``execution_backend`` YAML key.

    Returns ``None`` when the key is absent (backward-compatible default that
    results in local-mode execution).  Any parsing errors from
    :meth:`ExecutionBackendConfig.from_dict` propagate to the caller.
    """
    if raw is None:
        return None
    if isinstance(raw, dict):
        return ExecutionBackendConfig.from_dict(raw)
    raise ValueError(
        f"execution_backend must be a mapping or absent, got {type(raw).__name__}"
    )


def _parse_experience(raw: Any) -> ExperienceConfig:
    """Parse optional top-level ``experience`` YAML key.

    Returns defaults (enabled=True, phase7_enabled=True) when absent.
    """
    if raw is None:
        return ExperienceConfig()
    if not isinstance(raw, dict):
        raise ValueError(
            f"experience must be a mapping or absent, got {type(raw).__name__}"
        )
    return ExperienceConfig(
        enabled=bool(raw.get("enabled", True)),
        phase7_enabled=bool(raw.get("phase7_enabled", True)),
    )


def _validate_phase_types(phases: list[PhaseDefinition]) -> None:
    """Ensure all phase types are in the set of valid types."""
    for phase in phases:
        if phase.type not in VALID_PHASE_TYPES:
            raise ValueError(
                f"Phase '{phase.id}': invalid type '{phase.type}'. "
                f"Valid types: {sorted(VALID_PHASE_TYPES)}")


def _validate_transitions(phases: list[PhaseDefinition], terminals: list[str]) -> None:
    """Ensure every transition target references a valid phase ID or terminal.

    Validates both:
    1. The flat `transitions` dict (e.g. {"on_success": "next"})
    2. The new `transition` object (TransitionDefinition with on_success/on_failure/on_skip/on_stagnation/on_reject_exhausted)
    """
    valid_targets = set(terminals) | {p.id for p in phases}

    for phase in phases:
        # Validate flat transitions dict (backward compat)
        if phase.transitions:
            if not isinstance(phase.transitions, dict):
                raise ValueError(
                    f"Phase '{phase.id}': 'transitions' must be a mapping, got {type(phase.transitions).__name__}")
            for event, target in phase.transitions.items():
                if target not in valid_targets:
                    raise ValueError(
                        f"Phase '{phase.id}': transition '{event}' references unknown target '{target}'. "
                        f"Valid targets: {sorted(valid_targets)}")

        # Validate TransitionDefinition object
        if phase.transition is not None:
            td = phase.transition
            for field_name in ("on_success", "on_failure", "on_skip", "on_stagnation", "on_reject_exhausted"):
                target = getattr(td, field_name, None)
                if target is not None and target not in valid_targets:
                    raise ValueError(
                        f"Phase '{phase.id}': transition.{field_name} references unknown target '{target}'. "
                        f"Valid targets: {sorted(valid_targets)}")
