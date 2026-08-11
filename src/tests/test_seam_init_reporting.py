"""Tests for the reporting module: rendering, persistence, secrecy, parser-validity.

Given/When/Then throughout. Uses tmp_path for real filesystem boundaries.
Verifies READY/PENDING_AUTH/FAILED output, atomic owner-only report writes,
zero-secret canary tests, and the parser-valid handoff command.
"""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from seam_init.models import (
    AuthState, BillableCallConsent, FailureKind, InitializerOutcome,
    SafeDetail, StageKind, StageRecord, StageStatus,
)
from seam_init.reporting import READY_COMMAND, persist_report, render_terminal
from seam_init.workflow_types import BACKUP_POLICY, WorkflowFacts
from seam_init.environment import EnvironmentChoice, EnvironmentKind

_STAGES = (
    StageRecord(StageKind.PYTHON_ENVIRONMENT, StageStatus.SUCCEEDED),
    StageRecord(StageKind.SEAM_INSTALL, StageStatus.SUCCEEDED),
    StageRecord(StageKind.OPENCODE_CONFIG, StageStatus.SUCCEEDED),
)


def _facts() -> WorkflowFacts:
    f = WorkflowFacts()
    f.environment = EnvironmentChoice(
        EnvironmentKind.NEW_VENV, "/fake/venv/python", "3.12.0")
    f.seam_status = "installed"
    f.opencode_version = "1.0.180"
    f.opencode_binary_path = "/home/user/.opencode/bin/opencode"
    f.opencode_config_committed = True
    f.opencode_config_path = "/proj/.opencode/opencode.jsonc"
    f.opencode_config_sha256 = "a" * 64
    f.opencode_transaction_id = "tx-oc-1"
    f.provider_model = "openai/gpt-4"
    f.omo_config_committed = True
    f.omo_config_path = "/proj/.omo/omo.jsonc"
    f.omo_config_sha256 = "b" * 64
    f.omo_transaction_id = "tx-omo-1"
    f.omo_version = "5.0.0-beta.5"
    f.omo_runtime_command = "bunx oh-my-openagent"
    f.omo_live_config_path = "/proj/.opencode/oh-my-openagent.jsonc"
    f.backup_policy = "owner-only backups; deleted on commit; restored on rollback"
    f.server_url = "http://127.0.0.1:4098"
    f.server_ownership = "owned"
    f.opencode_validation_fact = "message_ready"
    f.omo_validation_fact = "validated"
    f.auth_state = AuthState.PROVIDED
    f.billable_consent = BillableCallConsent.GIVEN
    return f


class TestRenderReady:
    def test_ready_output_contains_handoff_command(self) -> None:
        outcome = InitializerOutcome.ready(stages=_STAGES)
        text = render_terminal(outcome, _facts())
        assert READY_COMMAND in text

    def test_ready_command_is_parser_valid(self) -> None:
        # Given: the READY_COMMAND string
        # When: we split it into bash tokens
        parts = READY_COMMAND.split()
        # Then: it's a valid bash invocation with run_seam.sh and --server_url
        assert parts[0] == "bash"
        assert any("run_seam.sh" in p for p in parts)
        assert "--server_url" in parts
        url_idx = parts.index("--server_url")
        assert parts[url_idx + 1] == "http://127.0.0.1:4098"

    def test_ready_output_mentions_optional_flags(self) -> None:
        outcome = InitializerOutcome.ready(stages=_STAGES)
        text = render_terminal(outcome, _facts())
        assert "--dashboard" in text
        assert "--review" in text
        assert "--seal-manifest" in text


class TestRenderPendingAuth:
    def test_pending_auth_never_says_success(self) -> None:
        outcome = InitializerOutcome.pending_auth(stages=_STAGES)
        text = render_terminal(outcome, _facts())
        lowered = text.lower()
        assert "success" not in lowered

    def test_pending_auth_production_backup_policy_never_says_success(self) -> None:
        # Given: facts carrying the real production BACKUP_POLICY (not a
        # fixture string that can drift away from the production wording)
        facts = WorkflowFacts(backup_policy=BACKUP_POLICY)
        # When
        text = render_terminal(InitializerOutcome.pending_auth(stages=_STAGES), facts)
        # Then: the complete output (status table included) carries no success
        # wording, while the backup lifecycle meaning stays truthful
        assert "success" not in text.lower()
        assert "Backup policy:" in text
        assert "deleted" in text.lower()
        assert "commit" in text.lower()

    def test_pending_auth_says_not_runnable_ready(self) -> None:
        outcome = InitializerOutcome.pending_auth(stages=_STAGES)
        text = render_terminal(outcome, _facts())
        assert "NOT runnable-ready" in text or "not runnable-ready" in text.lower()

    def test_pending_auth_lists_auth_actions(self) -> None:
        outcome = InitializerOutcome.pending_auth(stages=_STAGES)
        text = render_terminal(outcome, _facts())
        assert "API key" in text or "api key" in text.lower()
        assert "Rerun" in text or "rerun" in text.lower()


