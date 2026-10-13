"""Operator API policy loading and source preflight checks."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any


DEFAULT_POLICY: dict[str, list[str]] = {
    "allowed_torch_api": [
        "torch.empty", "torch.empty_like", "torch.zeros", "torch.zeros_like",
        "torch.ones", "torch.ones_like", "torch.full", "torch.full_like",
        "torch.float16", "torch.float32", "torch.bfloat16", "torch.float64",
        "torch.int8", "torch.int16", "torch.int32", "torch.int64", "torch.bool",
        "torch.Size", "torch.pi", "torch.no_grad", "torch.manual_seed",
        "torch.npu.synchronize", "torch.npu.current_device", "torch.npu.is_available",
        "torch.npu.device_count", "torch.npu.set_device", "torch.npu.get_device_name",
    ],
    "allowed_torch_npu_api": [
        "torch_npu.synchronize", "torch_npu.set_device", "torch_npu.get_device_name",
        "torch_npu.is_available", "torch_npu.device_count", "torch_npu.profiler.",
        "torch_npu.npu.",
    ],
    "allowed_custom_op_prefix": ["torch.ops.cpp_extension_acs."],
    "forbidden_api": [
        "torch.nn.functional.gelu", "torch.erf", "torch.tanh",
        "torch.ops.aten.", "torch_npu.npu_gelu", "torch.ops.load_library",
    ],
}


def load_operator_constraints(raw: str | None) -> dict[str, Any]:
    """Load a JSON policy, merging it over the safe built-in policy."""
    policy: dict[str, Any] = {key: list(value) for key, value in DEFAULT_POLICY.items()}
    if not raw or not raw.strip():
        return policy
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("OPERATOR_CONSTRAINTS must contain a JSON object") from exc
    if not isinstance(parsed, dict):
        raise ValueError("OPERATOR_CONSTRAINTS must contain a JSON object")
    source = parsed.get("api_policy", parsed)
    if not isinstance(source, dict):
        raise ValueError("OPERATOR_CONSTRAINTS.api_policy must be an object")
    for key in policy:
        value = source.get(key)
        if value is not None:
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                raise ValueError(f"{key} must be a list of strings")
            policy[key] = list(value)
    return policy


def render_operator_constraints(raw: str | None) -> str:
    return json.dumps(load_operator_constraints(raw), ensure_ascii=False, indent=2)


def _chain(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _chain(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return ""


def _matches(value: str, exact: list[str], prefixes: list[str]) -> bool:
    return value in exact or any(value.startswith(prefix) for prefix in prefixes)


def _import_aliases(tree: ast.AST) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                local_name = item.asname or item.name.split(".")[0]
                aliases[local_name] = item.name if item.asname else local_name
        elif isinstance(node, ast.ImportFrom) and node.module:
            for item in node.names:
                if item.name != "*":
                    aliases[item.asname or item.name] = f"{node.module}.{item.name}"
    return aliases


def _resolve_alias(value: str, aliases: dict[str, str]) -> str:
    first, separator, remainder = value.partition(".")
    target = aliases.get(first)
    if target is None:
        return value
    return f"{target}.{remainder}" if separator else target


def preflight_operator_sources(
    task_dir: str | Path, constraints: str | None = None
) -> dict[str, Any]:
    """Reject unauthorized torch APIs declared by the candidate solution."""
    root = Path(task_dir).expanduser().resolve()
    policy = load_operator_constraints(constraints)
    violations: list[dict[str, Any]] = []
    solution_path = root / "solution.json"
    try:
        solution = json.loads(solution_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "passed": False,
            "files_scanned": 0,
            "violations": [
                {"file": str(solution_path), "api": "<solution.json>", "error": str(exc)}
            ],
            "policy": policy,
        }
    sources = solution.get("sources") if isinstance(solution, dict) else None
    if not isinstance(sources, list) or not sources:
        return {
            "passed": False,
            "files_scanned": 0,
            "violations": [
                {"file": str(solution_path), "api": "<sources>", "error": "solution.json must declare source files"}
            ],
            "policy": policy,
        }
    python_sources = [
        source for source in sources
        if isinstance(source, dict)
        and isinstance(source.get("path"), str)
        and source["path"].endswith(".py")
    ]
    if not python_sources:
        violations.append({
            "file": str(solution_path), "api": "<python-source>",
            "error": "candidate solution must declare a Python adapter source",
        })
    for source in python_sources:
        relative_path = Path(source["path"])
        if relative_path.is_absolute() or ".." in relative_path.parts:
            violations.append({
                "file": source["path"], "api": "<source-path>",
                "error": "solution source path must be relative and stay within the solution",
            })
            continue
        content = source.get("content")
        if not isinstance(content, str):
            violations.append({
                "file": source["path"], "api": "<source-content>",
                "error": "Python source content must be a string",
            })
            continue
        try:
            tree = ast.parse(content, filename=source["path"])
        except SyntaxError as exc:
            violations.append({"file": source["path"], "api": "<parse>", "error": str(exc)})
            continue
        aliases = _import_aliases(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _chain(node.func)
            if name:
                name = _resolve_alias(name, aliases)
            elif isinstance(node.func, ast.Call) and _chain(node.func.func) == "getattr":
                getter = node.func
                if len(getter.args) >= 2 and isinstance(getter.args[1], ast.Constant):
                    base = _resolve_alias(_chain(getter.args[0]), aliases)
                    if base in {"torch", "torch_npu"}:
                        name = f"{base}.{getter.args[1].value}"
            if not name:
                continue
            if _matches(name, policy["forbidden_api"], policy["forbidden_api"]):
                violations.append({"file": source["path"], "line": node.lineno, "api": name})
                continue
            if name.startswith("torch_npu."):
                allowed = _matches(name, policy["allowed_torch_npu_api"], policy["allowed_torch_npu_api"])
            elif name.startswith("torch."):
                allowed = (
                    _matches(name, [], policy["allowed_custom_op_prefix"])
                    if name.startswith("torch.ops.")
                    else _matches(name, policy["allowed_torch_api"], [])
                )
            else:
                continue
            if not allowed:
                violations.append({"file": source["path"], "line": node.lineno, "api": name})
    return {
        "passed": not violations,
        "files_scanned": len(python_sources),
        "violations": violations,
        "policy": policy,
    }
