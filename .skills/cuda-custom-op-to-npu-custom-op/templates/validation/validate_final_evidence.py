#!/usr/bin/env python3
"""Example final-evidence checker for migration notes."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def load_json(path: Path) -> dict[str, object]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} root is expected to be a JSON object")
    return data


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def summarise(label: str, data: dict[str, object]) -> list[str]:
    lines = [f"{label}_status={data.get('status', 'unknown')}"]
    if "manifest_sha256" in data:
        lines.append(f"{label}_manifest_sha256={data.get('manifest_sha256')}")
    if "speedup_vs_baseline" in data:
        lines.append(f"{label}_speedup_vs_baseline={data.get('speedup_vs_baseline')}")
    if "unresolved_total" in data:
        lines.append(f"{label}_unresolved_total={data.get('unresolved_total')}")
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--manifest", type=Path)
    _ = parser.add_argument("--runtime-coverage", type=Path)
    _ = parser.add_argument("--baseline-result", type=Path)
    _ = parser.add_argument("--custom-result", type=Path)
    _ = parser.add_argument("--report-path", type=Path)
    args = parser.parse_args()

    inputs = {
        "manifest": args.manifest,
        "runtime_coverage": args.runtime_coverage,
        "baseline": args.baseline_result,
        "custom": args.custom_result,
    }
    missing = [name for name, path in inputs.items() if path is None]
    if missing:
        print("final_evidence_status=needs_inputs")
        print(f"missing_inputs={','.join(missing)}")
        return 1

    manifest = load_json(args.manifest)
    runtime_coverage = load_json(args.runtime_coverage)
    baseline = load_json(args.baseline_result)
    custom = load_json(args.custom_result)
    report_text = args.report_path.read_text(encoding="utf-8") if args.report_path else ""

    print("final_evidence_status=observed")
    print(f"manifest_sha256={sha256(args.manifest)}")
    for line in summarise("runtime_coverage", runtime_coverage):
        print(line)
    for line in summarise("baseline", baseline):
        print(line)
    for line in summarise("custom", custom):
        print(line)
    if report_text:
        print(f"report_length={len(report_text)}")
    entries = manifest.get("entries")
    entry_count = len(entries) if isinstance(entries, list) else 0
    print(f"manifest_entries={entry_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
