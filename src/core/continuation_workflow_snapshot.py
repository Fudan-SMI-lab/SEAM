from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Protocol, TypedDict

import yaml

import sys

from core.continuation_config_compat import (
    WorkflowCompatibilityError,
    load_workflow_compat,
)
from core.platform_policy import parse_target_platform
from core.types import WorkflowDefinition
from core.continuation_lock_identity import (
    BoundedReadError,
    BoundedReadErrorKind,
    read_verified_bytes,
)


class WorkflowSnapshotError(ValueError):
    pass


_MAX_WORKFLOW_BYTES = 2 * 1024 * 1024


class _RequiredWorkflowDocument(TypedDict):
    name: str
    version: object
    phases: list[dict[str, object]]
    terminals: object


class _WorkflowDocument(_RequiredWorkflowDocument, total=False):
    description: str
    globals: dict[str, object] | None
    agents: dict[str, dict[str, object]]
    sub_workflows: dict[str, dict[str, object]]
    hooks: dict[str, list[dict[str, object]]]
    execution_backend: object
    experience: object
    target_platform: object
    rule_migration: object


class _WorkflowYamlLoader(Protocol):
    def __call__(
        self, content: str, /
    ) -> (
        _WorkflowDocument
        | list[object]
        | str
        | int
        | float
        | bool
        | bytes
        | date
        | datetime
        | set[object]
        | None
    ): ...


def _load_yaml(
    loader: _WorkflowYamlLoader,
    content: str,
) -> (
    _WorkflowDocument
    | list[object]
    | str
    | int
    | float
    | bool
    | bytes
    | date
    | datetime
    | set[object]
    | None
):
    return loader(content)


def read_workflow_snapshot(path: Path) -> bytes:
    try:
        return read_verified_bytes(path, _MAX_WORKFLOW_BYTES)
    except BoundedReadError as exc:
        if exc.kind is BoundedReadErrorKind.TOO_LARGE:
            raise WorkflowSnapshotError(
                "workflow exceeds the snapshot byte limit"
            ) from exc
        if exc.kind is BoundedReadErrorKind.CHANGED:
            raise WorkflowSnapshotError("workflow changed while being read") from exc
        raise WorkflowSnapshotError("workflow snapshot is unavailable") from exc


def load_workflow_snapshot(content: bytes, source: str) -> WorkflowDefinition:
    try:
        loaded = _load_yaml(yaml.safe_load, content.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise WorkflowSnapshotError(f"Workflow '{source}' is not valid UTF-8") from exc
    if not isinstance(loaded, dict):
        raise WorkflowSnapshotError(
            f"Workflow YAML must contain a top-level mapping, got {type(loaded).__name__}"
        )
    raw = loaded
    if not isinstance(raw.get("name"), str):
        raise WorkflowSnapshotError(f"Workflow '{source}': 'name' must be a string")
    if not isinstance(raw.get("version"), (str, int)):
        raise WorkflowSnapshotError(
            f"Workflow '{source}': 'version' must be a string or integer"
        )
    if "description" in raw and not isinstance(raw["description"], str):
        raise WorkflowSnapshotError(
            f"Workflow '{source}': 'description' must be a string"
        )
    if (
        "globals" in raw
        and raw["globals"] is not None
        and not isinstance(raw["globals"], dict)
    ):
        raise WorkflowSnapshotError(
            f"Workflow '{source}': 'globals' must be a mapping or null"
        )
    for field in ("agents", "sub_workflows", "hooks"):
        value = raw.get(field, {})
        if not isinstance(value, dict):
            raise WorkflowSnapshotError(
                f"Workflow '{source}': '{field}' must be a mapping"
            )
    if sys.version_info < (3, 9):
        try:
            return load_workflow_compat(content)
        except (OSError, ValueError, WorkflowCompatibilityError) as exc:
            raise WorkflowSnapshotError(
                f"Workflow '{source}' could not be parsed"
            ) from exc
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

    _validate_top_level(dict(raw), source)
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
