from __future__ import annotations

import ast
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
POLICY_MODULES = (
    "core/workflow_condition_policy.py",
    "core/workflow_dispatch_policy.py",
    "core/workflow_stop_policy.py",
    "core/workflow_stagnation_policy.py",
    "core/workflow_transition_policy.py",
    "harness/run/workflow_result_projection.py",
)


@pytest.mark.parametrize("relative_path", POLICY_MODULES)
def test_workflow_policy_module_is_bounded_and_python38_compatible(
    relative_path: str,
) -> None:
    path = ROOT / relative_path
    source = path.read_text(encoding="utf-8")

    _ = ast.parse(source, filename=str(path), feature_version=(3, 8))
    pure_lines = [
        line
        for line in source.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert len(pure_lines) <= 250
    assert "typing.Any" not in source
    assert "from typing import Any" not in source
    assert "cast(" not in source


def test_coordinators_delegate_policy_decisions() -> None:
    executor = (ROOT / "core/workflow_executor.py").read_text(encoding="utf-8")
    harness = (ROOT / "tests/e2e/e2e_test_v3.py").read_text(encoding="utf-8")

    for delegated in (
        "evaluate_condition(",
        "select_dispatch_route(",
        "select_stop_status(",
        "reduce_stagnation(",
        "plan_next_phase(",
    ):
        assert delegated in executor
    assert "project_workflow_result(" in harness
    assert "def _safe_eval_bool(" not in executor
    assert "def _tokenize(" not in executor
