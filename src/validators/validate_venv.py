"""Validation for Phase 2 virtual environment output."""

from typing import cast

from core.validator_engine import ValidationDict


def validate(data: dict[str, object]) -> ValidationDict:
    errors: list[str] = []

    venv_path = data.get("venv_path")
    if not isinstance(venv_path, str) or not venv_path.strip():
        errors.append("venv_path must be a non-empty string")

    python_path = data.get("python_path")
    if not isinstance(python_path, str) or not python_path.strip():
        errors.append("python_path must be a non-empty string")

    installed_packages = data.get("installed_packages")
    if not isinstance(installed_packages, list):
        errors.append("installed_packages must be a list")
    else:
        package_list = cast(list[object], installed_packages)
        if not all(isinstance(package, str) for package in package_list):
            errors.append("installed_packages must contain only strings")

    return {"passed": not errors, "errors": errors, "warnings": []}
