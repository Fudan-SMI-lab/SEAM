#!/usr/bin/env python3
"""Example scanner for genericity and leakage review in migration notes.

The script is illustrative only. It looks for local paths, private URLs, secrets,
untrusted prompt text, and caller-supplied forbidden terms in shared documentation
files.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

TEXT_SUFFIXES = {".cmake", ".cpp", ".h", ".hpp", ".json", ".md", ".py", ".sh", ".txt", ".yml", ".yaml"}
SKIP_DIRS = {".cache", ".git", ".mypy_cache", ".pytest_cache", ".ruff_cache", "__pycache__", "build", "build_out", "cache", "dist", "examples", "generated", "local_opp", "node_modules"}

LOCAL_PATH_RE = re.compile(r"(?<![A-Za-z0-9_{}$])(?:/(?:home|Users|tmp|var/tmp|mnt|inspire|workspace|root|usr/local)(?:/|\b)|[A-Za-z]:\\(?:Users|Documents and Settings|tmp|temp)\\|~/(?:[^\s`'\"]+))")
PRIVATE_URL_RE = re.compile(r"(?:https?|ssh)://(?:localhost|0\.0\.0\.0|127\.0\.0\.1|\[::1\]|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(?:1[6-9]|2\d|3[0-1])\.\d+\.\d+|[^\s/@]+\.(?:local|lan|internal|corp|intranet))(?:[:/]|\b)", re.IGNORECASE)
SECRET_RE = re.compile(r"(?:BEGIN (?:RSA |OPENSSH |EC |DSA )?PRIVATE KEY|AKIA[0-9A-Z]{16}|ASIA[0-9A-Z]{16}|ghp_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|xox[baprs]-[A-Za-z0-9-]{10,}|sk-[A-Za-z0-9]{20,}|Authorization:\s*Bearer\s+(?!\{\{)[A-Za-z0-9._~+/=-]{12,}|(?:api[_-]?key|secret[_-]?access[_-]?key|access[_-]?token|refresh[_-]?token|password)\s*[:=]\s*['\"]?(?!\{\{)[A-Za-z0-9._~+/=@-]{12,})", re.IGNORECASE)
UNTRUSTED_PROMPT_RE = re.compile(r"(?:ignore (?:all )?(?:previous|prior|above) instructions|disregard (?:all )?(?:previous|prior|above) instructions|developer mode|system prompt|you are now|reveal (?:the )?(?:system|developer) message|<\|(?:system|developer|assistant|user)\|>)", re.IGNORECASE)


@dataclass(frozen=True)
class Finding:
    path: Path
    line: int
    message: str


def iter_text_files(root: Path) -> list[Path]:
    paths: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        paths.append(path)
    return paths


def scan_lines(path: Path, forbidden_terms: list[str]) -> list[Finding]:
    findings: list[Finding] = []
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return findings

    term_patterns = [re.compile(re.escape(term), re.IGNORECASE) for term in forbidden_terms if term]
    for line_no, line in enumerate(text.splitlines(), start=1):
        if LOCAL_PATH_RE.search(line):
            findings.append(Finding(path, line_no, "local path pattern seen in shared notes"))
        if PRIVATE_URL_RE.search(line):
            findings.append(Finding(path, line_no, "private URL pattern seen in shared notes"))
        if SECRET_RE.search(line):
            findings.append(Finding(path, line_no, "secret-like text seen in shared notes"))
        if UNTRUSTED_PROMPT_RE.search(line):
            findings.append(Finding(path, line_no, "prompt-injection style text seen in shared notes"))
        for pattern, term in zip(term_patterns, forbidden_terms):
            if pattern.search(line):
                findings.append(Finding(path, line_no, f"forbidden term seen: {term}"))
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("root", nargs="?", default=".", help="Root directory to scan")
    _ = parser.add_argument("--forbidden-term", action="append", default=[], help="Optional literal term to look for")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    findings: list[Finding] = []
    for path in iter_text_files(root):
        findings.extend(scan_lines(path, list(args.forbidden_term)))

    if findings:
        print("scan_status=needs_review")
        print(f"finding_count={len(findings)}")
        for finding in findings:
            print(f"{finding.path}:{finding.line}: {finding.message}")
        return 1

    print("scan_status=clear")
    print("finding_count=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
