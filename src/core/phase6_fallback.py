from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


PHASE6_DEFAULT_TIMEOUT = 3600
PHASE6_TIMEOUT_CONFIG_KEYS = (
    "session_timeout_phase6",
    "session_timeout_phase_6",
    "session_timeout_report",
)


PHASE6_PRIOR_PHASE_IDS: tuple[str, ...] = (
    "phase_0_env_detect",
    "phase_1_project_analysis",
    "phase_1_5_constraint_summary",
    "phase_2_venv_create",
    "phase_3_entry_script",
    "phase_35_static_validate",
    "phase_4_rule_migration",
    "phase_5_validation",
)


def collect_phase6_prior_artifacts(artifact_store: Any) -> dict[str, Any]:
    outputs: dict[str, Any] = {}
    for phase_id in PHASE6_PRIOR_PHASE_IDS:
        try:
            data = artifact_store.load_phase_output(phase_id)
        except Exception:
            data = None
        if data is not None:
            outputs[phase_id] = data
    return outputs


def collect_phase6_prior_state(state: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in state.items()
        if key in PHASE6_PRIOR_PHASE_IDS and isinstance(value, dict)
    }


def resolve_phase6_timeout(
    framework_config: dict[str, Any],
    phase_timeout: int | None,
    logger: Any,
) -> int:
    if phase_timeout is not None:
        return int(phase_timeout)
    for key in PHASE6_TIMEOUT_CONFIG_KEYS:
        raw_value = framework_config.get(key)
        if raw_value is None:
            continue
        try:
            return int(raw_value)
        except (TypeError, ValueError):
            logger.warning("Invalid %s=%r for phase_6_report; using default %s", key, raw_value, PHASE6_DEFAULT_TIMEOUT)
            break
    return PHASE6_DEFAULT_TIMEOUT


def build_phase6_fallback_report(
    *,
    project_dir: str,
    report_dir: str,
    prior_outputs: dict[str, Any],
    reason: str,
) -> dict[str, Any]:
    report_root = Path(report_dir)
    report_root.mkdir(parents=True, exist_ok=True)

    phase_statuses = _phase_statuses(prior_outputs)
    files_migrated, files_skipped = _migration_file_counts(prior_outputs)
    phase5_status = phase_statuses.get("phase_5_validation", "missing")
    phase5_success = phase5_status in {"success", "passed"}
    migration_summary = {
        "overall_status": "pass" if phase5_success else "partial",
        "migration_success": phase5_success,
        "files_migrated": files_migrated,
        "files_skipped": files_skipped,
        "phase6_fallback": True,
        "fallback_reason": reason,
        "phase4_strategy": _phase4_strategy(prior_outputs),
        "phase5_status": phase5_status,
        "phase5_terminal_failure": not phase5_success,
        "completed_phase_count": sum(1 for status in phase_statuses.values() if status != "missing"),
    }

    report_payloads = {
        "SUMMARY_REPORT.md": _summary_report(project_dir, reason, phase_statuses, migration_summary),
        "TOOLS_EXECUTION_REPORT.md": _tools_report(prior_outputs),
        "OPENCODE_OPERATIONS_LOG.md": _operations_report(reason, phase_statuses),
    }
    report_paths: list[str] = []
    for filename, content in report_payloads.items():
        path = report_root / filename
        _atomic_write_text(path, content)
        report_paths.append(str(path))

    return {
        "phase_id": "phase_6_report",
        "report_paths": report_paths,
        "migration_summary": migration_summary,
        "project_dir": str(project_dir),
        "fallback": True,
        "fallback_reason": reason,
    }


