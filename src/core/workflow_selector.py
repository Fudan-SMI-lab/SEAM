"""Workflow Selector — YAML-driven candidate workflow resolution with agent selection.

A selector YAML file (``kind: workflow_selector``) lists candidate workflow files,
asks an agent (via the OpenCode session manager) to pick the best one for the current
project, then overlays explicit override fields onto the selected workflow before
loading it with the standard ``load_workflow()`` path.

Public API
----------
.. function:: resolve_workflow_from_selector

    Main entry point. Given a selector YAML path, session manager, prompt loader,
    and optional project context, returns the path to the materialized (merged)
    workflow YAML that can be consumed by ``load_workflow()``.

.. function:: is_selector_yaml

    Check whether a raw YAML dict is a selector definition.

.. function:: is_selector_file

    Check whether a YAML file on disk is a selector definition (lightweight).

Example selector YAML::

    kind: workflow_selector
    name: "auto-select"
    description: "Auto-select the best workflow for this project"
    candidate_workflows:
      - path: "workflows/npu_migration_v2.yaml"
        description: "Ascend NPU migration"
      - path: "workflows/ppu_migration_v2.yaml"
        description: "PPU migration"
    fallback: "workflows/npu_migration_v2.yaml"
    overrides:
      execution_backend:
        container_name: "my-custom-name"
      globals:
        max_repair_iterations: 10

Integration notes for later wiring
----------------------------------
- ``resolve_workflow_from_selector`` returns a ``Path`` to the materialized YAML.
  The caller should pass that path to ``load_workflow(path)``.
- The function requires a ``session_mgr`` with ``get_or_create`` and ``send_command``,
  and a ``prompt_loader`` with ``load_prompt``.  Both can be mocked in tests.
- The ``project_context`` dict is a lightweight summary of the target project
  (not a full file dump).  Keep it compact.
- The output materialization directory defaults to
  ``output_dir / "resolved_workflows"``, creating it on-demand.
"""

from __future__ import annotations

import logging
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from .config import _read_yaml
from .paths import execution_root, resolve_relative_path

logger = logging.getLogger(__name__)

SELECTOR_KIND_MARKERS = frozenset({"workflow_selector", "workflow-selector"})

# ── Public helpers ──────────────────────────────────────────────────────


def is_selector_yaml(raw: dict[str, Any]) -> bool:
    """Return True when *raw* is a workflow-selector definition."""
    return raw.get("kind") in SELECTOR_KIND_MARKERS


def is_selector_file(path: str | Path) -> bool:
    """Return True when the YAML file at *path* is a workflow-selector definition.

    This is a lightweight check that reads the file and inspects ``kind``
    without performing full schema validation.  Use it before ``load_workflow()``
    to decide whether to route through ``resolve_workflow_from_selector()``.

    Args:
        path: Absolute or relative path to a YAML file.

    Returns:
        True if the file has ``kind: workflow_selector`` (or ``workflow-selector``).

    Raises:
        FileNotFoundError: If the file does not exist.
    """
    p = Path(path)
    raw = _read_yaml(_resolve_selector_path(str(p)))
    return is_selector_yaml(raw)


