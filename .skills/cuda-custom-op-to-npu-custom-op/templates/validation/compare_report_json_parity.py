#!/usr/bin/env python3
"""Example check that a Markdown report reflects benchmark JSON values."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import TypeAlias, cast

JSONValue: TypeAlias = str | int | float | bool | None | list["JSONValue"] | dict[str, "JSONValue"]
JSONObject: TypeAlias = dict[str, JSONValue]

DEFAULT_JSON_PATHS = (
    "status",
    "manifest_sha256",
    "producer_identity.manifest_producer_path",
    "producer_identity.manifest_producer_sha256",
    "producer_identity.loaded_producer_path",
    "producer_identity.loaded_producer_sha256",
    "producer_identity.loaded_adapter_path",
    "producer_identity.loaded_adapter_sha256",
    "coverage.missing_calls",
    "unsupported_cases",
    "report_parity.status",
    "report_parity.missing_or_mismatched_fields",
    "benchmark.seconds_per_iter",
    "speedup_vs_baseline",
)
OPTIONAL_JSON_PATHS = (
    "project_tests.status",
    "project_tests.commands",
    "project_tests.failures",
    "final_validation.status",
    "final_validation.commands",
    "final_validation.failures",
    "per_manifest_entry_closure.status",
    "final_unresolved_counts.unresolved_total",
)
DEFAULT_REPORT_TEXT = (
    "Evidence Matrix And Open Work",
    "artifact observation",
    "artifact presence, not full migration behavior",
    "report parity",
    "final validation",
    "measured speedup or slowdown",
    "zero unresolved items",
)


def _load_json(path: Path) -> JSONObject:
    loaded = cast(JSONValue, json.loads(path.read_text(encoding="utf-8")))
    if not isinstance(loaded, dict):
        raise ValueError("benchmark JSON root is expected to be an object")
    return loaded


def _lookup(data: JSONObject, dotted_path: str) -> JSONValue:
    current: JSONValue = data
    for part in dotted_path.split("."):
        if not isinstance(current, dict) or part not in current:
            raise KeyError(dotted_path)
        current = current[part]
    return current


def _path_exists(data: JSONObject, dotted_path: str) -> bool:
    try:
        _ = _lookup(data, dotted_path)
    except KeyError:
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("benchmark_json", type=Path)
    _ = parser.add_argument("report_md", type=Path)
    _ = parser.add_argument(
        "--json-path",
        action="append",
        default=[],
        help="Dotted JSON path whose value is expected in the report",
    )
    _ = parser.add_argument(
        "--require-text",
        action="append",
        default=[],
        help="Additional literal text to look for in the report",
    )
    args = parser.parse_args()
    explicit_json_paths = cast(list[str], args.json_path)
    explicit_report_text = cast(list[str], args.require_text)
    benchmark_json = cast(Path, args.benchmark_json)
    report_md = cast(Path, args.report_md)

    data = _load_json(benchmark_json)
    report = report_md.read_text(encoding="utf-8")
    missing: list[str] = []
    json_paths = list(dict.fromkeys([*DEFAULT_JSON_PATHS, *explicit_json_paths]))
    json_paths.extend(path for path in OPTIONAL_JSON_PATHS if _path_exists(data, path) and path not in json_paths)
    report_text = list(dict.fromkeys([*DEFAULT_REPORT_TEXT, *explicit_report_text]))

    for dotted_path in json_paths:
        try:
            value = _lookup(data, dotted_path)
        except KeyError:
            missing.append(f"missing JSON path: {dotted_path}")
            continue
        if str(value) not in report:
            missing.append(f"report missing JSON path value: {dotted_path}")

    for index, text in enumerate(report_text):
        if text not in report:
            missing.append(f"report missing observed text #{index + 1}")

    if missing:
        print("gate_scope=report_json_parity")
        print("evidence_scope=report_parity")
        print("report_json_parity=CHECK_FAILED")
        print("report parity evidence incomplete")
        for item in missing:
            print(item)
        return 1
    print("gate_scope=report_json_parity")
    print("evidence_scope=report_parity")
    print("report_json_parity=CHECK_OK")
    print("report parity evidence matches selected JSON paths")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
