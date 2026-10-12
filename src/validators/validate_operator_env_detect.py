"""Validation for a single KernelBench operator task and environment phase."""

from typing import cast

from core.operator_benchmark import validate_operator_task
from core.validator_engine import ValidationDict


def validate(data: dict[str, object]) -> ValidationDict:
    errors: list[str] = []
    task_dir = data.get("operator_task_dir")
    if not isinstance(task_dir, str) or not task_dir.strip():
        errors.append("operator_task_dir must be a non-empty string")
    else:
        task_result = validate_operator_task(task_dir)
        errors.extend(cast(list[str], task_result.get("errors", [])))
    if data.get("solution_type", "ascendc") != "ascendc":
        errors.append("solution_type must be ascendc in the first implementation")
    return {"passed": not errors, "errors": errors, "warnings": []}
