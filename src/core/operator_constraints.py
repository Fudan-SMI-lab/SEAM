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


def preflight_operator_sources(
    task_dir: str | Path, constraints: str | None = None
) -> dict[str, Any]:
    """Reject unauthorized torch APIs before invoking KernelBench."""
    root = Path(task_dir).expanduser().resolve()
    policy = load_operator_constraints(constraints)
    violations: list[dict[str, Any]] = []
    files = sorted(path for path in root.rglob("*.py") if path.is_file())
    for path in files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError) as exc:
            violations.append({"file": str(path), "api": "<parse>", "error": str(exc)})
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _chain(node.func)
            if not name:
                continue
            if _matches(name, policy["forbidden_api"], policy["forbidden_api"]):
                violations.append({"file": str(path), "line": node.lineno, "api": name})
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
                violations.append({"file": str(path), "line": node.lineno, "api": name})
    return {
        "passed": not violations,
        "files_scanned": len(files),
        "violations": violations,
        "policy": policy,
    }
