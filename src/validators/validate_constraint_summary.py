"""Validation for Phase 1.5 constraint summary output."""

from core.validator_engine import ValidationDict


def validate(data: dict[str, object]) -> ValidationDict:
    errors: list[str] = []

    constraint_summary = data.get("constraint_summary")
    if not isinstance(constraint_summary, str) or not constraint_summary.strip():
        errors.append("constraint_summary must be a non-empty string")

    return {"passed": not errors, "errors": errors, "warnings": []}
