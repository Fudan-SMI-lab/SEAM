"""Validation for Phase 6 reports output."""

from typing import cast

from core.validator_engine import ValidationDict


def validate(data: dict[str, object]) -> ValidationDict:
    errors: list[str] = []

    report_paths = data.get("report_paths")
    if not isinstance(report_paths, list):
        errors.append("report_paths must be a list")
    else:
        report_path_list = cast(list[object], report_paths)
        if not all(isinstance(path, str) for path in report_path_list):
            errors.append("report_paths must contain only strings")

    if not isinstance(data.get("migration_summary"), dict):
        errors.append("migration_summary must be a dictionary")

    return {"passed": not errors, "errors": errors, "warnings": []}
