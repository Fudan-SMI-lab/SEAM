#!/usr/bin/env python3
"""Example artifact scanner for a migration manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def load_json(path: Path) -> dict[str, object]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("manifest root is expected to be a JSON object")
    return data


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def add_path(results: list[tuple[str, str]], root: Path, label: str, value: object) -> None:
    if not isinstance(value, str) or not value:
        return
    candidate = (root / value).resolve()
    state = "present" if candidate.exists() else "missing"
    results.append((label, f"{state} {value}"))


def collect_artifacts(manifest: dict[str, object]) -> list[tuple[str, str]]:
    results: list[tuple[str, str]] = []
    package_artifacts = manifest.get("package_artifacts")
    if isinstance(package_artifacts, dict):
        for key, value in package_artifacts.items():
            add_path(results, Path("."), f"package_artifacts.{key}", value)

    add_path(results, Path("."), "adapter_artifact", manifest.get("adapter_artifact"))
    add_path(results, Path("."), "producer_artifact", manifest.get("producer_artifact"))

    entries = manifest.get("entries")
    if isinstance(entries, list):
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                continue
            for key in ("aclnn_header", "kernel_dir", "kernel_json", "kernel_object", "op_config_json", "op_info_json", "opapi_library"):
                add_path(results, Path("."), f"entries[{index}].{key}", entry.get(key))
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("manifest", type=Path)
    _ = parser.add_argument("--root", type=Path, default=Path("."), help="Base directory for relative artifact paths")
    args = parser.parse_args()

    manifest = load_json(args.manifest)
    results: list[tuple[str, str]] = []

    custom_opp_path = manifest.get("custom_opp_path")
    if isinstance(custom_opp_path, str) and custom_opp_path:
        root = (args.root / custom_opp_path).resolve()
    else:
        root = args.root.resolve()

    package_artifacts = manifest.get("package_artifacts")
    if isinstance(package_artifacts, dict):
        for key, value in package_artifacts.items():
            add_path(results, root, f"package_artifacts.{key}", value)

    add_path(results, root, "adapter_artifact", manifest.get("adapter_artifact"))
    add_path(results, root, "producer_artifact", manifest.get("producer_artifact"))

    entries = manifest.get("entries")
    if isinstance(entries, list):
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                continue
            for key in ("aclnn_header", "kernel_dir", "kernel_json", "kernel_object", "op_config_json", "op_info_json", "opapi_library"):
                add_path(results, root, f"entries[{index}].{key}", entry.get(key))

    print("artifact_scan=observed")
    print(f"manifest_sha256={sha256(args.manifest)}")
    for label, state in results:
        print(f"{label}={state}")
    return 0 if all(state.startswith("present") for _, state in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
