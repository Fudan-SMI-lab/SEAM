"""Bounded, run-scoped collection of migration report evidence."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.secret_redaction import redact_sensitive_text


SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "models", "weights"}
GENERATED = {"migration_report.md", "migration_report_facts.json", "migration_report_manifest.json"}
SUFFIXES = {".md", ".log", ".txt", ".json", ".jsonl"}


def redact_report_text(text: str) -> str:
    """Reuse secret redaction without hiding numeric token measurements."""
    metrics: list[str] = []

    def protect(match: re.Match[str]) -> str:
        metrics.append(match.group())
        return f"__SEAM_NUMERIC_MEASUREMENT_{len(metrics) - 1}__"

    protected = re.sub(
        r'(?<![\w])"?(?:input_tokens|output_tokens|total_tokens|num_tokens|n_tokens|'
        r'tokens_per_second|throughput_tokens_per_second)"?\s*[:=]\s*'
        r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?(?=\s|[,}\]]|$)",
        protect,
        text,
    )
    result = redact_sensitive_text(protected)
    for index, metric in enumerate(metrics):
        result = result.replace(f"__SEAM_NUMERIC_MEASUREMENT_{index}__", metric)
    return result


@dataclass
class ReportSource:
    id: str
    path: str
    text: str
    sha256: str
    size_bytes: int
    truncated: bool = False
    data: Any = None


@dataclass
class ReportEvidence:
    run_id: str
    input_dir: str = ""
    sources: list[ReportSource] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def document(self, name: str, *, root_only: bool = False) -> tuple[dict[str, Any], str]:
        """Prefer input-root evidence to lower-priority supplemental roots."""
        for source in self.sources:
            if root_only and Path(source.path).parent != Path(self.input_dir):
                continue
            if Path(source.path).name == name and isinstance(source.data, dict):
                return source.data, source.id
        return {}, "—"


def _read_json(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 2_000_000:
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def collect_report_evidence(
    input_dir: Path,
    *,
    artifact_dir: Path | None = None,
    run_id: str | None = None,
    exclude: Path | None = None,
    max_file_bytes: int = 262_144,
    max_total_bytes: int = 4_194_304,
    max_files: int = 200,
) -> ReportEvidence:
    if min(max_file_bytes, max_total_bytes, max_files) <= 0:
        raise ValueError("Evidence limits must be positive")
    root = input_dir.expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"Input is not a directory: {root}")
    summary = _read_json(root / "summary.json")
    recorded_run = summary.get("run_id")
    if run_id and recorded_run and run_id != recorded_run:
        raise ValueError(f"Selected run {run_id} conflicts with summary run {recorded_run}")
    selected = run_id or (str(recorded_run) if recorded_run else None)
    roots = [root]
    candidates: list[Path] = []
    if artifact_dir is not None:
        explicit = artifact_dir.expanduser().resolve(strict=True)
        if not explicit.is_dir():
            raise ValueError(f"Artifact path is not a directory: {explicit}")
        candidates = (
            sorted(p for p in explicit.iterdir() if p.is_dir() and not p.is_symlink())
            if explicit.name == ".sm-artifacts"
            else [explicit]
        )
    elif (root / ".sm-artifacts").is_dir() and not (root / ".sm-artifacts").is_symlink():
        candidates = sorted(
            p for p in (root / ".sm-artifacts").iterdir() if p.is_dir() and not p.is_symlink()
        )
    elif root.name == ".sm-artifacts":
        candidates = sorted(p for p in root.iterdir() if p.is_dir() and not p.is_symlink())
        roots = []
    if selected:
        matches = [p for p in candidates if p.name == selected]
        if candidates and not matches:
            raise ValueError(f"No artifact directory for run {selected}")
        candidates = matches
    if len(candidates) > 1:
        raise ValueError(
            "Multiple artifact runs found; select --run-id: "
            + ", ".join(p.name for p in candidates)
        )
    if candidates:
        selected = selected or candidates[0].name
        roots.extend(p for p in candidates if p not in roots)
    evidence = ReportEvidence(
        selected or (root.name if (root / "validated").is_dir() else "未提供"),
        input_dir=str(root),
    )
    files: list[Path] = []
    seen: set[Path] = set()
    for source_root in roots:
        for directory, dirs, names in os.walk(source_root, followlinks=False):
            base = Path(directory)
            nested_run = _read_json(base / "summary.json").get("run_id") if base != root else None
            if nested_run and (selected is None or str(nested_run) != selected):
                raise ValueError(
                    f"Input contains another run ({nested_run}); "
                    "use that run's own output directory"
                )
            dirs[:] = sorted(
                d
                for d in dirs
                if d not in SKIP_DIRS | {".sm-artifacts"} and not (base / d).is_symlink()
            )
            if len(base.relative_to(source_root).parts) >= 8:
                if dirs:
                    evidence.warnings.append(f"目录深度限制，未读取子目录：{base}")
                dirs[:] = []
            for name in sorted(names):
                path = base / name
                generated = name.lower() in GENERATED and (
                    name.lower() != "migration_report.md" or base == root
                )
                if (
                    path.suffix.lower() not in SUFFIXES
                    or generated
                    or path == exclude
                    or path.is_symlink()
                    or not path.is_file()
                ):
                    continue
                if path not in seen:
                    seen.add(path)
                    files.append(path)

    # Read canonical facts before logs/raw traces if the evidence budget is exhausted.
    def priority(path: Path) -> int:
        if path.name in {"summary.json", "status.json", "run_timeline.json"}:
            return 0
        if "validated" in path.parts:
            return 1
        if path.name in {
            "performance.json",
            "baseline.json",
            "config.json",
            "resource_manifest.json",
        }:
            return 2
        if path.name.endswith(".receipt.json"):
            return 2
        if path.suffix == ".md":
            return 3
        if path.suffix in {".log", ".txt"}:
            return 4
        return 5

    files.sort(key=priority)
    total = 0
    for index, path in enumerate(files):
        if len(evidence.sources) >= max_files or total >= max_total_bytes:
            evidence.warnings.append(f"证据读取达到上限，尚有 {len(files) - index} 个文件未读取。")
            break
        try:
            size = path.stat().st_size
            limit = min(max_file_bytes, max_total_bytes - total)
            with path.open("rb") as stream:
                if size <= limit:
                    raw = stream.read(limit)
                else:
                    head = stream.read(limit // 2)
                    stream.seek(max(0, size - (limit - len(head))))
                    raw = (
                        head
                        + b"\n[... omitted middle bytes ...]\n"
                        + stream.read(limit - len(head))
                    )
            total += min(size, limit)
            truncated = size > limit
            text = redact_report_text(raw.decode("utf-8", errors="replace"))
            data = None
            if path.suffix == ".json" and not truncated:
                try:
                    data = json.loads(text)
                except ValueError:
                    evidence.warnings.append(f"JSON 无法解析，作为文本收录：{path}")
            if truncated:
                evidence.warnings.append(f"文件过长，仅读取首尾片段：{path}")
            # Hash the bytes actually read; truncated sources explicitly identify this scope.
            evidence.sources.append(
                ReportSource(
                    id=f"S{len(evidence.sources) + 1}",
                    path=str(path),
                    text=text,
                    sha256=hashlib.sha256(raw).hexdigest(),
                    size_bytes=size,
                    truncated=truncated,
                    data=data,
                )
            )
        except OSError as exc:
            evidence.warnings.append(f"无法读取 {path}: {exc}")
    if not evidence.sources:
        evidence.warnings.append("没有找到可读取的 Markdown、JSON 或日志证据。")
    return evidence


def flatten(data: Any, pointer: str = "") -> list[tuple[str, Any]]:
    """Retain JSON pointers, list fields and scalar precision."""
    if isinstance(data, dict):
        return [
            item
            for key, value in data.items()
            for item in flatten(
                value, pointer + "/" + str(key).replace("~", "~0").replace("/", "~1")
            )
        ]
    if isinstance(data, list):
        return [(pointer, data)] + [
            item
            for index, value in enumerate(data)
            for item in flatten(value, f"{pointer}/{index}")
        ]
    return [(pointer, data)]
