"""Validation for Phase 6 reports output.

The Phase-6 report contract is declared once in ``schemas/phase_6_reports.json``
(bug #18): this module consumes that schema via ``jsonschema`` so nested type
violations (e.g. ``migration_summary.files_migrated`` given as a string) are
rejected instead of silently passing the old hand-rolled dict checks. The
hand-rolled checks are kept because the schema alone cannot express the
time-facts contract (a ``—`` em-dash placeholder is a valid JSON string but a
violation of the run-timeline contract locked by bug #13).

The declared schema version is surfaced through ``REPORT_SCHEMA_VERSION``.
"""

from __future__ import annotations

import json
from typing import cast

import jsonschema
from jsonschema import Draft7Validator

from core.paths import src_root
from core.validator_engine import ValidationDict

REPORT_SCHEMA_PATH = src_root() / "schemas" / "phase_6_reports.json"
REPORT_SCHEMA_VERSION = "1.0"

with open(REPORT_SCHEMA_PATH, encoding="utf-8") as _schema_fh:
    _REPORT_SCHEMA = json.load(_schema_fh)
_REPORT_SCHEMA_VALIDATOR = Draft7Validator(_REPORT_SCHEMA)


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

    run_timeline = data.get("run_timeline")
    if run_timeline is not None and not isinstance(run_timeline, dict):
        errors.append("run_timeline must be a dictionary")
    elif isinstance(run_timeline, dict):
        phases = run_timeline.get("phases")
        if not isinstance(phases, list):
            errors.append("run_timeline.phases must be a list")
        else:
            for idx, phase_entry in enumerate(phases):
                if not isinstance(phase_entry, dict):
                    errors.append(
                        f"run_timeline.phases[{idx}] must be a dictionary"
                    )
                    continue
                for field in ("started_at", "ended_at"):
                    value = phase_entry.get(field)
                    if not isinstance(value, str) or not value or value == "\u2014":
                        errors.append(
                            f"run_timeline.phases[{idx}].{field} must be a real ISO-8601 UTC timestamp"
                        )

    # JSON-Schema pass against schemas/phase_6_reports.json. Catches nested
    # type violations the hand-rolled checks cannot see (e.g.
    # migration_summary.files_migrated given as a string instead of integer).
    for schema_error in _REPORT_SCHEMA_VALIDATOR.iter_errors(data):
        errors.append(schema_error.message)

    return {
        "passed": not errors,
        "errors": errors,
        "warnings": [],
    }