def resolve_workflow_from_selector(  # pylint: disable=too-many-locals; silent
    selector_path: str,
    session_mgr: Any,
    prompt_loader: Any,
    *,
    project_context: dict[str, Any] | None = None,
    output_dir: str | Path = ".",
) -> Path:
    """Parse a selector YAML, pick a candidate, apply overrides, materialize.

    Args:
        selector_path: Absolute or relative path to the selector YAML.
        session_mgr: OpenCode session manager (must have ``get_or_create``
                     and ``send_command``).
        prompt_loader: Prompt loader with ``load_prompt(template_name, context)``.
        project_context: Lightweight dictionary describing the project (paths,
                         language hints, framework hints, etc.).  Not a full
                         source dump.
        output_dir: Directory where the materialized merged workflow YAML
                    is written.  A ``resolved_workflows/`` subdirectory
                    is created inside *output_dir*.

    Returns:
        Path to the materialized workflow YAML, ready for ``load_workflow()``.

    Raises:
        FileNotFoundError: Selector YAML or a candidate workflow does not exist.
        ValueError: Invalid selector shape, empty candidates, or missing fallback
                    when agent selection fails.
    """
    if project_context is None:
        project_context = {}

    selector_abs = _resolve_selector_path(selector_path)
    raw = _read_yaml(selector_abs)

    if not is_selector_yaml(raw):
        raise ValueError(
            f"File at '{selector_path}' is not a workflow selector. "
            f"Expected 'kind: workflow_selector', got {raw.get('kind')!r}"
        )

    _validate_selector_schema(raw, selector_path)

    selector_name = str(raw.get("name", selector_abs.stem))
    selector_dir = selector_abs.parent

    # ── Selector-level configuration ─────────────────────────────────
    # Top-level fields (backward-compatible) take priority unless the
    # ``selector`` block explicitly overrides them.  This lets a single
    # file define both selector settings and workflow overrides without
    # breaking existing selector YAMLs.
    raw_selector_cfg = raw.get("selector")
    selector_cfg: dict[str, Any] = (
        dict(raw_selector_cfg) if isinstance(raw_selector_cfg, dict) else {}
    )
    # ``selector.fallback`` takes precedence over top-level ``fallback``.
    effective_fallback = selector_cfg.pop("fallback", raw.get("fallback"))

    # Resolve and validate candidate workflow paths
    candidates = _resolve_candidates(raw["candidate_workflows"], selector_dir, selector_path)

    # Agent-based selection
    selected_path = _select_workflow_via_agent(
        candidates=candidates,
        session_mgr=session_mgr,
        prompt_loader=prompt_loader,
        project_context=project_context,
        selector_name=selector_name,
        fallback=effective_fallback,
        selector_path=selector_path,
        selector_config=selector_cfg,
    )

    # Load the selected workflow YAML
    selected_raw = _read_yaml(selected_path)

    # Apply overrides
    overrides = raw.get("overrides", {})
    if overrides:
        merged_raw = _deep_merge_overrides(selected_raw, overrides)
    else:
        merged_raw = selected_raw

    # Ensure the merged result keeps the original name unless overridden
    if "name" not in overrides and "name" in selected_raw:
        merged_raw["name"] = selected_raw["name"]
    if "version" not in overrides and "version" in selected_raw:
        merged_raw["version"] = selected_raw["version"]

    # Materialize
    materialized_path = _materialize_merged_workflow(
        merged_raw=merged_raw,
        selector_name=selector_name,
        output_dir=Path(output_dir),
    )

    logger.info(
        "Workflow selector resolved: %s → %s → %s",
        selector_path,
        selected_path,
        materialized_path,
    )
    return materialized_path


# ── Internal helpers ────────────────────────────────────────────────────


def _resolve_selector_path(path: str) -> Path:
    """Resolve *path* to an absolute, existing YAML file."""
    p = Path(path)
    if not p.is_absolute():
        p = resolve_relative_path(p)
    if not p.exists():
        raise FileNotFoundError(f"Selector file not found: {path}")
    return p


def _validate_selector_schema(raw: dict[str, Any], path: str) -> None:
    """Ensure the selector YAML has the required shape."""
    candidates = raw.get("candidate_workflows")
    if not candidates or not isinstance(candidates, list) or len(candidates) == 0:
        raise ValueError(f"Selector '{path}': 'candidate_workflows' must be a non-empty list")

    seen_paths: set[str] = set()
    for i, entry in enumerate(candidates):
        if not isinstance(entry, dict):
            raise ValueError(f"Selector '{path}': candidate_workflows[{i}] must be a mapping")
        entry_path = entry.get("path")
        if not entry_path or not isinstance(entry_path, str) or not entry_path.strip():
            raise ValueError(
                f"Selector '{path}': candidate_workflows[{i}] missing required "
                f"non-empty string 'path'"
            )
        if entry_path in seen_paths:
            raise ValueError(f"Selector '{path}': duplicate candidate path '{entry_path}'")
        seen_paths.add(entry_path)

    fallback = raw.get("fallback")
    if fallback is not None:
        if not isinstance(fallback, str) or not fallback.strip():
            raise ValueError(f"Selector '{path}': 'fallback' must be a non-empty string when set")

    overrides = raw.get("overrides")
    if overrides is not None and not isinstance(overrides, dict):
        raise ValueError(f"Selector '{path}': 'overrides' must be a mapping when set")


def _resolve_candidates(
    candidate_entries: list[dict[str, Any]],
    selector_dir: Path,
    selector_path: str,
) -> list[dict[str, Any]]:
    """Resolve each candidate path and verify the files exist.

    Resolution order:
    1. Relative to the selector YAML's parent directory (first).
    2. Relative to the execution root (second).

    Returns a list of dicts with keys ``path`` (resolved absolute Path),
    ``description`` (str), and the original ``raw_path`` (str).
    """
    resolved: list[dict[str, Any]] = []
    for i, entry in enumerate(candidate_entries):
        raw_path = entry["path"].strip()
        candidate_path = _resolve_candidate_path(raw_path, selector_dir, selector_path, i)
        resolved.append(
            {
                "path": candidate_path,
                "raw_path": raw_path,
                "description": str(entry.get("description", candidate_path.stem)),
            }
        )
    return resolved


