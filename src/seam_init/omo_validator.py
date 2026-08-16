"""Narrow draft-07 validator for OMO config candidates.

Implements every validation keyword present in the vendored
``omo.schema.json``: type, enum, const, properties, additionalProperties,
required, propertyNames, items, maxItems, minItems, anyOf, oneOf,
minimum, maximum, exclusiveMinimum, minLength, maxLength, pattern.
``format`` is recognised as a draft-07 annotation (not enforced).

Constraints are read FROM the schema document at validation time — not
hand-coded.  When a schema subschema contains a validation keyword absent
from the recognised set, the validator returns an issue so that a future
schema with unsupported keywords fails closed rather than passing silently.
"""
from __future__ import annotations

import re

from core.jsonc import JsonValue
from seam_init.opencode_discovery import JsonDict

__all__ = ["validate_against_schema"]

_IMPLEMENTED: frozenset[str] = frozenset({
    "type", "enum", "const", "properties", "additionalProperties", "required",
    "propertyNames", "items", "maxItems", "minItems", "anyOf", "oneOf",
    "minimum", "maximum", "exclusiveMinimum", "minLength", "maxLength",
    "pattern",
})
_NOOP_FORMAT: frozenset[str] = frozenset({"format"})
_ANNOTATION: frozenset[str] = frozenset({"title", "description", "default"})
_ALL_RECOGNISED: frozenset[str] = _IMPLEMENTED | _NOOP_FORMAT | _ANNOTATION


def validate_against_schema(schema: JsonDict, instance: JsonValue) -> list[str]:
    """Validate *instance* against *schema*; return issue strings (empty=valid)."""
    return _validate(schema, instance, "$")


def _validate(schema: JsonValue, instance: JsonValue, path: str) -> list[str]:
    if not isinstance(schema, dict):
        return []
    issues: list[str] = []
    for key in schema:
        if key not in _ALL_RECOGNISED and not key.startswith("$"):
            issues.append(f"{path}: unsupported schema keyword {key!r}")
            return issues
    expected = schema.get("type")
    if isinstance(expected, str) and not _type_matches(instance, expected):
        issues.append(f"{path}: expected {expected}, got {_json_type(instance)}")
        return issues
    _check_const(schema, instance, path, issues)
    enum_values = schema.get("enum")
    if isinstance(enum_values, list) and instance not in enum_values:
        issues.append(f"{path}: {instance!r} not in enum")
    if isinstance(instance, dict):
        issues.extend(_validate_object(schema, instance, path))
    elif isinstance(instance, list):
        issues.extend(_validate_array(schema, instance, path))
    elif isinstance(instance, str):
        issues.extend(_validate_string(schema, instance, path))
    elif isinstance(instance, (int, float)) and not isinstance(instance, bool):
        issues.extend(_validate_number(schema, instance, path))
    _check_anyof(schema, instance, path, issues)
    _check_oneof(schema, instance, path, issues)
    return issues


def _check_const(
    schema: JsonDict, instance: JsonValue, path: str, issues: list[str],
) -> None:
    if "const" not in schema:
        return
    const_val = schema["const"]
    if isinstance(instance, bool) or isinstance(const_val, bool):
        if type(instance) is not type(const_val) or instance != const_val:
            issues.append(f"{path}: {instance!r} != const {const_val!r}")
    elif instance != const_val:
        issues.append(f"{path}: {instance!r} != const {const_val!r}")


def _check_anyof(
    schema: JsonDict, instance: JsonValue, path: str, issues: list[str],
) -> None:
    if "anyOf" not in schema:
        return
    branches = schema["anyOf"]
    if isinstance(branches, list) and not any(
        isinstance(b, dict) and not _validate(b, instance, path) for b in branches
    ):
        issues.append(f"{path}: no anyOf branch matched")


def _check_oneof(
    schema: JsonDict, instance: JsonValue, path: str, issues: list[str],
) -> None:
    if "oneOf" not in schema:
        return
    branches = schema["oneOf"]
    if not isinstance(branches, list):
        return
    matches = sum(
        1 for b in branches if isinstance(b, dict) and not _validate(b, instance, path))
    if matches != 1:
        issues.append(f"{path}: oneOf matched {matches} branches, expected exactly 1")


