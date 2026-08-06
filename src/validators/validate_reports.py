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

    return {"passed": not errors, "errors": errors, "warnings": []}
