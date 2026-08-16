"""Public facade for the JSONC parser and structural config merge boundary.

This module is a thin re-export so callers keep the stable
``from core.jsonc import ...`` import path. The parser implementation lives in
:mod:`core.jsonc_parser` and the structural merge boundary lives in
:mod:`core.jsonc_merge`; each holds a single responsibility and stays under
the 250 pure-LOC ceiling.
"""
from __future__ import annotations

from core.jsonc_merge import ArrayMergePolicy, merge_config
from core.jsonc_parser import (
    JsoncError,
    JsoncErrorKind,
    JsoncParseError,
    JsonValue,
    ParsedJsonc,
    error_kind_label,
    parse_config_object,
    parse_jsonc,
    strip_jsonc,
)

__all__ = [
    "ArrayMergePolicy",
    "JsoncError",
    "JsoncErrorKind",
    "JsoncParseError",
    "JsonValue",
    "ParsedJsonc",
    "error_kind_label",
    "merge_config",
    "parse_config_object",
    "parse_jsonc",
    "strip_jsonc",
]
