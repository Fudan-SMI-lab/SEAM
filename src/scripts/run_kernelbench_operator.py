#!/usr/bin/env python3
"""Run one KernelBench operator evaluation and preserve its JSONL traces."""

from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task_dir", type=Path)
    parser.add_argument("solution", type=Path)
    parser.add_argument("kernelbench_root", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()

    task_dir = args.task_dir.expanduser().resolve()
    solution = args.solution.expanduser().resolve()
    kernelbench_root = args.kernelbench_root.expanduser().resolve()
    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "uv", "run", "npu-kernelbench", "eval", str(task_dir),
        "--solution", str(solution), "--runner", "persistent",
        "--timeout", str(args.timeout), "-o", str(output), "--verbose",
    ]
    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    completed = subprocess.run(command, cwd=kernelbench_root, env=env, check=False)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