def _resolve_candidate_path(
    raw_path: str,
    selector_dir: Path,
    selector_path: str,
    index: int,
) -> Path:
    """Resolve a single candidate path to an existing absolute Path."""
    # Try relative to selector dir first
    candidate = (selector_dir / raw_path).resolve()
    if candidate.exists() and candidate.is_file():
        return candidate

    # Try relative to execution root
    exec_root = execution_root()
    candidate = (exec_root / raw_path).resolve()
    if candidate.exists() and candidate.is_file():
        return candidate

    raise FileNotFoundError(
        f"Selector '{selector_path}': candidate_workflows[{index}] "
        f"path '{raw_path}' not found relative to selector directory "
        f"({selector_dir}) or execution root ({exec_root})"
    )


# pylint: disable-next=too-many-arguments,too-many-branches,too-many-locals; silent
def _select_workflow_via_agent(
    *,
    candidates: list[dict[str, Any]],
    session_mgr: Any,
    prompt_loader: Any,
    project_context: dict[str, Any],
    selector_name: str,
    fallback: str | None,
    selector_path: str,
    selector_config: dict[str, Any] | None = None,
) -> Path:
    """Ask an agent to pick a candidate; fall back when output is invalid."""
    # pylint: disable-next=import-outside-toplevel; silent
    from harness.session.manager import extract_json_response

    if selector_config is None:
        selector_config = {}

    selector_agent = str(selector_config.get("agent", "")).strip()
    selector_timeout = selector_config.get("timeout", 120)
    if not isinstance(selector_timeout, (int, float)):
        selector_timeout = 120

    # Build candidate list text
    candidate_lines: list[str] = []
    for i, c in enumerate(candidates):
        candidate_lines.append(f"{i + 1}. **{c['raw_path']}** — {c['description']}")
    candidates_text = "\n".join(candidate_lines)

    # Build project context summary text
    project_summary = _format_project_summary(project_context)

    prompt_text = prompt_loader.load_prompt(
        "workflow_select",
        {
            "selector_name": selector_name,
            "candidate_workflows": candidates_text,
            "project_context": project_summary,
        },
    )

    # Get or create a session
    try:
        kwargs: dict[str, Any] = {"role": "workflow_selector", "lifecycle": "ephemeral"}
        if selector_agent:
            kwargs["agent"] = selector_agent
        sid = session_mgr.get_or_create(**kwargs)
    except TypeError:
        # Graceful fallback: fake session managers may not accept ``agent``.
        sid = session_mgr.get_or_create(role="workflow_selector", lifecycle="ephemeral")
    except Exception:  # pylint: disable=broad-exception-caught; silent
        # Graceful fallback: create_session if get_or_create missing
        create = getattr(session_mgr, "create_session", None)
        if callable(create):
            kwargs = {
                "role": "workflow_selector",
                "lifecycle": "ephemeral",
                "title": f"migration-workflow-selector-{selector_name}",
            }
            if selector_agent:
                kwargs["agent"] = selector_agent
            try:
                sid = create(**kwargs)
            except TypeError:
                sid = create(
                    role="workflow_selector",
                    lifecycle="ephemeral",
                    title=f"migration-workflow-selector-{selector_name}",
                )
        else:
            # pylint: disable-next=raise-missing-from; silent
            raise RuntimeError("Session manager has no get_or_create or create_session method")

    # Send prompt and parse
    try:
        cmd_kwargs: dict[str, Any] = {"timeout": selector_timeout}
        if selector_agent:
            cmd_kwargs["agent"] = selector_agent
        raw_response = session_mgr.send_command(sid, prompt_text, **cmd_kwargs)
        parsed = extract_json_response(raw_response)
        selected_raw = _validate_agent_selection(parsed, candidates, selector_path)
        if selected_raw:
            return selected_raw
    except TypeError:
        # Fake session manager doesn't support agent kwarg.
        raw_response = session_mgr.send_command(sid, prompt_text, timeout=selector_timeout)
        parsed = extract_json_response(raw_response)
        selected_raw = _validate_agent_selection(parsed, candidates, selector_path)
        if selected_raw:
            return selected_raw
    except Exception as exc:  # pylint: disable=broad-exception-caught; silent
        logger.warning("Agent workflow selection failed for '%s': %s", selector_path, exc)

    # Fallback path
    return _resolve_fallback(fallback, candidates, selector_path)