class TestRenderFailed:
    def test_failed_names_the_failing_step(self) -> None:
        outcome = InitializerOutcome.failed(
            failure_kind=FailureKind.OPENCODE_CONFIG, stages=_STAGES,
            safe_detail=SafeDetail("schema validation failed"))
        text = render_terminal(outcome, _facts())
        assert "OPENCODE_CONFIG" in text
        assert str(outcome.exit_code) in text

    def test_failed_includes_recovery_guidance(self) -> None:
        outcome = InitializerOutcome.failed(
            failure_kind=FailureKind.SEAM_INSTALL, stages=_STAGES)
        text = render_terminal(outcome, _facts())
        assert "Rerun" in text or "rerun" in text.lower()


class TestPersistReport:
    def test_report_written_atomically(self, tmp_path: Path) -> None:
        outcome = InitializerOutcome.ready(stages=_STAGES)
        persist_report(tmp_path, outcome, _facts())
        report_path = tmp_path / ".seam-init" / "last-report.json"
        assert report_path.is_file()
        data = json.loads(report_path.read_text(encoding="utf-8"))
        assert data["status"] == "ready"
        assert data["exit_code"] == 0

    def test_report_contains_stage_ledger(self, tmp_path: Path) -> None:
        outcome = InitializerOutcome.ready(stages=_STAGES)
        persist_report(tmp_path, outcome, _facts())
        data = json.loads((tmp_path / ".seam-init" / "last-report.json").read_text("utf-8"))
        assert len(data["stages"]) == 3
        assert data["stages"][0]["kind"] == "python_environment"
        assert data["stages"][0]["status"] == "succeeded"

    def test_report_owner_only_on_posix(self, tmp_path: Path) -> None:
        outcome = InitializerOutcome.pending_auth(stages=_STAGES)
        persist_report(tmp_path, outcome, _facts())
        report_path = tmp_path / ".seam-init" / "last-report.json"
        if os.name == "posix":
            mode = stat.S_IMODE(report_path.stat().st_mode)
            assert mode == 0o600

    def test_report_no_secret_canary(self, tmp_path: Path) -> None:
        # Given: a canary secret in the facts
        facts = _facts()
        facts.opencode_version = "sk-canary-1234567890abcdef"
        outcome = InitializerOutcome.ready(stages=_STAGES)
        # When
        persist_report(tmp_path, outcome, facts)
        raw = (tmp_path / ".seam-init" / "last-report.json").read_bytes()
        # Then: zero canary characters in the report
        assert b"sk-canary" not in raw
        assert b"1234567890abcdef" not in raw

    def test_report_no_env_snapshot(self, tmp_path: Path) -> None:
        outcome = InitializerOutcome.ready(stages=_STAGES)
        persist_report(tmp_path, outcome, _facts())
        data = json.loads((tmp_path / ".seam-init" / "last-report.json").read_text("utf-8"))
        # Must not contain environment snapshot keys
        assert "env" not in data
        assert "environment_snapshot" not in data
        assert "os_environ" not in data

    def test_report_no_config_bytes(self, tmp_path: Path) -> None:
        outcome = InitializerOutcome.ready(stages=_STAGES)
        persist_report(tmp_path, outcome, _facts())
        data = json.loads((tmp_path / ".seam-init" / "last-report.json").read_text("utf-8"))
        assert "config_bytes" not in data
        assert "opencode_config_content" not in data

    def test_report_write_failure_raises_oserror(self, tmp_path: Path) -> None:
        # Given: make .seam-init a file instead of a directory (causes mkdir to fail)
        seam_init = tmp_path / ".seam-init"
        seam_init.write_text("blocker", encoding="utf-8")
        outcome = InitializerOutcome.ready(stages=_STAGES)
        # When / Then: persist raises OSError
        with pytest.raises(OSError):
            persist_report(tmp_path, outcome, _facts())

    def test_report_overwrites_previous(self, tmp_path: Path) -> None:
        outcome1 = InitializerOutcome.pending_auth(stages=_STAGES)
        persist_report(tmp_path, outcome1, _facts())
        outcome2 = InitializerOutcome.ready(stages=_STAGES)
        persist_report(tmp_path, outcome2, _facts())
        data = json.loads((tmp_path / ".seam-init" / "last-report.json").read_text("utf-8"))
        assert data["status"] == "ready"


