"""CLI entry point for SM-Adapt v2 — CUDA to NPU migration orchestrator."""

import argparse
import os
import sys
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    """Build and return the argument parser."""
    parser = argparse.ArgumentParser(
        prog="sm_adapt_cli",
        description="SM-Adapt v2: CUDA-to-NPU migration CLI",
        epilog="Example: python sm_adapt_cli.py --project-dir /path/to/cuda-project --command 'Migrate to NPU'",
    )

    parser.add_argument(
        "--project-dir",
        required=True,
        type=str,
        help="Path to CUDA project to migrate",
    )

    parser.add_argument(
        "--workflow",
        required=False,
        default=None,
        type=str,
        help="Path to workflow YAML definition (default: workflows/npu_migration_v1.yaml relative to this script, overridable via SEAM_DEFAULT_WORKFLOW env var)",
    )

    parser.add_argument(
        "--opencode-url",
        required=False,
        default="http://127.0.0.1:4096",
        type=str,
        help="OpenCode server URL (default: http://127.0.0.1:4096)",
    )

    parser.add_argument(
        "--command",
        required=False,
        default=None,
        type=str,
        help="User instruction for migration",
    )

    parser.add_argument(
        "--resume",
        action="store_true",
        default=False,
        help="Resume from latest checkpoint",
    )

    parser.add_argument(
        "--verbose",
        action="store_true",
        default=False,
        help="Enable detailed logging",
    )

    parser.add_argument(
        "--max-iterations",
        required=False,
        default=5,
        type=int,
        help="Phase 5 maximum iterations (default: 5)",
    )

    parser.add_argument(
        "--user-constraints",
        type=str,
        default=None,
        help=(
            "User-defined constraints for the migration. "
            "Accepts either a direct string or a file path (e.g. ADAPTATION_REQUIREMENTS.md). "
            "If a file path is given, its contents are read and used as the constraint text."
        ),
    )

    return parser


def get_default_workflow() -> str:
    """Return the default workflow YAML path relative to this script.

    Can be overridden via the SEAM_DEFAULT_WORKFLOW environment variable.
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))
    return os.environ.get(
        "SEAM_DEFAULT_WORKFLOW",
        os.path.join(script_dir, "..", "workflows", "npu_migration_v1.yaml"),
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments. Returns parsed namespace."""
    parser = build_parser()

    if argv is not None:
        args = parser.parse_args(argv)
    else:
        args = parser.parse_args()

    # Resolve default workflow path if not explicitly provided
    if args.workflow is None:
        args.workflow = get_default_workflow()

    return args


def print_progress(phase: str, status: str, detail: str = "") -> None:
    """Print phase progress to console."""
    icon = {
        "running": "[~]",
        "success": "[+]",
        "failed": "[x]",
        "skipped": "[-]",
    }.get(status, "[-]")

    msg = f"{icon} Phase: {phase} — {status}"
    if detail:
        msg += f" | {detail}"
    print(msg)


def _resolve_user_constraints(raw: str | None) -> str:
    """Resolve user constraints from a direct string or a file path.
    
    Args:
        raw: Either a constraint string or a path to a constraint file.
        
    Returns:
        The constraint text, or empty string if raw is None/empty.
    """
    if not raw:
        return ""
    path = Path(raw)
    if path.is_file():
        return path.read_text(encoding="utf-8").strip()
    return raw.strip()


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
    except SystemExit as e:
        code = e.code
        if code is None:
            return 2
        if isinstance(code, str):
            return int(code) if code.isdigit() else 2
        return code

    user_constraints = _resolve_user_constraints(args.user_constraints)

    if args.verbose:
        print(f"[INFO] Project directory: {args.project_dir}")
        print(f"[INFO] Workflow: {args.workflow}")
        print(f"[INFO] OpenCode URL: {args.opencode_url}")
        print(f"[INFO] Resume: {args.resume}")
        print(f"[INFO] Max iterations: {args.max_iterations}")
        if args.command:
            print(f"[INFO] Command: {args.command}")
        if args.user_constraints:
            print(f"[INFO] User constraints: {args.user_constraints}")

    # TODO: Integrate with orchestrator when ready
    # NOTE: `user_constraints` should be passed to `orchestrator.run_workflow()`
    print_progress("Initialization", "success", "CLI arguments parsed successfully")
    print_progress("Execution", "skipped", "Orchestrator integration pending")

    return 0


if __name__ == "__main__":
    sys.exit(main())
