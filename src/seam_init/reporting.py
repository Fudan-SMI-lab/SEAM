"""Terminal rendering and atomic owner-only report persistence.

Renders a concise status table and outcome-specific handoff for READY,
PENDING_AUTH, and FAILED. Persists ``.seam-init/last-report.json`` via an
atomic owner-only write containing only enums, paths, hashes, and redacted
summaries — never config bytes, API keys, env snapshots, raw subprocess
output, prompt answers, or exception reprs. Every string is re-redacted at
this boundary even when the source is branded :data:`~seam_init.models.SafeDetail`.
"""
from __future__ import annotations

import json
import os
import secrets
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Final, final

from core.secret_redaction import redact_sensitive_text
from seam_init.models import InitializerOutcome, InitializerStatus, StageRecord
from seam_init.workflow_types import WorkflowFacts

__all__ = ["READY_COMMAND", "persist_report", "render_terminal"]

READY_COMMAND: str = "bash src/scripts/run_seam.sh /path/to/project --server_url http://127.0.0.1:4098"
_REPORT_REL: str = ".seam-init/last-report.json"
_REDACT_SAFETY_BOUND: Final[int] = 4096
_MAX_WARNING: Final[int] = 300
_MAX_DIAGNOSTICS: Final[int] = 8
_MAX_DIAGNOSTIC: Final[int] = 300


def _rd(text: str) -> str:
    """Re-redact at the report boundary (defense-in-depth on SafeDetail)."""
    return redact_sensitive_text(text)


def _br(text: str, bound: int) -> str:
    """Safety-bound, redact, then display-bound (redact before display bound)."""
    redacted = redact_sensitive_text(text[:_REDACT_SAFETY_BOUND])
    suffix = "...[truncated]" if len(redacted) > bound else ""
    return redacted[:bound] + suffix


def _line(label: str, value: str) -> str:
    return f"  {label:<16} {_rd(value)}"


@final
class _Renderer:
    __slots__ = ("_lines",)

    def __init__(self) -> None:
        self._lines: list[str] = []

    def add(self, line: str) -> None:
        self._lines.append(line)

    def sep(self) -> None:
        self._lines.append("-" * 52)

    def table(self, outcome: InitializerOutcome, facts: WorkflowFacts) -> None:
        status_name = outcome.status.value.upper()
        self.add(f"SEAM Initializer — {status_name} (exit {outcome.exit_code})")
        self.sep()
        if facts.environment is not None:
            self.add(_line("Python:", f"{facts.environment.python_executable} ({facts.environment.python_version}, {facts.environment.kind.value})"))
        if facts.seam_status:
            self.add(_line("SEAM:", facts.seam_status))
        if facts.opencode_version:
            self.add(_line("OpenCode:", f"{facts.opencode_version} at {facts.opencode_binary_path}"))
        if facts.bun_version:
            self.add(_line("Bun:", f"{facts.bun_version} at {facts.bun_path}"))
        if facts.omo_action:
            self.add(_line("OMO:", facts.omo_action))
        if facts.provider_model:
            self.add(_line("Model:", facts.provider_model))
        self.add(_line("OC cfg:", f"{facts.opencode_config_path or '(none)'} committed={facts.opencode_config_committed}"))
        if facts.opencode_config_sha256:
            self.add(_line("OC cfg sha256:", f"{facts.opencode_config_sha256[:16]}..."))
        if facts.opencode_transaction_id:
            self.add(_line("OC cfg tx:", facts.opencode_transaction_id))
        self.add(_line("OMO cfg:", f"{facts.omo_config_path or '(none)'} committed={facts.omo_config_committed}"))
        if facts.omo_config_sha256:
            self.add(_line("OMO cfg sha256:", f"{facts.omo_config_sha256[:16]}..."))
        if facts.omo_transaction_id:
            self.add(_line("OMO cfg tx:", facts.omo_transaction_id))
        if facts.omo_version:
            self.add(_line("OMO version:", facts.omo_version))
        if facts.omo_runtime_command:
            self.add(_line("OMO runtime:", facts.omo_runtime_command))
        if facts.omo_live_config_path:
            self.add(_line("OMO live cfg:", facts.omo_live_config_path))
        if facts.backup_policy:
            self.add(_line("Backup policy:", facts.backup_policy))
        if facts.server_url:
            self.add(_line("Server:", f"{facts.server_ownership} at {facts.server_url}"))
        if facts.opencode_validation_fact:
            self.add(_line("OC validate:", facts.opencode_validation_fact))
        if facts.omo_validation_fact:
            self.add(_line("OMO validate:", facts.omo_validation_fact))
        for w in facts.warnings:
            self.add(f"  WARNING: {_br(str(w), _MAX_WARNING)}")
        self.sep()

    def ready(self) -> None:
        self.add("")
        self.add("SEAM setup is READY.")
        self.add("")
        self.add("Run your migration:")
        self.add(f"  {READY_COMMAND}")
        self.add("")
        self.add("Replace /path/to/project with your CUDA project directory.")
        self.add("Optional flags:")
        self.add("  --dashboard       Force the live terminal dashboard on")
        self.add("  --review          Enable the Review Gate")
        self.add("  --seal-manifest   Seal a root run-manifest after a direct run")

    def pending(self) -> None:
        self.add("")
        self.add("SEAM setup is NOT runnable-ready (PENDING_AUTH).")
        self.add("")
        self.add("Required software is installed and structural checks passed,")
        self.add("but provider authentication and/or the billable validation call")
        self.add("were deferred.")
        self.add("")
        self.add("To complete setup:")
        self.add("  1. Provide an API key: rerun the initializer and enter your key,")
        self.add("     or set the environment variable referenced by your answers file.")
        self.add("  2. Consent to a billable validation call when prompted.")
        self.add("  3. Rerun: bash src/scripts/init_seam.sh")
        self.add("  4. Verify: check for exit code 0 and the READY handoff command.")

    def failed(self, outcome: InitializerOutcome) -> None:
        self.add("")
        kind_name = outcome.failure_kind.name if outcome.failure_kind else "UNKNOWN"
        self.add(f"SEAM setup FAILED at step: {kind_name} (exit {outcome.exit_code})")
        self.add("")
        detail = str(outcome.safe_detail).strip()
        if detail:
            self.add(_rd(detail))
            self.add("")
        self.add("Recovery guidance:")
        self.add("  - Address the issue described above.")
        self.add("  - Rerun: bash src/scripts/init_seam.sh")

    def text(self) -> str:
        return "\n".join(self._lines)


