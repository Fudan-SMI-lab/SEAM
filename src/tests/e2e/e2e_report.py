from __future__ import annotations

import argparse
import difflib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

MAX_DIFF_LINES_PER_FILE = 200
MAX_PREVIEW_CHARS = 120


def _load_json(path: Path) -> dict | None:
    if not path.is_file():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _truncate(text: str | None, limit: int = MAX_PREVIEW_CHARS) -> str:
    if not text:
        return "-"
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def _diff_snippet(old: str, new: str, label: str) -> str:
    old_lines = old.splitlines(keepends=True)
    new_lines = new.splitlines(keepends=True)
    diff = list(
        difflib.unified_diff(
            old_lines, new_lines, fromfile=f"before/{label}", tofile=f"after/{label}", n=3
        )
    )
    if not diff:
        return "No changes detected."
    if len(diff) > MAX_DIFF_LINES_PER_FILE:
        diff = diff[:MAX_DIFF_LINES_PER_FILE]
        diff.append(f"\n... (truncated, {MAX_DIFF_LINES_PER_FILE} lines shown)\n")
    return "```diff\n" + "".join(diff) + "```"


def _lines_changed(old: str, new: str) -> tuple[int, int]:
    added = 0
    removed = 0
    for line in difflib.unified_diff(old.splitlines(), new.splitlines(), n=0):
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return removed, added


def _build_overview(summary: dict) -> list[str]:
    lines: list[str] = []
    lines.append("# migration_utils E2E Migration Test Report")
    lines.append("")
    lines.append("## Run Overview")
    lines.append("")
    run_id = summary.get("run_id", "N/A")
    lines.append(f"- **Run ID:** `{run_id}`")
    lines.append(f"- **Workflow:** {summary.get('workflow_path', 'N/A')}")
    status = summary.get("overall_status", "N/A")
    status_badge = f":heavy_check_mark: **{status}**" if status == "PASS" else f":x: **{status}**"
    lines.append(f"- **Overall status:** {status_badge}")
    duration = summary.get("total_duration_seconds", 0)
    lines.append(f"- **Total duration:** {duration:.2f}s")
    ts = datetime.now(timezone.utc).isoformat()
    lines.append(f"- **Report generated at:** {ts}")
    lines.append(f"- **Sessions created:** {summary.get('session_count', 'N/A')}")
    lines.append(f"- **Commands sent:** {summary.get('command_count', 'N/A')}")
    entry = summary.get("entry_script")
    if entry:
        lines.append(f"- **Entry script:** `{entry}`")
    lines.append("")
    return lines


def _build_phase_table(summary: dict) -> list[str]:
    lines: list[str] = []
    lines.append("## Phase-by-Phase Breakdown")
    lines.append("")
    lines.append("| # | Phase | Status | Duration | Error |")
    lines.append("|---|-------|--------|----------|-------|")
    phases = summary.get("phases", [])
    if not isinstance(phases, list) or not phases:
        lines.append("| N/A | No phase data available | N/A | N/A | N/A |")
    else:
        for p in phases:
            num = p.get("phase_number", "?")
            label = p.get("label", p.get("phase_id", "?"))
            status = p.get("status", "UNKNOWN")
            dur = f"{p.get('duration_seconds', 0):.2f}s"
            err = _truncate(p.get("error"), 60)
            lines.append(f"| {num} | {label} | {status.upper()} | {dur} | {err} |")
    lines.append("")
    return lines


# pylint: disable-next=too-many-locals; silent
def _build_session_command_table(telemetry: dict) -> list[str]:
    lines: list[str] = []
    lines.append("## Session & Command Telemetry")
    lines.append("")

    sessions = telemetry.get("sessions", [])
    if sessions:
        lines.append(f"**{len(sessions)} session(s) created:**")
        lines.append("")
        lines.append("| Role | Lifecycle | Commands | Phases |")
        lines.append("|------|-----------|----------|--------|")
        for s in sessions:
            sid = s.get("session_id", "?")[:16]
            role = s.get("role", "?")
            lc = s.get("lifecycle", "?")
            cmds = s.get("command_count", 0)
            ph = ", ".join(s.get("phases", [])[:3])
            lines.append(f"| `{sid}` | {role} | {lc} | {cmds} | {ph} |")
        lines.append("")

    commands = telemetry.get("commands", [])
    if commands:
        lines.append(f"**{len(commands)} command(s) sent:**")
        lines.append("")
        lines.append("| # | Phase | Duration | Status | Prompt Preview | Response Preview |")
        lines.append("|---|-------|----------|--------|----------------|------------------|")
        for cmd in commands:
            seq = cmd.get("sequence", "?")
            phase = cmd.get("phase_id", "-")
            dur = f"{cmd.get('duration_seconds', 0):.2f}s"
            status = cmd.get("status", "?").upper()
            prompt = _truncate(cmd.get("command_preview"))
            resp = _truncate(cmd.get("response_preview"))
            lines.append(f"| {seq} | {phase} | {dur} | {status} | {prompt} | {resp} |")
        lines.append("")

    return lines