class TestRenderCanarySecrecy:
    def test_forged_safedetail_does_not_leak_in_render(self) -> None:
        # Given: a forged SafeDetail with a canary
        facts = _facts()
        facts.opencode_version = "sk-canary-SECRET123456"
        outcome = InitializerOutcome.failed(
            failure_kind=FailureKind.OPENCODE_CONFIG,
            stages=_STAGES,
            safe_detail=SafeDetail("sk-canary-SECRET123456 in detail"))
        # When
        text = render_terminal(outcome, facts)
        # Then: zero canary characters
        assert "sk-canary" not in text
        assert "SECRET123456" not in text


class TestRenderTruthfulFacts:
    def test_table_shows_provider_model(self) -> None:
        # Given / When
        text = render_terminal(InitializerOutcome.ready(stages=_STAGES), _facts())
        # Then
        assert "openai/gpt-4" in text

    def test_table_shows_config_path_hash_and_transaction(self) -> None:
        # Given / When
        text = render_terminal(InitializerOutcome.ready(stages=_STAGES), _facts())
        # Then
        assert "/proj/.opencode/opencode.jsonc" in text
        assert "aaaaaaaa" in text
        assert "tx-oc-1" in text

    def test_table_shows_omo_version_runtime_and_live_path(self) -> None:
        # Given / When
        text = render_terminal(InitializerOutcome.ready(stages=_STAGES), _facts())
        # Then
        assert "5.0.0-beta.5" in text
        assert "bunx oh-my-openagent" in text
        assert "/proj/.opencode/oh-my-openagent.jsonc" in text

    def test_table_shows_backup_policy(self) -> None:
        # Given / When
        text = render_terminal(InitializerOutcome.ready(stages=_STAGES), _facts())
        # Then
        assert "backup" in text.lower()
        assert "owner-only" in text

    def test_table_shows_doctor_and_message_facts(self) -> None:
        # Given / When
        text = render_terminal(InitializerOutcome.ready(stages=_STAGES), _facts())
        # Then
        assert "message_ready" in text
        assert "validated" in text


class TestReportFactsCompleteness:
    def test_json_carries_omo_metadata_and_backup_policy(self, tmp_path: Path) -> None:
        # Given
        outcome = InitializerOutcome.ready(stages=_STAGES)
        # When
        persist_report(tmp_path, outcome, _facts())
        data = json.loads((tmp_path / ".seam-init" / "last-report.json").read_text("utf-8"))
        facts = data["facts"]
        # Then
        assert facts["provider_model"] == "openai/gpt-4"
        assert facts["omo_version"] == "5.0.0-beta.5"
        assert facts["omo_runtime_command"] == "bunx oh-my-openagent"
        assert facts["omo_live_config_path"] == "/proj/.opencode/oh-my-openagent.jsonc"
        assert facts["backup_policy"]
        assert facts["opencode_transaction_id"] == "tx-oc-1"
        assert facts["omo_transaction_id"] == "tx-omo-1"

    def test_json_diagnostics_bounded_and_redacted(self, tmp_path: Path) -> None:
        # Given: more diagnostics than the bound, one carrying a canary
        facts = _facts()
        facts.diagnostics = tuple(
            [SafeDetail(f"diag-{i}") for i in range(32)]
            + [SafeDetail("token sk-canary-DIAG1234567890 tail")])
        outcome = InitializerOutcome.failed(
            failure_kind=FailureKind.OMO_VALIDATION, stages=_STAGES)
        # When
        persist_report(tmp_path, outcome, facts)
        raw = (tmp_path / ".seam-init" / "last-report.json").read_bytes()
        data = json.loads(raw.decode("utf-8"))
        diags = data["facts"]["diagnostics"]
        # Then: bounded count, bounded length, zero canary characters
        assert len(diags) <= 8
        assert all(len(str(d)) <= 320 for d in diags)
        assert b"sk-canary" not in raw
        assert b"DIAG1234567890" not in raw

    def test_json_warnings_bounded_and_redacted(self, tmp_path: Path) -> None:
        # Given: a warning carrying a canary and one very long warning
        facts = _facts()
        facts.warnings = (
            "api_key=sk-canary-WARN1234567890",
            "w" * 5000,
        )
        outcome = InitializerOutcome.ready(stages=_STAGES)
        # When
        persist_report(tmp_path, outcome, facts)
        raw = (tmp_path / ".seam-init" / "last-report.json").read_bytes()
        data = json.loads(raw.decode("utf-8"))
        warnings = data["facts"]["warnings"]
        # Then
        assert b"sk-canary" not in raw
        assert b"WARN1234567890" not in raw
        assert all(len(str(w)) <= 340 for w in warnings)
        assert not any("w" * 5000 in str(w) for w in warnings)
