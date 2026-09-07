"""Schema authority for OMO config: loads the vendored draft-07 schema document
pinned to commit ``ee81ab7`` by SHA-256, extracts the canonical schema URL
and reasoning vocabulary FROM the schema data, and provides schema-driven
candidate validation via :mod:`seam_init.omo_validator`.

The schema asset is the real ``omo.schema.json`` shipped by the upstream
``code-yeongyu/oh-my-openagent`` package at commit ``ee81ab7`` (v5.0.0-beta).
It is vendored under ``seam_init/data/`` — never fetched from the network.

``load_schema_document`` rejects a missing, unparseable, or **tampered**
asset (valid JSON with wrong SHA-256) so integrity is tied to the pinned
commit, not merely syntactic validity.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Final

from core.jsonc import parse_config_object
from seam_init.omo_validator import validate_against_schema
from seam_init.opencode_discovery import JsonDict

__all__ = [
    "SCHEMA_COMMIT", "SCHEMA_ASSET_REL", "SCHEMA_SHA256", "SchemaAssetError",
    "extract_reasoning_values", "extract_schema_url", "load_schema_document",
    "validate_against_schema",
]

SCHEMA_COMMIT: Final[str] = "ee81ab7c5150fbe027b0b79b411093a30e1d7353"
SCHEMA_SHA256: Final[str] = (
    "20c2941457aae29a7d7cbdebbaf01d300285bc53e6ee434476a4e25fc44bae63"
)
SCHEMA_ASSET_REL: Final[str] = "data/omo.schema.json"


class SchemaAssetError(Exception):
    """Raised when the vendored schema asset is missing, corrupt, or tampered."""


def _asset_path() -> Path:
    return Path(__file__).resolve().parent / SCHEMA_ASSET_REL


def load_schema_document() -> JsonDict:
    """Read, hash-verify, and parse the vendored schema asset.

    Raises :class:`SchemaAssetError` if the asset is missing, unparseable,
    or has a SHA-256 that does not match :data:`SCHEMA_SHA256`.
    """
    path = _asset_path()
    if not path.is_file():
        raise SchemaAssetError(f"schema asset missing: {SCHEMA_ASSET_REL}")
    raw = path.read_bytes()
    actual = hashlib.sha256(raw).hexdigest()
    if actual != SCHEMA_SHA256:
        raise SchemaAssetError(
            f"schema asset hash mismatch: expected {SCHEMA_SHA256[:16]}, "
            f"got {actual[:16]}")
    try:
        parsed = parse_config_object(raw.decode("utf-8"))
    except (OSError, ValueError) as exc:
        raise SchemaAssetError(f"schema asset unparseable: {exc}") from exc
    value = parsed.value
    if not isinstance(value, dict) or "$id" not in value or "$schema" not in value:
        raise SchemaAssetError("schema asset lacks $id/$schema root keys")
    return value


def extract_schema_url(schema: JsonDict) -> str:
    """Return the canonical ``$id`` URL from the schema document."""
    raw = schema.get("$id")
    if not isinstance(raw, str) or not raw.strip():
        raise SchemaAssetError("schema $id is not a nonempty string")
    return raw


def extract_reasoning_values(schema: JsonDict) -> tuple[str, ...]:
    """Walk the schema to find the reasoning enum; derive FROM schema data."""
    found = _find_reasoning_enum(schema)
    if found is None:
        raise SchemaAssetError("reasoning enum not found in schema document")
    return found


def _find_reasoning_enum(node: object) -> tuple[str, ...] | None:
    if isinstance(node, dict):
        props = node.get("properties")
        if isinstance(props, dict):
            reasoning = props.get("reasoning")
            if isinstance(reasoning, dict):
                extracted = _enum_from_anyof(reasoning)
                if extracted is not None:
                    return extracted
        for child in node.values():
            result = _find_reasoning_enum(child)
            if result is not None:
                return result
    return None


def _enum_from_anyof(definition: JsonDict) -> tuple[str, ...] | None:
    branches = definition.get("anyOf")
    if not isinstance(branches, list):
        return None
    for branch in branches:
        if not isinstance(branch, dict):
            continue
        enum = branch.get("enum")
        if isinstance(enum, list):
            strs = [e for e in enum if isinstance(e, str)]
            if strs and len(strs) == len(enum):
                return tuple(strs)
    return None
