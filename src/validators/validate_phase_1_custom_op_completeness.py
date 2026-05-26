"""Validator for assisted Phase 1 custom-op completeness reports."""

from __future__ import annotations

from typing import cast

from core.assisted_verification import validate_phase1_assisted_report
from core.validator_engine import ValidationDict


def validate(data: dict[str, object]) -> ValidationDict:
    phase1_output = data.get("phase1_output")
    report = data.get("report")
    if not isinstance(phase1_output, dict) or not isinstance(report, dict):
        return {
            "passed": False,
            "errors": ["phase1_output and report must be objects"],
            "warnings": [],
        }
    errors = validate_phase1_assisted_report(cast(dict[str, object], report), cast(dict[str, object], phase1_output))
    return {"passed": not errors, "errors": errors, "warnings": []}
