#!/usr/bin/env python3
"""Mechanically clean up Python files so a combined pylint run reports nothing.

Pipeline:
  1. Batched formatting on all target files (ruff format, autopep8, ruff check).
  2. A combined silencing pass: run pylint over all files together, let
     pylint-silent add inline disables, and fall back to file-level disables
     for anything that cannot be silenced inline (e.g. long lines inside
     docstrings).

Silencing is done in combined mode on purpose: cross-file checks and astroid
inference differ from per-file runs, and the CI verify step lints all files
together, so silencing must match that to guarantee a clean result.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path


DEFAULT_LOGS_DIR = Path("pylint_autofix_logs")
DEFAULT_LINE_LENGTH = 100
DEFAULT_MAX_SILENCE_LOOPS = 5
DOCSTRING_DISABLE = (
    "missing-module-docstring,missing-class-docstring,missing-function-docstring"
)
# Cross-file checks only surface when pylint scans many files together and
# cannot be fixed mechanically, so they are disabled to allow a clean run.
CROSS_FILE_DISABLE = "duplicate-code,cyclic-import"
PYLINT_DISABLE = f"{DOCSTRING_DISABLE},{CROSS_FILE_DISABLE}"
# Conservative ruff rule set. Categories that frequently REWRITE code in ways
# that can change runtime behavior are intentionally excluded:
#   - SIM (flake8-simplify): e.g. rewrote `row.keys()` to `row` and broke
#     sqlite3.Row iteration.
#   - B   (flake8-bugbear): may alter semantics.
#   - UP  (pyupgrade): may rewrite to newer syntax with subtle behavior shifts.
RUFF_SELECT = "E,F,W,I,PLC,PLE,PLW"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Format the given Python files in place and silence remaining "
            "pylint messages so a combined pylint run is clean "
            "(docstring and cross-file checks are disabled)."
        )
    )
    parser.add_argument(
        "files",
        nargs="+",
        type=Path,
        help="Python files to fix in place.",
    )
    parser.add_argument(
        "--logs-dir",
        type=Path,
        default=DEFAULT_LOGS_DIR,
        help=f"Directory for pylint logs. Defaults to {DEFAULT_LOGS_DIR}.",
    )
    parser.add_argument(
        "--line-length",
        type=int,
        default=DEFAULT_LINE_LENGTH,
        help=f"Line length used by formatters and pylint-silent. Defaults to {DEFAULT_LINE_LENGTH}.",
    )
    parser.add_argument(
        "--max-silence-loops",
        type=int,
        default=DEFAULT_MAX_SILENCE_LOOPS,
        help="Maximum pylint-silent iterations for remaining messages.",
    )
    parser.add_argument(
        "--unsafe-ruff",
        action="store_true",
        help=(
            "Opt in to the ruff --unsafe-fixes pass. Off by default because "
            "unsafe fixes can change runtime behavior."
        ),
    )
    return parser.parse_args()


def run_command(
    command: list[str],
    root: Path,
    env: dict[str, str],
    allow_failure: bool = False,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=root,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0 and not allow_failure:
        print(f"Command failed: {' '.join(command)}", file=sys.stderr)
        print(result.stdout, file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        raise SystemExit(result.returncode)
    return result


def require_tools(root: Path, env: dict[str, str]) -> None:
    tools = {
        "pylint": [sys.executable, "-m", "pylint", "--version"],
        "ruff": ["ruff", "--version"],
        "autopep8": [sys.executable, "-m", "autopep8", "--version"],
        "pylint-silent": ["pylint-silent", "--version"],
    }
    missing: list[str] = []
    for tool, command in tools.items():
        result = run_command(command, root, env, allow_failure=True)
        if result.returncode != 0:
            missing.append(tool)
    if missing:
        print("Missing required tools:", ", ".join(missing), file=sys.stderr)
        print(
            "Install them with: python3 -m pip install ruff autopep8 pylint-silent",
            file=sys.stderr,
        )
        raise SystemExit(2)


def project_env(root: Path) -> dict[str, str]:
    env = os.environ.copy()
    src_path = str(root / "src")
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = src_path if not existing else src_path + os.pathsep + existing
    return env


def resolve_target(root: Path, file_arg: Path) -> Path | None:
    target = (root / file_arg).resolve()
    if not target.is_file():
        print(f"Skipping missing file: {file_arg.as_posix()}", file=sys.stderr)
        return None
    if target.suffix != ".py":
        print(f"Skipping non-Python file: {file_arg.as_posix()}", file=sys.stderr)
        return None
    return target


def relative_paths(root: Path, targets: list[Path]) -> list[str]:
    return [target.relative_to(root).as_posix() for target in targets]


def run_ruff_format(root: Path, paths: list[str], line_length: int, env: dict[str, str]) -> None:
    run_command(
        ["ruff", "format", "--line-length", str(line_length), *paths],
        root,
        env,
        allow_failure=True,
    )


def run_autopep8(root: Path, paths: list[str], line_length: int, env: dict[str, str]) -> None:
    run_command(
        [
            sys.executable,
            "-m",
            "autopep8",
            "--in-place",
            "--aggressive",
            "--aggressive",
            "--max-line-length",
            str(line_length),
            "--select",
            "E501,W291,W292,W293,W391",
            *paths,
        ],
        root,
        env,
        allow_failure=True,
    )


def run_ruff_check(
    root: Path,
    paths: list[str],
    line_length: int,
    env: dict[str, str],
    unsafe: bool = False,
) -> None:
    command = [
        "ruff",
        "check",
        "--fix",
        "--line-length",
        str(line_length),
        "--select",
        RUFF_SELECT,
        *paths,
    ]
    if unsafe:
        command.insert(3, "--unsafe-fixes")
    run_command(command, root, env, allow_failure=True)


def pylint_messages(root: Path, paths: list[str], env: dict[str, str]) -> list[dict]:
    result = run_command(
        [
            sys.executable,
            "-m",
            "pylint",
            *paths,
            f"--disable={PYLINT_DISABLE}",
            "--output-format=json",
            "--score=n",
        ],
        root,
        env,
        allow_failure=True,
    )
    try:
        return json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return []


def write_pylint_text(root: Path, paths: list[str], env: dict[str, str], out_path: Path) -> None:
    result = run_command(
        [
            sys.executable,
            "-m",
            "pylint",
            *paths,
            f"--disable={PYLINT_DISABLE}",
            "--score=n",
        ],
        root,
        env,
        allow_failure=True,
    )
    out_path.write_text(result.stdout + result.stderr, encoding="utf-8")


def apply_pylint_silent(root: Path, text_log: Path, line_length: int, env: dict[str, str]) -> None:
    run_command(
        [
            "pylint-silent",
            "--signature",
            "--max-line-length",
            str(line_length),
            "apply",
            text_log.as_posix(),
        ],
        root,
        env,
        allow_failure=True,
    )


def add_file_level_disable(target: Path, symbols: set[str]) -> None:
    if not symbols:
        return
    source = target.read_text(encoding="utf-8")
    header = "\n".join(source.splitlines()[:20])
    # Only treat a column-0 module-level disable as existing. Inline disables
    # that pylint-silent may have embedded inside a docstring/string are
    # indented and must not fool this check.
    missing_symbols = {
        symbol
        for symbol in symbols
        if not re.search(
            rf"^#\s*pylint:\s*disable=.*\b{re.escape(symbol)}\b", header, re.MULTILINE
        )
    }
    if not missing_symbols:
        return
    disable_line = f"# pylint: disable={','.join(sorted(missing_symbols))}"
    target.write_text(disable_line + "\n" + source, encoding="utf-8")


def silence_combined(
    root: Path,
    targets: list[Path],
    logs_dir: Path,
    args: argparse.Namespace,
    env: dict[str, str],
) -> int:
    paths = relative_paths(root, targets)
    text_log = logs_dir / "combined.txt"

    # Inline silencing with pylint-silent until it stops making progress.
    for _ in range(args.max_silence_loops):
        messages = pylint_messages(root, paths, env)
        if not messages:
            return 0
        write_pylint_text(root, paths, env, text_log)
        apply_pylint_silent(root, text_log, args.line_length, env)
        if len(pylint_messages(root, paths, env)) >= len(messages):
            break

    # File-level fallback for anything pylint-silent cannot fix inline, such as
    # a too-long line inside a docstring (an inline disable comment there is
    # just docstring text and has no effect).
    remaining = pylint_messages(root, paths, env)
    by_file: dict[str, set[str]] = {}
    for message in remaining:
        path = message.get("path")
        symbol = message.get("symbol")
        if path and symbol:
            by_file.setdefault(path, set()).add(symbol)
    for path, symbols in by_file.items():
        add_file_level_disable((root / path), symbols)

    return len(pylint_messages(root, paths, env))


def main() -> int:
    args = parse_args()
    root = Path.cwd().resolve()
    env = project_env(root)
    require_tools(root, env)

    logs_dir = (root / args.logs_dir).resolve()
    logs_dir.mkdir(parents=True, exist_ok=True)

    targets = [resolved for resolved in (resolve_target(root, f) for f in args.files) if resolved]
    if not targets:
        print("No Python files to fix.")
        return 0

    paths = relative_paths(root, targets)
    print(f"Formatting {len(paths)} file(s)...")
    run_ruff_format(root, paths, args.line_length, env)
    run_autopep8(root, paths, args.line_length, env)
    run_ruff_check(root, paths, args.line_length, env, unsafe=False)
    if args.unsafe_ruff:
        run_ruff_check(root, paths, args.line_length, env, unsafe=True)

    print("Silencing remaining pylint messages (combined)...")
    remaining = silence_combined(root, targets, logs_dir, args, env)

    if remaining == 0:
        print("All files clean: combined pylint reports no messages.")
        return 0

    print(f"Combined pylint still reports {remaining} message(s).")
    print(f"Inspect the log at: {(logs_dir / 'combined.txt').as_posix()}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
