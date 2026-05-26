"""Validator for assisted Phase 3 custom-op coverage reports."""

from __future__ import annotations

from typing import cast

from core.assisted_verification import validate_phase3_assisted_report
from core.validator_engine import ValidationDict


def validate(data: dict[str, object]) -> ValidationDict:
    phase1_output = data.get("phase1_output")
    phase3_output = data.get("phase3_output")
    report = data.get("report")
    if not isinstance(phase1_output, dict) or not isinstance(phase3_output, dict) or not isinstance(report, dict):
        return {
            "passed": False,
            "errors": ["phase1_output, phase3_output, and report must be objects"],
            "warnings": [],
        }
    errors = validate_phase3_assisted_report(
        cast(dict[str, object], report),
        cast(dict[str, object], phase3_output),
        cast(dict[str, object], phase1_output),
    )
    return {"passed": not errors, "errors": errors, "warnings": []}
