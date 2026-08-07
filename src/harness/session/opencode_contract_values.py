from __future__ import annotations

import math

from harness.session.opencode_contract_json import JsonObject, JsonValue


def object_value(value: JsonValue) -> JsonObject | None:
    return value if isinstance(value, dict) else None


def string_value(value: JsonValue) -> str:
    return value if isinstance(value, str) else ""


def valid_identifier(value: JsonValue, prefix: str) -> bool:
    return isinstance(value, str) and value.startswith(prefix)


def strings_present(raw: JsonObject, keys: tuple[str, ...]) -> bool:
    return all(isinstance(raw.get(key), str) for key in keys)


def nonnegative_number(value: JsonValue) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    return finite_number(value) and value >= 0


def finite_number(value: JsonValue) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return value.bit_length() <= 1024
    return isinstance(value, float) and math.isfinite(value)


def nonnegative_integer(value: JsonValue) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value >= 0


def response_status(response: JsonObject | None) -> int | None:
    if response is None:
        return None
    value = response.get("status")
    return value if type(value) is int else None


def valid_time(raw: JsonValue, require_end: bool = False) -> bool:
    value = object_value(raw)
    if value is None or not nonnegative_integer(value.get("start")):
        return False
    if require_end and "end" not in value:
        return False
    if "end" in value and not nonnegative_integer(value["end"]):
        return False
    compacted = value.get("compacted")
    return "compacted" not in value or nonnegative_integer(compacted)


def optional_string(raw: JsonObject, key: str) -> bool:
    return key not in raw or isinstance(raw[key], str)


def optional_boolean(raw: JsonObject, key: str) -> bool:
    return key not in raw or isinstance(raw[key], bool)


def string_record(value: JsonValue) -> bool:
    record = object_value(value)
    return record is not None and all(isinstance(item, str) for item in record.values())


def valid_tokens(value: JsonValue) -> bool:
    tokens = object_value(value)
    cache = object_value(tokens.get("cache")) if tokens is not None else None
    if tokens is None or cache is None:
        return False
    total = tokens.get("total")
    return (
        all(finite_number(tokens.get(key)) for key in ("input", "output", "reasoning"))
        and all(finite_number(cache.get(key)) for key in ("read", "write"))
        and ("total" not in tokens or finite_number(total))
    )


def _valid_file_diff(value: JsonValue) -> bool:
    diff = object_value(value)
    if diff is None:
        return False
    status = diff.get("status")
    return (
        finite_number(diff.get("additions"))
        and finite_number(diff.get("deletions"))
        and optional_string(diff, "file")
        and optional_string(diff, "patch")
        and (
            "status" not in diff
            or isinstance(status, str)
            and status in {"added", "deleted", "modified"}
        )
    )


def valid_diffs(value: JsonValue) -> bool:
    return isinstance(value, list) and all(_valid_file_diff(item) for item in value)


def _valid_permission_rule(value: JsonValue) -> bool:
    rule = object_value(value)
    return (
        rule is not None
        and strings_present(rule, ("permission", "pattern"))
        and isinstance(rule.get("action"), str)
        and rule.get("action") in {"allow", "deny", "ask"}
    )


def valid_permissions(value: JsonValue) -> bool:
    return isinstance(value, list) and all(
        _valid_permission_rule(item) for item in value
    )