def _validate_object(schema: JsonDict, obj: dict[str, JsonValue], path: str) -> list[str]:
    issues: list[str] = []
    props = schema.get("properties")
    known: set[str] = set(props.keys()) if isinstance(props, dict) else set()
    required = schema.get("required")
    if isinstance(required, list):
        for req in required:
            if isinstance(req, str) and req not in obj:
                issues.append(f"{path}: missing required property {req!r}")
    addl = schema.get("additionalProperties")
    for key, val in obj.items():
        child_path = f"{path}.{key}"
        name_schema = schema.get("propertyNames")
        if isinstance(name_schema, dict):
            issues.extend(_validate(name_schema, key, child_path))
        if key in known and isinstance(props, dict):
            issues.extend(_validate(props[key], val, child_path))
        elif addl is False:
            issues.append(f"{child_path}: additional property not allowed")
        elif isinstance(addl, dict):
            issues.extend(_validate(addl, val, child_path))
    return issues


def _validate_array(schema: JsonDict, arr: list[JsonValue], path: str) -> list[str]:
    issues: list[str] = []
    max_items = schema.get("maxItems")
    if isinstance(max_items, int) and not isinstance(max_items, bool) and len(arr) > max_items:
        issues.append(f"{path}: array exceeds maxItems {max_items}")
    min_items = schema.get("minItems")
    if isinstance(min_items, int) and not isinstance(min_items, bool) and len(arr) < min_items:
        issues.append(f"{path}: array has fewer than minItems {min_items}")
    item_schema = schema.get("items")
    if isinstance(item_schema, dict):
        for i, item in enumerate(arr):
            issues.extend(_validate(item_schema, item, f"{path}[{i}]"))
    return issues


def _validate_string(schema: JsonDict, val: str, path: str) -> list[str]:
    issues: list[str] = []
    min_len = schema.get("minLength")
    if isinstance(min_len, int) and not isinstance(min_len, bool) and len(val) < min_len:
        issues.append(f"{path}: string shorter than minLength {min_len}")
    max_len = schema.get("maxLength")
    if isinstance(max_len, int) and not isinstance(max_len, bool) and len(val) > max_len:
        issues.append(f"{path}: string exceeds maxLength {max_len}")
    pattern = schema.get("pattern")
    if isinstance(pattern, str):
        try:
            if not re.search(pattern, val):
                issues.append(f"{path}: does not match pattern {pattern!r}")
        except re.error:
            pass
    return issues


def _validate_number(schema: JsonDict, val: int | float, path: str) -> list[str]:
    issues: list[str] = []
    minimum = schema.get("minimum")
    if isinstance(minimum, (int, float)) and not isinstance(minimum, bool) and val < minimum:
        issues.append(f"{path}: {val} below minimum {minimum}")
    maximum = schema.get("maximum")
    if isinstance(maximum, (int, float)) and not isinstance(maximum, bool) and val > maximum:
        issues.append(f"{path}: {val} above maximum {maximum}")
    excl = schema.get("exclusiveMinimum")
    if isinstance(excl, (int, float)) and not isinstance(excl, bool) and val <= excl:
        issues.append(f"{path}: {val} not above exclusiveMinimum {excl}")
    return issues


def _type_matches(instance: JsonValue, expected: str) -> bool:
    match expected:
        case "object":
            return isinstance(instance, dict)
        case "array":
            return isinstance(instance, list)
        case "string":
            return isinstance(instance, str)
        case "boolean":
            return isinstance(instance, bool)
        case "number":
            return isinstance(instance, (int, float)) and not isinstance(instance, bool)
        case "integer":
            return isinstance(instance, int) and not isinstance(instance, bool)
        case _:
            return True


def _json_type(instance: JsonValue) -> str:
    if isinstance(instance, bool):
        return "boolean"
    if isinstance(instance, int):
        return "integer"
    if isinstance(instance, float):
        return "number"
    if isinstance(instance, str):
        return "string"
    if isinstance(instance, list):
        return "array"
    if isinstance(instance, dict):
        return "object"
    return "null"