def render_terminal(outcome: InitializerOutcome, facts: WorkflowFacts) -> str:
    """Render the complete terminal output for the given outcome."""
    r = _Renderer()
    r.table(outcome, facts)
    match outcome.status:
        case InitializerStatus.READY:
            r.ready()
        case InitializerStatus.PENDING_AUTH:
            r.pending()
        case InitializerStatus.FAILED:
            r.failed(outcome)
    return r.text()


def _fsync_dir(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _atomic_owner_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.{secrets.token_hex(8)}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    fd = os.open(tmp, flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    _fsync_dir(path.parent)
    if os.name == "posix" and stat.S_IMODE(path.lstat().st_mode) != 0o600:
        os.chmod(path, 0o600)


def _serialize_facts(facts: WorkflowFacts) -> dict[str, object]:
    env = facts.environment
    return {
        "python_executable": _rd(env.python_executable) if env else "",
        "python_version": _rd(env.python_version) if env else "",
        "environment_kind": env.kind.value if env else "",
        "seam_status": _rd(facts.seam_status),
        "opencode_version": _rd(facts.opencode_version),
        "opencode_path": _rd(facts.opencode_binary_path),
        "bun_version": _rd(facts.bun_version),
        "bun_path": _rd(facts.bun_path),
        "omo_action": _rd(facts.omo_action),
        "omo_version": _rd(facts.omo_version),
        "omo_runtime_command": _rd(facts.omo_runtime_command),
        "omo_live_config_path": _rd(facts.omo_live_config_path),
        "backup_policy": _rd(facts.backup_policy),
        "opencode_config_committed": facts.opencode_config_committed,
        "opencode_config_path": _rd(facts.opencode_config_path),
        "opencode_config_sha256": _rd(facts.opencode_config_sha256),
        "opencode_transaction_id": _rd(facts.opencode_transaction_id),
        "omo_config_committed": facts.omo_config_committed,
        "omo_config_path": _rd(facts.omo_config_path),
        "omo_config_sha256": _rd(facts.omo_config_sha256),
        "omo_transaction_id": _rd(facts.omo_transaction_id),
        "provider_model": _rd(facts.provider_model),
        "server_url": _rd(facts.server_url),
        "server_ownership": _rd(facts.server_ownership),
        "opencode_validation_fact": _rd(facts.opencode_validation_fact),
        "omo_validation_fact": _rd(facts.omo_validation_fact),
        "warnings": tuple(_br(str(w), _MAX_WARNING) for w in facts.warnings),
        "diagnostics": tuple(
            _br(str(d), _MAX_DIAGNOSTIC) for d in facts.diagnostics[:_MAX_DIAGNOSTICS]),
    }


def _serialize_stages(stages: tuple[StageRecord, ...]) -> list[dict[str, str]]:
    return [{"kind": s.kind.value, "status": s.status.value} for s in stages]


def persist_report(
    project_root: Path, outcome: InitializerOutcome, facts: WorkflowFacts,
) -> None:
    """Atomically write ``.seam-init/last-report.json`` (owner-only, redacted).

    Raises :class:`OSError` on write failure; the caller decides whether to
    suppress. The report contains only enums, paths, hashes, and redacted
    summaries — never config bytes, API keys, or raw subprocess output.
    """
    report = {
        "status": outcome.status.value,
        "exit_code": outcome.exit_code,
        "auth_state": outcome.auth_state.value,
        "billable_consent": outcome.billable_consent.value,
        "stages": _serialize_stages(outcome.stages),
        "failure_kind": outcome.failure_kind.name if outcome.failure_kind else None,
        "safe_detail": _rd(str(outcome.safe_detail)),
        "facts": _serialize_facts(facts),
        "timestamp": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
    }
    _atomic_owner_write(project_root / _REPORT_REL, json.dumps(report, indent=2).encode("utf-8"))
