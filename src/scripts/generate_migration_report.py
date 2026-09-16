"""Generate one unified Markdown report without rerunning migration."""

from __future__ import annotations

import argparse
from pathlib import Path

from core.migration_report import generate_migration_report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input", required=True, type=Path, help="One run's output/report directory"
    )
    parser.add_argument(
        "--artifact-dir", type=Path, help="Explicit .sm-artifacts/<run-id> directory"
    )
    parser.add_argument("--run-id", help="Required if input contains several artifact runs")
    parser.add_argument("--output", type=Path, help="Default: <input>/MIGRATION_REPORT.md")
    parser.add_argument(
        "--project-dir", default="", help="Recorded model project directory (metadata only)"
    )
    parser.add_argument("--max-file-bytes", type=int, default=262_144)
    parser.add_argument("--max-total-bytes", type=int, default=4_194_304)
    args = parser.parse_args()
    try:
        path = generate_migration_report(
            args.input,
            output=args.output,
            artifact_dir=args.artifact_dir,
            run_id=args.run_id,
            project_dir=args.project_dir,
            max_file_bytes=args.max_file_bytes,
            max_total_bytes=args.max_total_bytes,
        )
    except (OSError, ValueError) as exc:
        parser.exit(2, f"Report generation failed: {exc}\n")
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