def _phase_statuses(prior_outputs: dict[str, Any]) -> dict[str, str]:
    statuses: dict[str, str] = {}
    for phase_id in PHASE6_PRIOR_PHASE_IDS:
        value = prior_outputs.get(phase_id)
        if not isinstance(value, dict):
            statuses[phase_id] = "missing"
            continue
        raw_status = value.get("status") or value.get("overall_status")
        if isinstance(raw_status, str) and raw_status.strip():
            statuses[phase_id] = raw_status.strip()
            continue
        exit_code = value.get("script_exit_code")
        if exit_code == 0:
            statuses[phase_id] = "success"
        elif isinstance(exit_code, int):
            statuses[phase_id] = "failure"
        else:
            statuses[phase_id] = "available"
    return statuses


def _phase4_strategy(prior_outputs: dict[str, Any]) -> str:
    phase4 = prior_outputs.get("phase_4_rule_migration")
    if isinstance(phase4, dict):
        for key in ("strategy", "strategy_file", "rule_migration_strategy"):
            value = phase4.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return "unknown"


def _migration_file_counts(prior_outputs: dict[str, Any]) -> tuple[int, int]:
    phase4 = prior_outputs.get("phase_4_rule_migration")
    if not isinstance(phase4, dict):
        return 0, 0
    result = phase4.get("result")
    summary = result.get("summary") if isinstance(result, dict) else phase4.get("summary")
    if not isinstance(summary, dict):
        return 0, 0
    migrated = _int_value(
        summary.get("files_migrated"),
        summary.get("modified_files"),
        summary.get("files_modified"),
        summary.get("total_replacements"),
    )
    skipped = _int_value(summary.get("files_skipped"), summary.get("skipped_files"))
    return migrated, skipped


def _int_value(*values: Any) -> int:
    for value in values:
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return max(0, value)
        if isinstance(value, str):
            try:
                return max(0, int(value))
            except ValueError:
                continue
    return 0


def _summary_report(
    project_dir: str,
    reason: str,
    phase_statuses: dict[str, str],
    migration_summary: dict[str, Any],
) -> str:
    lines = [
        "# Migration Summary Report",
        "",
        "This report was generated by the deterministic Phase 6 fallback renderer.",
        "The LLM report step did not complete, so this report only summarizes stored phase artifacts.",
        "",
        "## Status",
        f"- Overall status: {migration_summary['overall_status']}",
        f"- Fallback reason: {reason}",
        f"- Project directory: {project_dir}",
        f"- Phase 4 strategy: {migration_summary['phase4_strategy']}",
        f"- Phase 5 status: {migration_summary['phase5_status']}",
        f"- Migration success: {migration_summary['migration_success']}",
        f"- Phase 5 terminal failure: {migration_summary['phase5_terminal_failure']}",
        "",
        "## Phase Artifacts",
    ]
    for phase_id, status in phase_statuses.items():
        lines.append(f"- {phase_id}: {status}")
    lines.append("")
    return "\n".join(lines)


def _tools_report(prior_outputs: dict[str, Any]) -> str:
    lines = [
        "# Tools Execution Report",
        "",
        "This fallback report is derived from canonical phase outputs and does not invoke an LLM.",
        "",
        "## Available Phase Outputs",
    ]
    for phase_id in PHASE6_PRIOR_PHASE_IDS:
        value = prior_outputs.get(phase_id)
        if isinstance(value, dict):
            keys = ", ".join(sorted(str(key) for key in value.keys())[:12])
            lines.append(f"- {phase_id}: available ({keys})")
        else:
            lines.append(f"- {phase_id}: missing")
    lines.append("")
    return "\n".join(lines)


def _operations_report(reason: str, phase_statuses: dict[str, str]) -> str:
    payload = {
        "phase_6_report": {
            "status": "fallback",
            "reason": reason,
        },
        "phase_statuses": phase_statuses,
    }
    return "# OpenCode Operations Log\n\n```json\n" + json.dumps(payload, indent=2, sort_keys=True) + "\n```\n"


def _atomic_write_text(path: Path, content: str) -> None:
    tmp_path = path.with_name(path.name + ".tmp")
    try:
        tmp_path.write_text(content, encoding="utf-8")
        os.replace(tmp_path, path)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        finally:
            raise
