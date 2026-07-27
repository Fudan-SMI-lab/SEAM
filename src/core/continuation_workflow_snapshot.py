from __future__ import annotations

import os
from pathlib import Path
from typing import Any, cast

import yaml

from core.config import (
    _parse_agents,
    _parse_execution_backend,
    _parse_experience,
    _parse_hooks,
    _parse_phases,
    _parse_rule_migration,
    _parse_sub_workflows,
    _parse_terminals,
    _validate_phase_types,
    _validate_top_level,
    _validate_transitions,
)
from core.platform_policy import parse_target_platform
from core.types import WorkflowDefinition


class WorkflowSnapshotError(ValueError):
    pass


_MAX_WORKFLOW_BYTES = 2 * 1024 * 1024


def read_workflow_snapshot(path: Path) -> bytes:
    try:
        with path.open("rb") as handle:
            size = os.fstat(handle.fileno()).st_size
            if size > _MAX_WORKFLOW_BYTES:
                raise WorkflowSnapshotError("workflow exceeds the snapshot byte limit")
            content = handle.read(_MAX_WORKFLOW_BYTES + 1)
            if len(content) != size:
                raise WorkflowSnapshotError("workflow changed while being read")
            return content
    except OSError as exc:
        raise WorkflowSnapshotError("workflow snapshot is unavailable") from exc


def load_workflow_snapshot(content: bytes, source: str) -> WorkflowDefinition:
    try:
        loaded = yaml.safe_load(content.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise WorkflowSnapshotError(f"Workflow '{source}' is not valid UTF-8") from exc
    if not isinstance(loaded, dict):
        raise WorkflowSnapshotError(
            f"Workflow YAML must contain a top-level mapping, got {type(loaded).__name__}"
        )
    raw = cast(dict[str, Any], loaded)
    _validate_top_level(raw, source)
    globals_config = raw.get("globals", {})
    terminals = _parse_terminals(raw["terminals"])
    phases = _parse_phases(raw["phases"])
    _validate_transitions(phases, terminals)
    _validate_phase_types(phases)
    return WorkflowDefinition(
        name=raw["name"],
        version=str(raw["version"]),
        description=raw.get("description", ""),
        globals=globals_config,
        phases=phases,
        terminals=terminals,
        agents=_parse_agents(raw.get("agents", {})),
        sub_workflows=_parse_sub_workflows(raw.get("sub_workflows", {})),
        hooks=_parse_hooks(raw.get("hooks", {})),
        execution_backend=_parse_execution_backend(raw.get("execution_backend")),
        experience=_parse_experience(raw.get("experience")),
        target_platform=parse_target_platform(raw.get("target_platform")),
        rule_migration=_parse_rule_migration(raw.get("rule_migration")),
    )
