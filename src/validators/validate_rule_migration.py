"""Validation for Phase 4 rule migration output."""

from typing import cast

from core.validator_engine import ValidationDict


def validate(data: dict[str, object]) -> ValidationDict:
    errors: list[str] = []

    files_migrated = data.get("files_migrated")
    if (
        not isinstance(files_migrated, int)
        or isinstance(files_migrated, bool)
        or files_migrated < 0
    ):
        errors.append("files_migrated must be an integer >= 0")

    files_skipped = data.get("files_skipped")
    if not isinstance(files_skipped, int) or isinstance(files_skipped, bool) or files_skipped < 0:
        errors.append("files_skipped must be an integer >= 0")

    replacement_counts = data.get("replacement_counts")
    if not isinstance(replacement_counts, dict):
        errors.append("replacement_counts must be a dictionary")
    else:
        replacement_count_items = cast(dict[object, object], replacement_counts)
        for rule_name, count in replacement_count_items.items():
            if not isinstance(rule_name, str):
                errors.append("replacement_counts keys must be strings")
                break
            if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                errors.append("replacement_counts values must be integers >= 0")
                break

    return {"passed": not errors, "errors": errors, "warnings": []}