def _validate_agent_selection(
    parsed: Any,
    candidates: list[dict[str, Any]],
    selector_path: str,
) -> Path | None:
    """Validate the agent's JSON output against the candidate list.

    Returns the resolved Path on success, None if output is invalid.
    """
    if not isinstance(parsed, dict):
        logger.warning("Agent selection output for '%s' is not a dict: %r", selector_path, parsed)
        return None

    selected_str = parsed.get("selected_workflow")
    if not selected_str or not isinstance(selected_str, str) or not selected_str.strip():
        logger.warning(
            "Agent selection output for '%s' missing 'selected_workflow': %r",
            selector_path,
            parsed,
        )
        return None

    selected_str = selected_str.strip()

    # Match against candidates by raw_path or resolved path stem
    for c in candidates:
        if selected_str == c["raw_path"]:
            return c["path"]
        if selected_str == str(c["path"]):
            return c["path"]
        # pylint: disable-next=consider-using-in; silent
        if selected_str == c["path"].stem or selected_str == c["path"].name:
            return c["path"]

    logger.warning(
        "Agent selected '%s' which is not in candidate list for '%s'",
        selected_str,
        selector_path,
    )
    return None


def _resolve_fallback(
    fallback: str | None,
    candidates: list[dict[str, Any]],
    selector_path: str,
) -> Path:
    """Resolve the fallback workflow path.

    Raises ValueError when there is no fallback configured.
    """
    if not fallback:
        raise ValueError(
            f"Agent workflow selection failed for selector '{selector_path}' and "
            f"no 'fallback' is configured.  Either ensure the agent can respond "
            f"correctly or set a fallback in the selector YAML."
        )

    for c in candidates:
        if fallback == c["raw_path"] or fallback == str(c["path"]):
            logger.info("Using configured fallback '%s' for selector '%s'", fallback, selector_path)
            return c["path"]

    raise ValueError(
        f"Fallback '{fallback}' configured in selector '{selector_path}' "
        f"is not among the candidate_workflows."
    )


def _format_project_summary(project_context: dict[str, Any]) -> str:
    """Build a compact text summary of the project context."""
    if not project_context:
        # pylint: disable-next=line-too-long; silent
        return "(No project context provided — make your best guess based on workflow descriptions alone.)"

    # Start with a project name / path if available
    project_name = project_context.get("project_name", project_context.get("project_path", ""))
    lines: list[str] = []
    if project_name:
        lines.append(f"**Project**: {project_name}")

    # Project language / framework hints
    language = project_context.get("language", project_context.get("primary_language", ""))
    if language:
        lines.append(f"**Primary Language**: {language}")

    framework = project_context.get("framework", project_context.get("primary_framework", ""))
    if framework:
        lines.append(f"**Framework**: {framework}")

    # File count / size hints
    file_count = project_context.get("file_count")
    if file_count is not None:
        lines.append(f"**Files**: {file_count}")

    # Any other summarised fields
    for key in ("build_system", "python_version", "cuda_version", "notes"):
        val = project_context.get(key)
        if val:
            lines.append(f"**{key.replace('_', ' ').title()}**: {val}")

    # Quick file hints (paths only, not content)
    file_hints = project_context.get("file_hints", [])
    if file_hints:
        if isinstance(file_hints, str):
            file_hints = [h.strip() for h in file_hints.split(",") if h.strip()]
        if file_hints:
            lines.append(f"**Key Files**: {', '.join(str(h) for h in file_hints[:10])}")

    if not lines:
        return "(Minimal project context provided)"

    return "\n".join(lines)


def _deep_merge_overrides(
    base: dict[str, Any],
    overrides: dict[str, Any],
) -> dict[str, Any]:
    """Recursively merge *overrides* into *base*.

    Rules:
    - A key present in both dicts → recursively merge if both values are dicts.
    - A key present only in overrides → added to result.
    - A key present only in base → kept unchanged.
    - List / scalar values in overrides **replace** the base value entirely
      (no list merging).
    """
    result = deepcopy(base)

    for key, override_val in overrides.items():
        if key in result and isinstance(result[key], dict) and isinstance(override_val, dict):
            result[key] = _deep_merge_overrides(result[key], override_val)
        else:
            result[key] = deepcopy(override_val)

    return result


def _materialize_merged_workflow(
    merged_raw: dict[str, Any],
    selector_name: str,
    output_dir: Path,
) -> Path:
    """Write the merged workflow YAML to a deterministic path under the
    output artifact directory.

    The file is placed in ``<output_dir>/resolved_workflows/<selector_name>.yaml``.
    """
    resolved_dir = output_dir / "resolved_workflows"
    resolved_dir.mkdir(parents=True, exist_ok=True)

    # Sanitize the selector name for use as filename
    safe_name = (
        "".join(c if c.isalnum() or c in "._-" else "_" for c in selector_name).strip("_")
        or "selected_workflow"
    )

    out_path = resolved_dir / f"{safe_name}.yaml"

    with open(out_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(merged_raw, f, default_flow_style=False, sort_keys=False)

    logger.info("Materialized merged workflow to %s", out_path)
    return out_path