def _build_code_diff(before: dict | None, after: dict | None) -> list[str]:
    lines: list[str] = []
    lines.append("## Code Migration Diff")
    lines.append("")

    if not before or not after:
        lines.append("Code snapshots unavailable — no diff analysis possible.")
        lines.append("")
        return lines

    all_files = sorted(set(list(before.keys()) + list(after.keys())))
    if not all_files:
        lines.append("No .py files found in snapshots.")
        lines.append("")
        return lines

    changed_count = 0
    for filename in all_files:
        old_entry = before.get(filename)
        new_entry = after.get(filename)

        old_content = old_entry.get("content", "") if old_entry else ""
        new_content = new_entry.get("content", "") if new_entry else ""

        lines.append(f"### File: `{filename}`")
        lines.append("")

        if old_entry and not new_entry:
            lines.append("- :red_circle: **Removed** (existed before, not after migration)")
            lines.append("")
            continue
        if not old_entry and new_entry:
            lines.append("- :green_circle: **Added** (did not exist before)")
            removed, added = _lines_changed("", new_content)
            lines.append(f"- Lines: {removed} removed, {added} added")
            lines.append("")
            lines.append("```diff\n+ (file content omitted for brevity)")
            lines.append("```")
            lines.append("")
            continue

        old_sha = old_entry.get("sha256", "")
        new_sha = new_entry.get("sha256", "")
        if old_sha == new_sha:
            lines.append("- No changes detected.")
            lines.append("")
            continue

        changed_count += 1
        removed, added = _lines_changed(old_content, new_content)
        lines.append(f"- SHA256: `{old_sha[:16]}…` → `{new_sha[:16]}…`")
        lines.append(f"- Lines: {removed} removed, {added} added")
        lines.append("")
        lines.append(_diff_snippet(old_content, new_content, filename))
        lines.append("")

    lines.append(
        # pylint: disable-next=line-too-long; silent
        f"**Summary:** {changed_count} file(s) modified, {len(all_files) - changed_count} file(s) unchanged."
    )
    lines.append("")
    return lines


def _build_artifacts_summary(summary: dict) -> list[str]:
    lines: list[str] = []
    lines.append("## Artifacts & Errors")
    lines.append("")
    artifact_dir = summary.get("artifact_dir")
    if artifact_dir:
        lines.append(f"- **Artifacts directory:** `{artifact_dir}`")
    before_snap = summary.get("before_snapshot_path")
    after_snap = summary.get("after_snapshot_path")
    if before_snap:
        lines.append(f"- **Before snapshot:** `{before_snap}`")
    if after_snap:
        lines.append(f"- **After snapshot:** `{after_snap}`")
    lines.append("")

    errors = summary.get("errors", [])
    if errors:
        lines.append("### Errors")
        lines.append("")
        for err in errors:
            lines.append(f"- {err}")
        lines.append("")

    return lines


def _build_recommendations(
    # pylint: disable-next=unused-argument; silent
    summary: dict | None, telemetry: dict | None, phase_results_data: dict | None
) -> list[str]:
    lines: list[str] = []
    lines.append("## Recommendations")
    lines.append("")
    recommendations: list[str] = []

    if not summary:
        lines.append("- No summary data available for analysis.")
        lines.append("")
        return lines

    phases = summary.get("phases", [])
    failed_phases = [p for p in phases if p.get("status") != "passed"]
    if failed_phases:
        for fp in failed_phases:
            recommendations.append(
                # pylint: disable-next=line-too-long; silent
                f"- **{fp.get('label', fp.get('phase_id', '?'))}** failed: {fp.get('error', 'Unknown')}"
            )

    overall = summary.get("overall_status", "FAIL")
    if overall == "PASS" and not failed_phases:
        recommendations.append(
            "- All phases completed successfully. The migration pipeline is functioning correctly."
        )

    if (
        summary
        and summary.get("error_history")
        or (isinstance(phase_results_data, dict) and phase_results_data.get("error_history"))
    ):
        recommendations.append(
            # pylint: disable-next=line-too-long; silent
            "- Phase 5 encountered errors during validation. Review repair session logs for root causes."
        )

    if not recommendations:
        recommendations.append(
            "- No specific recommendations. Review the report data for further insights."
        )

    lines.extend(recommendations)
    lines.append("")
    return lines


def build_report(output_dir: Path, report_path: Path | None = None) -> Path:
    output_dir = Path(output_dir)
    if report_path is None:
        report_path = output_dir / "e2e_report.md"

    summary = _load_json(output_dir / "summary.json")
    telemetry = _load_json(output_dir / "telemetry.json")
    before = _load_json(output_dir / "before_snapshot.json")
    after = _load_json(output_dir / "after_snapshot.json")
    phase_results = _load_json(output_dir / "phase_results.json")

    all_lines: list[str] = []
    if summary:
        all_lines.extend(_build_overview(summary))
        all_lines.extend(_build_phase_table(summary))
    else:
        all_lines.append("# migration_utils E2E Migration Test Report")
        all_lines.append("")
        all_lines.append("## Run Overview")
        all_lines.append("")
        all_lines.append("No summary data available from this run.")
        all_lines.append("")

    if telemetry:
        all_lines.extend(_build_session_command_table(telemetry))

    all_lines.extend(_build_code_diff(before, after))

    if summary:
        all_lines.extend(_build_artifacts_summary(summary))

    all_lines.extend(_build_recommendations(summary, telemetry, phase_results))

    _ = report_path.parent.mkdir(parents=True, exist_ok=True)
    _ = report_path.write_text("\n".join(all_lines), encoding="utf-8")
    return report_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate a Markdown report from E2E test JSON artifacts."
    )
    _ = parser.add_argument(
        "output_dir", type=Path, help="Directory containing summary.json, telemetry.json, etc."
    )
    _ = parser.add_argument(
        "report_path",
        type=Path,
        nargs="?",
        default=None,
        help="Output report path (default: output_dir/e2e_report.md)",
    )
    args = parser.parse_args()
    generated = build_report(args.output_dir, args.report_path)
    print(f"Report generated: {generated}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
