"""Tests for the SEAM initializer workflow state machine.

Given/When/Then throughout. Fakes at the typed adapter boundary; real
filesystem/ConfigTransaction under tmp_path. No real network/subprocess/creds.
"""
from __future__ import annotations

import inspect
import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import FrozenInstanceError
from hashlib import sha256
from pathlib import Path
from typing import final

import pytest

from core.jsonc import JsonValue
from seam_init.answers import Answers, load_answers
from seam_init.environment import InterpreterInfo, SafetyReport
from seam_init.models import (
    AuthState, BillableCallConsent, EnvironmentKind, FailureKind, InitializerContractError,
    InitializerFailure, InitializerOutcome, InitializerStatus, SafeDetail,
    StageKind, StageStatus,
)
from seam_init.omo_config import DryRunResult, DryRunStatus, SchemaCapability
from seam_init.omo_install import (
    BunRuntime, BunVersion, SubscriptionSelection, TriState,
)
from seam_init.omo_validation import OmoCommandResult
from seam_init.opencode_install import (
    InstallAction, InstallOutcome, OpencodeBinary, OpencodeVersion,
)
from seam_init.opencode_runtime_types import (
    DiagnoseResult, OwnedProcessRef,
)
from seam_init.opencode_validation import VersionResult
from seam_init.workflow import run_workflow
from seam_init.workflow_ledger import StageLedger
from seam_init.workflow_types import (
    ConfirmOnlyPort, NonInteractivePromptReached, WorkflowFacts, WorkflowPorts,
    WorkflowRequest, refresh_config_facts,
)

_PROJECT_CONFIG = json.dumps({
    "$schema": "https://opencode.ai/config.json",
    "provider": {"openai": {"models": {"gpt-4": {}}}},
})
_REASONING = ("off", "minimal", "low", "medium", "high", "xhigh", "max", "auto")


def _info() -> InterpreterInfo:
    return InterpreterInfo(
        "/fake/python", "3.12.0", (3, 12, 0), True, "/fake/prefix",
        "/fake/base", True, False, True, False)


class _FakeVenv:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Path]] = []
    def create(self, python: str, target: Path) -> str:
        self.calls.append((python, target))
        return "/fake/venv/python"


class _FakeProbe:
    def __init__(self, info: InterpreterInfo | None = None) -> None:
        self._info = info or _info()
        self.calls: list[str] = []
    def probe(self, python_path: str) -> InterpreterInfo:
        self.calls.append(python_path)
        return self._info


class _FakePip:
    def __init__(self, *, satisfied: bool = True, fail: bool = False) -> None:
        self._satisfied = satisfied
        self._fail = fail
        self.calls: list[tuple[str, ...]] = []
    def run(self, argv: Sequence[str]):
        self.calls.append(tuple(argv))
        if self._fail:
            from seam_init.seam_install import PipRunResult
            return PipRunResult(argv=tuple(argv), returncode=1, stdout=SafeDetail(""), stderr=SafeDetail("pip fail"))
        from seam_init.seam_install import PipRunResult
        if "show" in argv and self._satisfied:
            return PipRunResult(argv=tuple(argv), returncode=0, stdout=SafeDetail("Editable project location: /repo/src"), stderr=SafeDetail(""))
        return PipRunResult(argv=tuple(argv), returncode=0, stdout=SafeDetail("ok"), stderr=SafeDetail(""))


class _ScriptedPort:
    """Interactive prompt double: pops scripted answers, records every call."""

    def __init__(self, *, asks: Sequence[str] = (), confirms: Sequence[bool] = (),
                 secrets: Sequence[str] = ()) -> None:
        self._asks = list(asks)
        self._confirms = list(confirms)
        self._secrets = list(secrets)
        self.ask_calls: list[str] = []
        self.confirm_calls: list[str] = []
        self.secret_calls: list[str] = []

    def ask(self, prompt: str, *, default: str | None = None) -> str:
        self.ask_calls.append(prompt)
        assert self._asks, f"unscripted ask: {prompt!r}"
        return self._asks.pop(0)

    def secret(self, prompt: str) -> str:
        self.secret_calls.append(prompt)
        assert self._secrets, f"unscripted secret: {prompt!r}"
        return self._secrets.pop(0)

    def confirm(self, prompt: str, *, default: bool = False) -> bool:
        self.confirm_calls.append(prompt)
        assert self._confirms, f"unscripted confirm: {prompt!r}"
        return self._confirms.pop(0)


class _EOFPort:
    """Prompt port double that raises EOFError (Ctrl-D) on every prompt."""

    def ask(self, prompt: str, *, default: str | None = None) -> str:
        raise EOFError

    def secret(self, prompt: str) -> str:
        raise EOFError

    def confirm(self, prompt: str, *, default: bool = False) -> bool:
        raise EOFError


class _RecordingNIPort:
    """Non-interactive guard: confirms auto-True; ask/secret recorded + raise."""

    def __init__(self) -> None:
        self.ask_calls: list[str] = []
        self.secret_calls: list[str] = []
        self.confirm_calls: list[str] = []

    def ask(self, prompt: str, *, default: str | None = None) -> str:
        self.ask_calls.append(prompt)
        raise NonInteractivePromptReached(f"ask() reached: {prompt!r}")

    def secret(self, prompt: str) -> str:
        self.secret_calls.append(prompt)
        raise NonInteractivePromptReached(f"secret() reached: {prompt!r}")

    def confirm(self, prompt: str, *, default: bool = False) -> bool:
        self.confirm_calls.append(prompt)
        return True


@final
class _PortSpy:
    def __init__(self, ports: WorkflowPorts) -> None:
        self._ports = ports

    def assert_zero_calls(self) -> None:
        p = self._ports
        assert isinstance(p.venv_creator, _FakeVenv)
        assert isinstance(p.pip_runner, _FakePip)
        assert isinstance(p.opencode_installer, _FakeOcInstaller)
        assert isinstance(p.bun_installer, _FakeBun)
        assert isinstance(p.omo_command, _FakeOmoCmd)
        assert isinstance(p.diagnose_runner, _FakeDiagnose)
        assert p.venv_creator.calls == []
        assert p.pip_runner.calls == []
        assert p.opencode_installer.calls == []
        assert p.bun_installer.calls == []
        assert p.omo_command.calls == []
        assert p.diagnose_runner.calls == []


class _FakeRuntime:
    def __init__(self, project_root: Path, *, models=("openai/gpt-4",), config_ok: bool = True) -> None:
        self._root = project_root
        self._models = models
        self._config_ok = config_ok
    def debug_config(self) -> dict[str, JsonValue] | None:
        if not self._config_ok:
            return None
        return {"configpath": str((self._root / ".opencode/opencode.jsonc").resolve())}
    def debug_models(self, config_bytes: bytes | None = None) -> tuple[str, ...] | None:
        return self._models


class _FakeValidator:
    def __init__(self, *, valid: bool = True) -> None:
        self._valid = valid
    def validate(self, config_bytes: bytes) -> bool:
        return self._valid


class _FakeSub:
    def select(self, provider_id: str) -> SubscriptionSelection:
        return SubscriptionSelection(
            claude=TriState.YES if "claude" in provider_id.lower() else TriState.NO,
            openai="openai" in provider_id.lower(), gemini=False, copilot=False,
            opencode_zen=False, zai_coding_plan=False, opencode_go=False,
            kimi_for_coding=False, bailian_coding_plan=False,
            minimax_cn_coding_plan=False, minimax_coding_plan=False,
            vercel_ai_gateway=False)


class _FakeOcInstaller:
    def __init__(self, *, fail: bool = False, refuse: bool = False) -> None:
        self._fail = fail
        self._refuse = refuse
        self.calls: list[str] = []
    def install(self) -> InstallOutcome:
        self.calls.append("install")
        if self._fail:
            raise InitializerFailure(
                kind=FailureKind.OPENCODE_INSTALL, safe_detail=SafeDetail("install fail"))
        if self._refuse:
            return InstallOutcome(action=InstallAction.REFUSED, refusal_reason=SafeDetail("user declined"))
        return InstallOutcome(
            action=InstallAction.INSTALLED,
            binary=OpencodeBinary(path=Path("/fake/opencode"), version_text="1.0.180", version=OpencodeVersion(1, 0, 180)))


class _FakeBun:
    def __init__(self, *, fail: bool = False) -> None:
        self._fail = fail
        self.calls: list[tuple[str, ...]] = []
    def install_bun(self, argv: Sequence[str]) -> None:
        self.calls.append(tuple(argv))
        if self._fail:
            raise InitializerFailure(
                kind=FailureKind.OMO_INSTALL, safe_detail=SafeDetail("bun install fail"))
    def install_omo(self, argv: Sequence[str]) -> str:
        self.calls.append(tuple(argv))
        if self._fail:
            raise InitializerFailure(
                kind=FailureKind.OMO_INSTALL, safe_detail=SafeDetail("omo install fail"))
        return ""


class _FakeRegistrar:
    def __init__(self, *, has: bool = True, warning: str = "") -> None:
        self._has = has
        self._warning = warning
    def has_plugin(self, plugin: str) -> bool:
        return self._has
    def register_plugin(self, plugin: str) -> str:
        return self._warning


class _FakeCap:
    def __init__(self, *, available: bool = True) -> None:
        self._available = available
    def resolve_capability(self) -> SchemaCapability | None:
        if not self._available:
            return None
        return SchemaCapability(
            schema_url="https://example.com/omo.schema.json",
            reasoning_values=_REASONING, version="1.0.0",
            schema_document={"type": "object"})
    def migrate_dry_run(self) -> DryRunResult:
        return DryRunResult(status=DryRunStatus.UNSUPPORTED, output=None)


class _FakeLifecycle:
    def __init__(self) -> None:
        self.started: list[OwnedProcessRef] = []
        self.stopped: list[OwnedProcessRef] = []
    def start(self, argv: Sequence[str], *, env: Mapping[str, str], cwd: str) -> OwnedProcessRef:
        ref = OwnedProcessRef(id=len(self.started) + 1)
        self.started.append(ref)
        return ref
    def stop(self, ref: OwnedProcessRef) -> SafeDetail:
        self.stopped.append(ref)
        return SafeDetail("stopped")
    def is_running(self, ref: OwnedProcessRef) -> bool:
        return ref not in self.stopped


class _FailingLifecycle(_FakeLifecycle):
    """Lifecycle whose stop() raises for the first `failures` attempts."""

    def __init__(self, *, failures: int) -> None:
        super().__init__()
        self._failures = failures

    def stop(self, ref: OwnedProcessRef) -> SafeDetail:
        if self._failures > 0:
            self._failures -= 1
            raise RuntimeError("simulated stop failure")
        return super().stop(ref)


class _FakeDiagnose:
    def __init__(self, *, message_ready: bool = True, basic_rc: int = 0, env_rc: int = 0,
                 start_owned: bool = False, message_script: Sequence[str] | None = None) -> None:
        self.message_ready = message_ready
        self._basic_rc = basic_rc
        self.env_rc = env_rc
        self.start_owned = start_owned
        self._basic_calls = 0
        self._script = list(message_script) if message_script is not None else None
        self.calls: list[tuple[str, ...]] = []
    def run(self, argv: Sequence[str], *, env: Mapping[str, str] | None = None) -> DiagnoseResult:
        self.calls.append(tuple(argv))
        mode = _mode(argv)
        if mode == "env":
            return DiagnoseResult(tuple(argv), self.env_rc, SafeDetail(""), SafeDetail(""))
        if mode == "basic":
            self._basic_calls += 1
            if self.start_owned and self._basic_calls == 1:
                return DiagnoseResult(tuple(argv), 40, SafeDetail(""), SafeDetail(""))
            return DiagnoseResult(tuple(argv), self._basic_rc, SafeDetail(""), SafeDetail(""))
        if mode == "message":
            scripted = self._script.pop(0) if self._script else None
            if scripted == "auth":
                probe = {"message_probe": {"message": {"status": 401},
                                           "session_id": "s1", "cleanup": {"ok": True}}}
                return DiagnoseResult(tuple(argv), 43, SafeDetail(json.dumps(probe)), SafeDetail("auth"))
            if scripted == "ready" or (scripted is None and self.message_ready):
                probe = {"message_probe": {"response_text": "SEAM_DIAG_OK", "contains_marker": True,
                                            "cleanup": {"ok": True}, "session_id": "s1"}}
                return DiagnoseResult(tuple(argv), 0, SafeDetail(json.dumps(probe)), SafeDetail(""))
            return DiagnoseResult(tuple(argv), 43, SafeDetail("{}"), SafeDetail("fail"))
        return DiagnoseResult(tuple(argv), 0, SafeDetail(""), SafeDetail(""))


def _mode(argv: Sequence[str]) -> str:
    for i, a in enumerate(argv):
        if a == "--mode" and i + 1 < len(argv):
            return argv[i + 1]
    return ""


class _FakeVersion:
    def __init__(self, *, ok: bool = True) -> None:
        self._ok = ok
    def check(self, executable: str) -> VersionResult:
        return VersionResult(ok=self._ok, version=SafeDetail("1.0.180"))


class _FakeOmoCmd:
    def __init__(self, *, validated: bool = True, doctor_ok: bool = True,
                 config_invalid: bool = False) -> None:
        self.validated = validated
        self.doctor_ok = doctor_ok
        self.config_invalid = config_invalid
        self.calls: list[tuple[str, ...]] = []
    def run(self, argv: Sequence[str], *, env: Mapping[str, str] | None = None, timeout: float) -> OmoCommandResult:
        self.calls.append(tuple(argv))
        if "doctor" in argv:
            if self.doctor_ok and self.config_invalid:
                doc = {"exitCode": 0, "target": "opencode",
                        "systemInfo": {"configValid": False, "configPath": "/p", "pluginVersion": "1.0"},
                        "results": [{"name": n, "status": "pass"} for n in
                                     ("System", "Configuration", "TUI Plugin", "Models")],
                        "tools": {"available": [], "enabled": []},
                        "summary": {"total": 4, "passed": 4, "failed": 0, "warnings": 0}}
                return OmoCommandResult(tuple(argv), 0, json.dumps(doc), SafeDetail(""))
            if self.doctor_ok:
                doc = {"exitCode": 0, "target": "opencode",
                        "systemInfo": {"configValid": True, "configPath": "/p", "pluginVersion": "1.0"},
                        "results": [{"name": n, "status": "pass"} for n in
                                     ("System", "Configuration", "TUI Plugin", "Models")],
                        "tools": {"available": [], "enabled": []},
                        "summary": {"total": 4, "passed": 4, "failed": 0, "warnings": 0}}
                return OmoCommandResult(tuple(argv), 0, json.dumps(doc), SafeDetail(""))
            return OmoCommandResult(tuple(argv), 1, "{}", SafeDetail("doctor fail"))
        if "run" in argv:
            if self.validated:
                run = {"success": True, "sessionId": "s1", "messageCount": 1,
                        "durationMs": 100, "summary": "SEAM_OMO_OK"}
                return OmoCommandResult(tuple(argv), 0, json.dumps(run), SafeDetail(""))
            return OmoCommandResult(tuple(argv), 1, "{}", SafeDetail("run fail"))
        return OmoCommandResult(tuple(argv), 1, "", SafeDetail("unknown"))


def _ports(tmp_path: Path, **overrides) -> WorkflowPorts:
    import dataclasses
    base = WorkflowPorts(
        venv_creator=_FakeVenv(), interpreter_probe=_FakeProbe(),
        pip_runner=_FakePip(), opencode_installer=_FakeOcInstaller(),
        opencode_runtime=_FakeRuntime(tmp_path), schema_validator=_FakeValidator(),
        subscription_selector=_FakeSub(),
        bun_installer=_FakeBun(), omo_installer=_FakeBun(),
        plugin_registrar=_FakeRegistrar(), omo_capability=_FakeCap(),
        server_lifecycle=_FakeLifecycle(), diagnose_runner=_FakeDiagnose(),
        version_probe=_FakeVersion(), omo_command=_FakeOmoCmd())
    return dataclasses.replace(base, **overrides)


def _req_ni(tmp_path: Path, ports: WorkflowPorts, **kw) -> WorkflowRequest:
    answers = kw.pop("answers", Answers(
        provider_id="openai", model_id="gpt-4", api_key_env="SEAM_TEST_KEY",
        billable_consent=True))
    return WorkflowRequest(
        project_root=tmp_path, seam_source_path=tmp_path / "src",
        prompt=kw.pop("prompt", ConfirmOnlyPort()), ports=ports, answers=answers,
        provider_selection=kw.pop("selection", None),
        base_env=dict(os.environ), **kw)


@pytest.fixture
def setup_config(tmp_path: Path) -> Path:
    oc = tmp_path / ".opencode"
    oc.mkdir(parents=True, exist_ok=True)
    (oc / "opencode.jsonc").write_text(_PROJECT_CONFIG, encoding="utf-8")
    return tmp_path


@pytest.fixture(autouse=True)
def _auto_patch(monkeypatch, tmp_path) -> None:
    fake_oc = tmp_path / "fake_opencode"
    fake_oc.write_bytes(b"fake-opencode-binary")
    fake_bun = tmp_path / "fake_bun"
    fake_bun.write_bytes(b"fake-bun-binary")
    monkeypatch.setattr("seam_init.opencode_install.detect_opencode", lambda: OpencodeBinary(
        path=fake_oc, version_text="1.0.180", version=OpencodeVersion(1, 0, 180)))
    monkeypatch.setattr("seam_init.omo_install.detect_bun", lambda: BunRuntime(
        path=fake_bun, version_text="1.1.0", version=BunVersion(1, 1, 0)))


def _no_in_progress(outcome: InitializerOutcome) -> bool:
    return all(s.status is not StageStatus.IN_PROGRESS for s in outcome.stages)


class TestWorkflowReady:
    def test_ready_happy_path(self, setup_config, monkeypatch) -> None:
        # Given
        monkeypatch.setenv("SEAM_TEST_KEY", "test-key-value")
        ports = _ports(setup_config)
        req = _req_ni(setup_config, ports)
        facts = WorkflowFacts()
        # When
        outcome = run_workflow(req, facts_out=facts)
        # Then
        assert outcome.status is InitializerStatus.READY
        assert outcome.exit_code == 0
        assert facts.provider_model == "openai/gpt-4"
        assert _no_in_progress(outcome)

    def test_ready_facts_contain_config_paths_and_hashes(self, setup_config, monkeypatch) -> None:
        # Given
        monkeypatch.setenv("SEAM_TEST_KEY", "k")
        ports = _ports(setup_config)
        req = _req_ni(setup_config, ports)
        facts = WorkflowFacts()
        # When
        run_workflow(req, facts_out=facts)
        # Then
        assert "opencode.jsonc" in facts.opencode_config_path
        assert "omo.jsonc" in facts.omo_config_path
        assert len(facts.opencode_config_sha256) == 64
        assert facts.opencode_transaction_id != ""


class TestWorkflowPendingAuth:
    def test_skip_key_yields_pending_auth(self, setup_config) -> None:
        # Given: no API key env var
        ports = _ports(setup_config)
        req = _req_ni(setup_config, ports, answers=Answers(
            provider_id="openai", model_id="gpt-4", billable_consent=False))
        # When
        outcome = run_workflow(req)
        # Then
        assert outcome.status is InitializerStatus.PENDING_AUTH
        assert outcome.exit_code == 60
        assert _no_in_progress(outcome)

    def test_skip_key_makes_zero_billable_calls(self, setup_config) -> None:
        # Given
        ports = _ports(setup_config)
        omo_cmd = ports.omo_command
        assert isinstance(omo_cmd, _FakeOmoCmd)
        req = _req_ni(setup_config, ports, answers=Answers(
            provider_id="openai", model_id="gpt-4", billable_consent=False))
        # When
        run_workflow(req)
        # Then: doctor called (structural), run NOT called (billable)
        doctor_calls = [c for c in omo_cmd.calls if "doctor" in c]
        run_calls = [c for c in omo_cmd.calls if "run" in c]
        assert len(doctor_calls) >= 1
        assert len(run_calls) == 0

    def test_declined_consent_yields_pending_auth(self, setup_config, monkeypatch) -> None:
        # Given: key provided but billable consent declined
        monkeypatch.setenv("SEAM_TEST_KEY", "test-key-value")
        ports = _ports(setup_config)
        req = _req_ni(setup_config, ports, answers=Answers(
            provider_id="openai", model_id="gpt-4",
            api_key_env="SEAM_TEST_KEY", billable_consent=False))
        # When
        outcome = run_workflow(req)
        # Then
        assert outcome.status is InitializerStatus.PENDING_AUTH
        assert outcome.exit_code == 60

    def test_pending_auth_validation_stages_skipped_not_succeeded(self, setup_config) -> None:
        # Given
        ports = _ports(setup_config)
        req = _req_ni(setup_config, ports, answers=Answers(
            provider_id="openai", model_id="gpt-4", billable_consent=False))
        # When
        outcome = run_workflow(req)
        # Then: validation stages are SKIPPED, not SUCCEEDED
        val_stages = [s for s in outcome.stages if s.kind in (StageKind.OPENCODE_VALIDATION, StageKind.OMO_VALIDATION)]
        for s in val_stages:
            assert s.status is StageStatus.SKIPPED


class TestWorkflowSelection:
    def test_non_openai_provider_survives_to_facts(self, setup_config, monkeypatch) -> None:
        # Given: anthropic provider/model
        monkeypatch.setenv("SEAM_TEST_KEY", "k")
        config = json.loads(_PROJECT_CONFIG)
        config["provider"]["anthropic"] = {"models": {"claude-sonnet-4": {}}}
        (setup_config / ".opencode" / "opencode.jsonc").write_text(json.dumps(config), encoding="utf-8")
        ports = _ports(setup_config, opencode_runtime=_FakeRuntime(
            setup_config, models=("anthropic/claude-sonnet-4",)))
        req = _req_ni(setup_config, ports, answers=Answers(
            provider_id="anthropic", model_id="claude-sonnet-4",
            api_key_env="SEAM_TEST_KEY", billable_consent=True))
        facts = WorkflowFacts()
        # When
        outcome = run_workflow(req, facts_out=facts)
        # Then: the selected provider/model survived into facts
        assert facts.provider_model == "anthropic/claude-sonnet-4"
        assert outcome.status is InitializerStatus.READY

    def test_incomplete_answers_no_default_model(self, setup_config) -> None:
        # Given: provider_id without model_id
        ports = _ports(setup_config)
        req = _req_ni(setup_config, ports, answers=Answers(provider_id="openai"))
        # When
        outcome = run_workflow(req)
        # Then: workflow fails (does not silently default to gpt-4)
        assert outcome.status is InitializerStatus.FAILED
        assert outcome.exit_code in range(61, 70)


class TestWorkflowFailures:
    def test_seam_install_failure_maps_to_62(self, setup_config) -> None:
        ports = _ports(setup_config, pip_runner=_FakePip(fail=True))
        req = _req_ni(setup_config, ports)
        outcome = run_workflow(req)
        assert outcome.exit_code == 62
        assert _no_in_progress(outcome)

    def test_opencode_install_failure_maps_to_63(self, setup_config, monkeypatch) -> None:
        # Given
        monkeypatch.setenv("SEAM_TEST_KEY", "k")
        ports = _ports(setup_config, opencode_installer=_FakeOcInstaller(fail=True))
        req = _req_ni(setup_config, ports)
        # When
        outcome = run_workflow(req)
        # Then
        assert outcome.exit_code == 63
        assert _no_in_progress(outcome)

    def test_opencode_config_failure_maps_to_64(self, setup_config, monkeypatch) -> None:
        monkeypatch.setenv("SEAM_TEST_KEY", "k")
        ports = _ports(setup_config, schema_validator=_FakeValidator(valid=False))
        req = _req_ni(setup_config, ports)
        outcome = run_workflow(req)
        assert outcome.exit_code == 64
        assert _no_in_progress(outcome)

    def test_omo_install_failure_maps_to_65(self, setup_config, monkeypatch) -> None:
        # Given: bun detection returns None → install fails
        monkeypatch.setenv("SEAM_TEST_KEY", "k")
        monkeypatch.setattr("seam_init.omo_install.detect_bun", lambda: None)
        ports = _ports(setup_config, bun_installer=_FakeBun(fail=True))
        req = _req_ni(setup_config, ports)
        # When
        outcome = run_workflow(req)
        # Then
        assert outcome.exit_code == 65
        assert _no_in_progress(outcome)

    def test_omo_config_failure_maps_to_66(self, setup_config, monkeypatch) -> None:
        monkeypatch.setenv("SEAM_TEST_KEY", "k")
        ports = _ports(setup_config, omo_capability=_FakeCap(available=False))
        req = _req_ni(setup_config, ports)
        outcome = run_workflow(req)
        assert outcome.exit_code == 66
        assert _no_in_progress(outcome)

    def test_opencode_runtime_failure_maps_to_67(self, setup_config, monkeypatch) -> None:
        monkeypatch.setenv("SEAM_TEST_KEY", "k")
        ports = _ports(setup_config, diagnose_runner=_FakeDiagnose(env_rc=1))
        req = _req_ni(setup_config, ports)
        outcome = run_workflow(req)
        assert outcome.exit_code == 67
        assert _no_in_progress(outcome)

    def test_opencode_validation_terminal_maps_to_68(self, setup_config, monkeypatch) -> None:
        monkeypatch.setenv("SEAM_TEST_KEY", "k")
        ports = _ports(setup_config, diagnose_runner=_FakeDiagnose(message_ready=False))
        req = _req_ni(setup_config, ports)
        outcome = run_workflow(req)
        assert outcome.exit_code == 68
        assert _no_in_progress(outcome)

    def test_omo_validation_failure_maps_to_69(self, setup_config, monkeypatch) -> None:
        monkeypatch.setenv("SEAM_TEST_KEY", "k")
        ports = _ports(setup_config, omo_command=_FakeOmoCmd(doctor_ok=False))
        req = _req_ni(setup_config, ports)
        outcome = run_workflow(req)
        assert outcome.exit_code == 69
        assert _no_in_progress(outcome)

    def test_python_environment_failure_maps_to_61(self, tmp_path) -> None:
        # Given: new venv target with non-existent parent (provider/model are
        # supplied so up-front answer validation passes and the environment
        # stage is the one that fails)
        ports = _ports(tmp_path)
        req = _req_ni(tmp_path, ports, answers=Answers(
            provider_id="openai", model_id="gpt-4",
            environment="new", venv_path=str(tmp_path / "nodir" / "sub" / "venv")))
        # When
        outcome = run_workflow(req)
        # Then
        assert outcome.exit_code == 61


class TestWorkflowCleanup:
    def test_owned_server_closed_on_success(self, setup_config, monkeypatch) -> None:
        monkeypatch.setenv("SEAM_TEST_KEY", "k")
        ports = _ports(setup_config, diagnose_runner=_FakeDiagnose(start_owned=True))
        lc = ports.server_lifecycle
        assert isinstance(lc, _FakeLifecycle)
        req = _req_ni(setup_config, ports)
        run_workflow(req)
        assert len(lc.stopped) >= 1

    def test_owned_server_closed_on_failure(self, setup_config, monkeypatch) -> None:
        monkeypatch.setenv("SEAM_TEST_KEY", "k")
        ports = _ports(setup_config, diagnose_runner=_FakeDiagnose(start_owned=True, message_ready=False))
        lc = ports.server_lifecycle
        assert isinstance(lc, _FakeLifecycle)
        req = _req_ni(setup_config, ports)
        run_workflow(req)
        assert len(lc.stopped) >= 1

    def test_owned_server_closed_on_interrupt(self, setup_config, monkeypatch) -> None:
        # Given
        monkeypatch.setenv("SEAM_TEST_KEY", "k")
        ports = _ports(setup_config, diagnose_runner=_FakeDiagnose(start_owned=True))
        lc = ports.server_lifecycle
        assert isinstance(lc, _FakeLifecycle)

        class _InterruptingDiagnose:
            def __init__(self, inner):
                self._inner = inner
                self.calls: list[tuple[str, ...]] = []
            def run(self, argv, *, env=None):
                self.calls.append(tuple(argv))
                if _mode(argv) == "message":
                    raise KeyboardInterrupt
                return self._inner.run(argv, env=env)

        ports2 = WorkflowPorts(
            **{f: getattr(ports, f) for f in ports.__dataclass_fields__})
        ports2 = _ports(setup_config, diagnose_runner=_InterruptingDiagnose(
            _FakeDiagnose(start_owned=True)),
            server_lifecycle=lc)
        req = _req_ni(setup_config, ports2)
        # When
        outcome = run_workflow(req)
        # Then: interrupt now maps to FAILED, not PENDING_AUTH
        assert outcome.status is InitializerStatus.FAILED
        assert len(lc.stopped) >= 1


class TestOwnedCleanupFailureOutcome:
    def test_ready_plus_persistent_close_failure_returns_failed_67(
            self, setup_config, monkeypatch) -> None:
        # Given: happy-path request; an owned server starts; every stop fails
        monkeypatch.setenv("SEAM_TEST_KEY", "k")
        lifecycle = _FailingLifecycle(failures=10)
        ports = _ports(setup_config, diagnose_runner=_FakeDiagnose(start_owned=True),
                       server_lifecycle=lifecycle)
        facts = WorkflowFacts()
        # When
        outcome = run_workflow(_req_ni(setup_config, ports), facts_out=facts)
        # Then: the terminal result reflects failed cleanup - never READY/0
        # while the owned process is known to remain alive
        assert outcome.status is InitializerStatus.FAILED
        assert outcome.exit_code == 67
        assert outcome.failure_kind is FailureKind.OPENCODE_RUNTIME
        assert lifecycle.stopped == []
        assert any("cleanup" in w for w in facts.warnings)
        assert _no_in_progress(outcome)

    def test_pending_auth_plus_persistent_close_failure_returns_failed_67(
            self, setup_config) -> None:
        # Given: auth deferred (no API key); owned server; stop always fails
        lifecycle = _FailingLifecycle(failures=10)
        ports = _ports(setup_config, diagnose_runner=_FakeDiagnose(start_owned=True),
                       server_lifecycle=lifecycle)
        req = _req_ni(setup_config, ports, answers=Answers(
            provider_id="openai", model_id="gpt-4", billable_consent=False))
        # When
        outcome = run_workflow(req)
        # Then: a non-FAILED validation outcome must not mask the live process
        assert outcome.status is InitializerStatus.FAILED
        assert outcome.exit_code == 67
        assert outcome.failure_kind is FailureKind.OPENCODE_RUNTIME

    def test_close_retry_success_preserves_ready(
            self, setup_config, monkeypatch) -> None:
        # Given: owned server; the first stop attempt fails, the retry succeeds
        monkeypatch.setenv("SEAM_TEST_KEY", "k")
        lifecycle = _FailingLifecycle(failures=1)
        ports = _ports(setup_config, diagnose_runner=_FakeDiagnose(start_owned=True),
                       server_lifecycle=lifecycle)
        # When
        outcome = run_workflow(_req_ni(setup_config, ports))
        # Then: the bounded retry stopped the process, so READY is preserved
        assert outcome.status is InitializerStatus.READY
        assert outcome.exit_code == 0
        assert len(lifecycle.stopped) == 1

    def test_validation_failure_plus_close_failure_keeps_original_68(
            self, setup_config, monkeypatch) -> None:
        # Given: message validation fails (68) AND owned cleanup also fails
        monkeypatch.setenv("SEAM_TEST_KEY", "k")
        lifecycle = _FailingLifecycle(failures=10)
        ports = _ports(setup_config,
                       diagnose_runner=_FakeDiagnose(start_owned=True, message_ready=False),
                       server_lifecycle=lifecycle)
        # When
        outcome = run_workflow(_req_ni(setup_config, ports))
        # Then: the outcome is already FAILED - the original, more specific
        # failure kind is kept and cleanup is never reported as READY
        assert outcome.status is InitializerStatus.FAILED
        assert outcome.exit_code == 68

    def test_reused_foreign_server_never_stopped_and_ready_preserved(
            self, setup_config, monkeypatch) -> None:
        # Given: a ready foreign server (no owned start anywhere in the run)
        monkeypatch.setenv("SEAM_TEST_KEY", "k")
        ports = _ports(setup_config)
        lifecycle = ports.server_lifecycle
        assert isinstance(lifecycle, _FakeLifecycle)
        # When
        outcome = run_workflow(_req_ni(setup_config, ports))
        # Then: the external/reused server is never stopped; READY unchanged
        assert outcome.status is InitializerStatus.READY
        assert outcome.exit_code == 0
        assert lifecycle.started == []
        assert lifecycle.stopped == []


class TestWorkflowLedger:
    def test_no_in_progress_in_any_outcome(self, setup_config, monkeypatch) -> None:
        # Given: verify all outcome types have zero IN_PROGRESS
        monkeypatch.setenv("SEAM_TEST_KEY", "k")
        ports_ready = _ports(setup_config)
        ports_fail = _ports(setup_config, pip_runner=_FakePip(fail=True))
        ports_skip = _ports(setup_config)
        # When
        ready = run_workflow(_req_ni(setup_config, ports_ready))
        failed = run_workflow(_req_ni(setup_config, ports_fail))
        skip = run_workflow(_req_ni(setup_config, ports_skip, answers=Answers(
            provider_id="openai", model_id="gpt-4", billable_consent=False)))
        # Then
        for outcome in (ready, failed, skip):
            assert _no_in_progress(outcome), [s for s in outcome.stages if s.status is StageStatus.IN_PROGRESS]

    def test_no_duplicate_stage_kinds(self, setup_config, monkeypatch) -> None:
        # Given
        monkeypatch.setenv("SEAM_TEST_KEY", "k")
        ports = _ports(setup_config)
        # When
        outcome = run_workflow(_req_ni(setup_config, ports))
        # Then: each StageKind appears at most once
        kinds = [s.kind for s in outcome.stages]
        assert len(kinds) == len(set(kinds)), f"duplicate kinds: {kinds}"


class TestWorkflowOutcomeFreeze:
    def test_outcome_is_frozen(self, setup_config, monkeypatch) -> None:
        monkeypatch.setenv("SEAM_TEST_KEY", "k")
        ports = _ports(setup_config)
        outcome = run_workflow(_req_ni(setup_config, ports))
        with pytest.raises(FrozenInstanceError):
            setattr(outcome, "exit_code", 99)

    def test_ready_requires_provided_auth(self) -> None:
        with pytest.raises(InitializerContractError):
            InitializerOutcome(
                status=InitializerStatus.READY, auth_state=AuthState.SKIPPED,
                billable_consent=BillableCallConsent.GIVEN, stages=())

    def test_pending_auth_constraint(self) -> None:
        with pytest.raises(InitializerContractError):
            InitializerOutcome(
                status=InitializerStatus.PENDING_AUTH, auth_state=AuthState.PROVIDED,
                billable_consent=BillableCallConsent.GIVEN, stages=())


class TestWorkflowReprSecrecy:
    def test_workflow_ports_repr_no_canary(self, setup_config) -> None:
        # Given: forge a port with canary in its repr
        class _CanaryPort:
            def __repr__(self):
                return "sk-canary-SECRET"
            def install(self):
                return _FakeOcInstaller().install()
        ports = _ports(setup_config, opencode_installer=_CanaryPort())
        # When
        r = repr(ports)
        # Then
        assert "sk-canary" not in r
        assert "SECRET" not in r

    def test_workflow_facts_repr_no_canary(self) -> None:
        # Given: forge diagnostics with canary
        facts = WorkflowFacts()
        facts.diagnostics = (SafeDetail("sk-canary-LEAKED"),)
        # When
        r = repr(facts)
        # Then
        assert "sk-canary" not in r
        assert "LEAKED" not in r


class TestStageLedgerInvariants:

    def test_begin_while_current_raises(self) -> None:
        # Given
        ledger = StageLedger()
        ledger.begin(StageKind.PYTHON_ENVIRONMENT)
        # When / Then
        with pytest.raises(RuntimeError):
            ledger.begin(StageKind.SEAM_INSTALL)

    def test_begin_duplicate_kind_raises(self) -> None:
        # Given
        ledger = StageLedger()
        ledger.begin(StageKind.PYTHON_ENVIRONMENT)
        ledger.complete(StageStatus.SUCCEEDED)
        # When / Then
        with pytest.raises(RuntimeError):
            ledger.begin(StageKind.PYTHON_ENVIRONMENT)

    def test_complete_without_begin_raises(self) -> None:
        # Given / When / Then
        with pytest.raises(RuntimeError):
            StageLedger().complete(StageStatus.SUCCEEDED)

    def test_complete_in_progress_rejected(self) -> None:
        # Given
        ledger = StageLedger()
        ledger.begin(StageKind.PYTHON_ENVIRONMENT)
        # When / Then
        with pytest.raises(RuntimeError):
            ledger.complete(StageStatus.IN_PROGRESS)

    def test_append_terminal_while_current_raises(self) -> None:
        # Given: a stage is IN_PROGRESS
        ledger = StageLedger()
        ledger.begin(StageKind.OPENCODE_RUNTIME)
        # When / Then: append_terminal refuses to forge ahead
        with pytest.raises(RuntimeError):
            ledger.append_terminal(StageKind.OPENCODE_VALIDATION, StageStatus.SUCCEEDED)

    def test_append_terminal_duplicate_kind_raises(self) -> None:
        # Given
        ledger = StageLedger()
        ledger.begin(StageKind.OPENCODE_VALIDATION)
        ledger.complete(StageStatus.SUCCEEDED)
        # When / Then
        with pytest.raises(RuntimeError):
            ledger.append_terminal(StageKind.OPENCODE_VALIDATION, StageStatus.FAILED)

    def test_append_terminal_in_progress_status_raises(self) -> None:
        # Given / When / Then
        with pytest.raises(RuntimeError):
            StageLedger().append_terminal(
                StageKind.OMO_VALIDATION, StageStatus.IN_PROGRESS)

    def test_replace_last_different_kind_raises(self) -> None:
        # Given
        ledger = StageLedger()
        ledger.begin(StageKind.OPENCODE_VALIDATION)
        ledger.complete(StageStatus.FAILED)
        # When / Then: cannot forge a different kind over the last record
        with pytest.raises(RuntimeError):
            ledger.replace_last(StageKind.OMO_VALIDATION, StageStatus.SUCCEEDED)

    def test_replace_last_cannot_forge_duplicate_kind(self) -> None:
        # Given: OMO_VALIDATION already reached earlier in the ledger
        ledger = StageLedger()
        ledger.begin(StageKind.OMO_VALIDATION)
        ledger.complete(StageStatus.SKIPPED)
        ledger.begin(StageKind.OPENCODE_VALIDATION)
        ledger.complete(StageStatus.FAILED)
        # When / Then: replacing the last record with an already-seen kind is
        # rejected (it is simultaneously a different kind and a duplicate)
        with pytest.raises(RuntimeError):
            ledger.replace_last(StageKind.OMO_VALIDATION, StageStatus.SUCCEEDED)

    def test_replace_last_same_kind_allowed(self) -> None:
        # Given
        ledger = StageLedger()
        ledger.begin(StageKind.OPENCODE_VALIDATION)
        ledger.complete(StageStatus.FAILED)
        # When
        ledger.replace_last(StageKind.OPENCODE_VALIDATION, StageStatus.SUCCEEDED)
        # Then
        snap = ledger.snapshot()
        assert snap[-1] == (StageKind.OPENCODE_VALIDATION, StageStatus.SUCCEEDED) or (
            snap[-1].kind, snap[-1].status) == (
            StageKind.OPENCODE_VALIDATION, StageStatus.SUCCEEDED)

    def test_snapshot_while_in_progress_raises(self) -> None:
        # Given
        ledger = StageLedger()
        ledger.begin(StageKind.PYTHON_ENVIRONMENT)
        # When / Then
        with pytest.raises(RuntimeError):
            ledger.snapshot()


class TestAnswerCompleteness:
    def test_model_only_answers_rejected_64_zero_port_calls(self, setup_config) -> None:
        # Given: model_id without provider_id
        ports = _ports(setup_config)
        spy = _PortSpy(ports)
        prompt = _RecordingNIPort()
        req = _req_ni(setup_config, ports, prompt=prompt,
                      answers=Answers(model_id="gpt-4"))
        # When
        outcome = run_workflow(req)
        # Then: OPENCODE_CONFIG/64 before any stage port or prompt runs
        assert outcome.status is InitializerStatus.FAILED
        assert outcome.exit_code == 64
        spy.assert_zero_calls()
        assert prompt.ask_calls == [] and prompt.secret_calls == [] and prompt.confirm_calls == []

    def test_provider_only_answers_rejected_64_zero_port_calls(self, setup_config) -> None:
        # Given: provider_id without model_id
        ports = _ports(setup_config)
        spy = _PortSpy(ports)
        prompt = _RecordingNIPort()
        req = _req_ni(setup_config, ports, prompt=prompt,
                      answers=Answers(provider_id="openai"))
        # When
        outcome = run_workflow(req)
        # Then
        assert outcome.exit_code == 64
        spy.assert_zero_calls()
        assert prompt.ask_calls == [] and prompt.secret_calls == []

    def test_custom_base_url_without_model_rejected_64(self, setup_config) -> None:
        # Given: custom base_url + provider but no model
        ports = _ports(setup_config)
        spy = _PortSpy(ports)
        req = _req_ni(setup_config, ports, answers=Answers(
            provider_id="my-proxy", base_url="https://api.example.com/v1"))
        # When
        outcome = run_workflow(req)
        # Then
        assert outcome.exit_code == 64
        spy.assert_zero_calls()

    def test_custom_base_url_without_provider_rejected_64(self, setup_config) -> None:
        # Given: custom base_url + model but no provider
        ports = _ports(setup_config)
        spy = _PortSpy(ports)
        req = _req_ni(setup_config, ports, answers=Answers(
            model_id="m1", base_url="https://api.example.com/v1"))
        # When
        outcome = run_workflow(req)
        # Then
        assert outcome.exit_code == 64
        spy.assert_zero_calls()

    def test_custom_base_url_alone_rejected_64(self, setup_config) -> None:
        # Given: only a custom base_url
        ports = _ports(setup_config)
        spy = _PortSpy(ports)
        req = _req_ni(setup_config, ports, answers=Answers(
            base_url="https://api.example.com/v1"))
        # When
        outcome = run_workflow(req)
        # Then
        assert outcome.exit_code == 64
        spy.assert_zero_calls()


class TestCancellationSemantics:

    def test_environment_cancel_is_failed_61_not_pending(self, tmp_path) -> None:
        # Given: interactive user cancels the environment menu
        ports = _ports(tmp_path)
        req = WorkflowRequest(
            project_root=tmp_path, seam_source_path=tmp_path / "src",
            prompt=_ScriptedPort(asks=["cancel"]), ports=ports, answers=None)
        # When
        outcome = run_workflow(req)
        # Then
        assert outcome.status is InitializerStatus.FAILED
        assert outcome.exit_code == 61
        assert outcome.stages == () or all(
            s.status is not StageStatus.IN_PROGRESS for s in outcome.stages)
        assert [(s.kind, s.status) for s in outcome.stages] == [
            (StageKind.PYTHON_ENVIRONMENT, StageStatus.FAILED)]

    def test_seam_install_decline_is_failed_62_not_pending(self, tmp_path) -> None:
        # Given: interactive user picks a new venv, then declines SEAM install
        ports = _ports(tmp_path)
        req = WorkflowRequest(
            project_root=tmp_path, seam_source_path=tmp_path / "src",
            prompt=_ScriptedPort(asks=["n", ""], confirms=[False]),
            ports=ports, answers=None)
        # When
        outcome = run_workflow(req)
        # Then
        assert outcome.status is InitializerStatus.FAILED
        assert outcome.exit_code == 62
        assert [(s.kind, s.status) for s in outcome.stages] == [
            (StageKind.PYTHON_ENVIRONMENT, StageStatus.SUCCEEDED),
            (StageKind.SEAM_INSTALL, StageStatus.FAILED)]

    def test_opencode_install_refusal_is_failed_63_not_pending(self, setup_config) -> None:
        # Given: the OpenCode installer reports a user refusal
        ports = _ports(setup_config, opencode_installer=_FakeOcInstaller(refuse=True))
        req = _req_ni(setup_config, ports)
        # When
        outcome = run_workflow(req)
        # Then
        assert outcome.status is InitializerStatus.FAILED
        assert outcome.exit_code == 63
        assert [(s.kind, s.status) for s in outcome.stages][-1] == (
            StageKind.OPENCODE_INSTALL, StageStatus.FAILED)

    def test_eof_at_first_prompt_is_failed_61_not_pending(self, tmp_path) -> None:
        # Given: interactive mode with Ctrl-D (EOF) at the environment menu
        ports = _ports(tmp_path)
        req = WorkflowRequest(
            project_root=tmp_path, seam_source_path=tmp_path / "src",
            prompt=_EOFPort(), ports=ports, answers=None)
        # When
        outcome = run_workflow(req)
        # Then: EOF is a typed FAILED with the current stage's kind, never 60
        assert outcome.status is InitializerStatus.FAILED
        assert outcome.exit_code == 61
        assert [(s.kind, s.status) for s in outcome.stages] == [
            (StageKind.PYTHON_ENVIRONMENT, StageStatus.FAILED)]

    def test_interrupt_during_validation_is_failed_68(self, setup_config, monkeypatch) -> None:
        # Given
        monkeypatch.setenv("SEAM_TEST_KEY", "k")

        class _InterruptOnMessage:
            def __init__(self) -> None:
                self.calls: list[tuple[str, ...]] = []
            def run(self, argv, *, env=None):
                self.calls.append(tuple(argv))
                if _mode(argv) == "message":
                    raise KeyboardInterrupt
                return DiagnoseResult(tuple(argv), 0, SafeDetail(""), SafeDetail(""))

        ports = _ports(setup_config, diagnose_runner=_InterruptOnMessage())
        req = _req_ni(setup_config, ports)
        # When
        outcome = run_workflow(req)
        # Then: interrupt during validation maps to OPENCODE_VALIDATION/68
        assert outcome.status is InitializerStatus.FAILED
        assert outcome.exit_code == 68
        assert all(s.status is not StageStatus.IN_PROGRESS for s in outcome.stages)


class TestPendingAuthInvariants:

    def test_pending_auth_has_structural_stages_succeeded(self, setup_config) -> None:
        # Given: no API key -> auth deferred
        ports = _ports(setup_config)
        req = _req_ni(setup_config, ports, answers=Answers(
            provider_id="openai", model_id="gpt-4", billable_consent=False))
        # When
        outcome = run_workflow(req)
        # Then
        assert outcome.status is InitializerStatus.PENDING_AUTH
        pre = [s for s in outcome.stages if s.kind not in (
            StageKind.OPENCODE_VALIDATION, StageKind.OMO_VALIDATION)]
        val = [s for s in outcome.stages if s.kind in (
            StageKind.OPENCODE_VALIDATION, StageKind.OMO_VALIDATION)]
        assert all(s.status is StageStatus.SUCCEEDED for s in pre)
        assert len(pre) == 7
        assert all(s.status in (StageStatus.SKIPPED, StageStatus.SUCCEEDED) for s in val)
        assert any(s.status is StageStatus.SKIPPED for s in val)

    def test_pending_auth_truthful_auth_state_when_key_provided(
            self, setup_config, monkeypatch) -> None:
        # Given: key provided but billable consent declined
        monkeypatch.setenv("SEAM_TEST_KEY", "k")
        ports = _ports(setup_config)
        req = _req_ni(setup_config, ports, answers=Answers(
            provider_id="openai", model_id="gpt-4",
            api_key_env="SEAM_TEST_KEY", billable_consent=False))
        # When
        outcome = run_workflow(req)
        # Then: the outcome must not falsely claim the key was skipped
        assert outcome.status is InitializerStatus.PENDING_AUTH
        assert outcome.auth_state is AuthState.PROVIDED
        assert outcome.billable_consent is BillableCallConsent.DECLINED


class TestTerminalPriority:

    def _keyed_req(self, root: Path, ports: WorkflowPorts) -> WorkflowRequest:
        return _req_ni(root, ports)

    def test_repairable_oc_plus_terminal_omo_maps_69(
            self, setup_config, monkeypatch) -> None:
        # Given: OC auth failure (repairable) + OMO doctor failure (terminal)
        monkeypatch.setenv("SEAM_TEST_KEY", "k")
        ports = _ports(
            setup_config,
            diagnose_runner=_FakeDiagnose(message_script=["auth"]),
            omo_command=_FakeOmoCmd(doctor_ok=False))
        # When
        outcome = run_workflow(self._keyed_req(setup_config, ports))
        # Then: the terminal OMO failure is not hidden by the repairable OC fact
        assert outcome.exit_code == 69
        kinds = {s.kind: s.status for s in outcome.stages}
        assert kinds[StageKind.OPENCODE_VALIDATION] is StageStatus.FAILED
        assert kinds[StageKind.OMO_VALIDATION] is StageStatus.FAILED

    def test_terminal_oc_plus_healthy_omo_maps_68_omo_not_mislabeled(
            self, setup_config, monkeypatch) -> None:
        # Given: OC version failure (terminal) + healthy OMO
        monkeypatch.setenv("SEAM_TEST_KEY", "k")
        ports = _ports(setup_config, version_probe=_FakeVersion(ok=False))
        # When
        outcome = run_workflow(self._keyed_req(setup_config, ports))
        # Then
        assert outcome.exit_code == 68
        kinds = {s.kind: s.status for s in outcome.stages}
        assert kinds[StageKind.OPENCODE_VALIDATION] is StageStatus.FAILED
        assert kinds[StageKind.OMO_VALIDATION] is StageStatus.SUCCEEDED

    def test_terminal_oc_plus_repairable_omo_maps_68(
            self, setup_config, monkeypatch) -> None:
        # Given: OC version failure (terminal) + OMO doctor config invalid (repairable)
        monkeypatch.setenv("SEAM_TEST_KEY", "k")
        ports = _ports(
            setup_config, version_probe=_FakeVersion(ok=False),
            omo_command=_FakeOmoCmd(config_invalid=True))
        # When
        outcome = run_workflow(self._keyed_req(setup_config, ports))
        # Then
        assert outcome.exit_code == 68
        kinds = {s.kind: s.status for s in outcome.stages}
        assert kinds[StageKind.OPENCODE_VALIDATION] is StageStatus.FAILED
        assert kinds[StageKind.OMO_VALIDATION] is StageStatus.FAILED

    def test_both_terminal_is_deterministic_69(
            self, setup_config, monkeypatch) -> None:
        # Given: both validators terminal (OC version failure + OMO doctor failure)
        monkeypatch.setenv("SEAM_TEST_KEY", "k")
        ports = _ports(
            setup_config, version_probe=_FakeVersion(ok=False),
            omo_command=_FakeOmoCmd(doctor_ok=False))
        # When
        first = run_workflow(self._keyed_req(setup_config, ports))
        second = run_workflow(self._keyed_req(setup_config, ports))
        # Then: deterministic terminal-OMO-first mapping on both runs
        assert first.exit_code == 69
        assert second.exit_code == 69


class TestNonInteractiveMultiModel:

    def test_selected_model_written_and_no_ask_secret(
            self, setup_config, monkeypatch) -> None:
        # Given: two runtime models; answers select the non-default one
        monkeypatch.setenv("SEAM_TEST_KEY", "k")
        config = json.loads(_PROJECT_CONFIG)
        config["provider"]["anthropic"] = {"models": {"claude-sonnet-4": {}}}
        _ = (setup_config / ".opencode" / "opencode.jsonc").write_text(
            json.dumps(config), encoding="utf-8")
        ports = _ports(setup_config, opencode_runtime=_FakeRuntime(
            setup_config, models=("openai/gpt-4", "anthropic/claude-sonnet-4")))
        prompt = _RecordingNIPort()
        req = _req_ni(setup_config, ports, prompt=prompt, answers=Answers(
            provider_id="anthropic", model_id="claude-sonnet-4",
            api_key_env="SEAM_TEST_KEY", billable_consent=True))
        facts = WorkflowFacts()
        # When
        outcome = run_workflow(req, facts_out=facts)
        # Then
        assert outcome.status is InitializerStatus.READY
        assert facts.provider_model == "anthropic/claude-sonnet-4"
        omo_text = (setup_config / ".omo" / "omo.jsonc").read_text(encoding="utf-8")
        assert "anthropic/claude-sonnet-4" in omo_text
        assert prompt.ask_calls == [] and prompt.secret_calls == []

    def test_selected_reasoning_written_when_answers_provide_it(
            self, setup_config, monkeypatch) -> None:
        # Given: two runtime models; answers select model + reasoning effort
        monkeypatch.setenv("SEAM_TEST_KEY", "k")
        config = json.loads(_PROJECT_CONFIG)
        config["provider"]["anthropic"] = {"models": {"claude-sonnet-4": {}}}
        _ = (setup_config / ".opencode" / "opencode.jsonc").write_text(
            json.dumps(config), encoding="utf-8")
        ports = _ports(setup_config, opencode_runtime=_FakeRuntime(
            setup_config, models=("openai/gpt-4", "anthropic/claude-sonnet-4")))
        req = _req_ni(setup_config, ports, answers=Answers(
            provider_id="anthropic", model_id="claude-sonnet-4",
            api_key_env="SEAM_TEST_KEY", billable_consent=True, reasoning="high"))
        # When
        outcome = run_workflow(req)
        # Then: the selected reasoning lands in the committed OMO config
        assert outcome.status is InitializerStatus.READY
        omo_text = (setup_config / ".omo" / "omo.jsonc").read_text(encoding="utf-8")
        assert '"high"' in omo_text


class TestOmoMetadataFacts:

    def test_ready_populates_omo_version_runtime_and_live_path(
            self, setup_config, monkeypatch) -> None:
        # Given
        monkeypatch.setenv("SEAM_TEST_KEY", "k")
        ports = _ports(setup_config)
        facts = WorkflowFacts()
        # When
        outcome = run_workflow(_req_ni(setup_config, ports), facts_out=facts)
        # Then
        assert outcome.status is InitializerStatus.READY
        assert facts.omo_version == "1.0.0"
        assert facts.omo_runtime_command == "bunx oh-my-openagent"
        assert facts.omo_live_config_path == "/p"
        assert "omo.jsonc" in facts.omo_config_path
        assert facts.omo_live_config_path != facts.omo_config_path

    def test_legacy_plugin_warning_recorded(self, setup_config, monkeypatch) -> None:
        # Given: the registrar reports a retained legacy plugin entry
        monkeypatch.setenv("SEAM_TEST_KEY", "k")
        warning = "retained legacy plugin entry 'oh-my-opencode' to preserve the existing setup"
        ports = _ports(setup_config, plugin_registrar=_FakeRegistrar(warning=warning))
        facts = WorkflowFacts()
        # When
        _ = run_workflow(_req_ni(setup_config, ports), facts_out=facts)
        # Then
        assert warning in facts.warnings


class TestRepairSelectionRefresh:

    def test_repair_model_change_updates_facts_and_revalidates_new_model(
            self, setup_config: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # Given: interactive run; initial openai/gpt-4 fails auth (repairable);
        # the repair edit switches to anthropic/claude-sonnet-4 which passes.
        config = json.loads(_PROJECT_CONFIG)
        config["provider"]["anthropic"] = {"models": {"claude-sonnet-4": {}}}
        _ = (setup_config / ".opencode" / "opencode.jsonc").write_text(
            json.dumps(config), encoding="utf-8")
        runtime = _FakeRuntime(
            setup_config, models=("openai/gpt-4", "anthropic/claude-sonnet-4"))
        diagnose = _FakeDiagnose(message_script=["auth", "ready"])
        ports = _ports(setup_config, opencode_runtime=runtime, diagnose_runner=diagnose)
        prompt = _ScriptedPort(
            asks=["n", "", "openai", "gpt-4", "edit", "anthropic", "claude-sonnet-4"],
            confirms=[True, True, True, True, True, True, True],
            secrets=["sk-key-1", "sk-key-2"])
        req = WorkflowRequest(
            project_root=setup_config, seam_source_path=setup_config / "src",
            prompt=prompt, ports=ports, answers=None)
        facts = WorkflowFacts()
        # When
        outcome = run_workflow(req, facts_out=facts)
        # Then: repair reached READY through the edited selection
        assert outcome.status is InitializerStatus.READY
        assert facts.provider_model == "anthropic/claude-sonnet-4"
        # And: the committed config carries the new model and facts were refreshed
        oc_bytes = (setup_config / ".opencode" / "opencode.jsonc").read_bytes()
        assert "anthropic/claude-sonnet-4" in oc_bytes.decode("utf-8")
        assert facts.opencode_config_sha256 == sha256(oc_bytes).hexdigest()
        # And: both validation stages appended exactly once, truthfully SUCCEEDED
        kinds = [s.kind for s in outcome.stages]
        assert kinds.count(StageKind.OPENCODE_VALIDATION) == 1
        assert kinds.count(StageKind.OMO_VALIDATION) == 1
        statuses = {s.kind: s.status for s in outcome.stages}
        assert statuses[StageKind.OPENCODE_VALIDATION] is StageStatus.SUCCEEDED
        assert statuses[StageKind.OMO_VALIDATION] is StageStatus.SUCCEEDED


class TestBillableConsentStrictBoolean:

    def test_json_string_false_consent_is_declined_zero_billable_calls(
            self, setup_config: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # Given: an answers FILE whose billable_consent is the JSON string
        # "false" (a truthy non-boolean), with a real API key available
        monkeypatch.setenv("SEAM_TEST_KEY", "k")
        answers_path = setup_config / "answers.json"
        _ = answers_path.write_text(json.dumps({
            "provider_id": "openai", "model_id": "gpt-4",
            "api_key_env": "SEAM_TEST_KEY", "billable_consent": "false",
        }), encoding="utf-8")
        answers = load_answers(answers_path)
        ports = _ports(setup_config)
        req = _req_ni(setup_config, ports, answers=answers)
        # When
        outcome = run_workflow(req)
        # Then: non-boolean consent is declined; no OpenCode message call and
        # no OMO run call can happen; the outcome is PENDING_AUTH, not READY
        assert answers.billable_consent is False
        assert outcome.status is InitializerStatus.PENDING_AUTH
        assert outcome.exit_code == 60
        assert outcome.billable_consent is BillableCallConsent.DECLINED
        diag = ports.diagnose_runner
        assert isinstance(diag, _FakeDiagnose)
        assert [c for c in diag.calls if _mode(c) == "message"] == []
        omo = ports.omo_command
        assert isinstance(omo, _FakeOmoCmd)
        assert len([c for c in omo.calls if "doctor" in c]) >= 1
        assert [c for c in omo.calls if "run" in c] == []


class TestNonInteractiveProviderModelRequired:

    def test_empty_answers_rejected_64_zero_side_effecting_port_calls(
            self, setup_config: Path) -> None:
        # Given: a completely empty non-interactive answers object
        ports = _ports(setup_config)
        spy = _PortSpy(ports)
        prompt = _RecordingNIPort()
        req = _req_ni(setup_config, ports, prompt=prompt, answers=Answers())
        # When
        outcome = run_workflow(req)
        # Then: typed FAILED before any install, config write, or server-start
        # port runs; no prompt is touched; the existing config is untouched
        assert outcome.status is InitializerStatus.FAILED
        assert outcome.exit_code == 64
        spy.assert_zero_calls()
        assert prompt.ask_calls == [] and prompt.secret_calls == []
        assert prompt.confirm_calls == []
        oc = setup_config / ".opencode" / "opencode.jsonc"
        assert oc.read_text(encoding="utf-8") == _PROJECT_CONFIG
        assert not (setup_config / ".omo" / "omo.jsonc").exists()

    def test_environment_only_answers_rejected_64_zero_port_calls(
            self, tmp_path: Path) -> None:
        # Given: environment answers but no provider/model
        ports = _ports(tmp_path)
        spy = _PortSpy(ports)
        req = _req_ni(tmp_path, ports, answers=Answers(
            environment="new", venv_path=str(tmp_path / ".venv")))
        # When
        outcome = run_workflow(req)
        # Then: answer validation fires before the environment stage creates
        # anything (the venv creator port records zero calls)
        assert outcome.status is InitializerStatus.FAILED
        assert outcome.exit_code == 64
        spy.assert_zero_calls()


class TestNonInteractiveEnvironmentValue:

    def test_bogus_environment_rejected_61_zero_port_calls(self, setup_config) -> None:
        # Given: an explicit unsupported environment value
        ports = _ports(setup_config)
        spy = _PortSpy(ports)
        probe = ports.interpreter_probe
        assert isinstance(probe, _FakeProbe)
        lifecycle = ports.server_lifecycle
        assert isinstance(lifecycle, _FakeLifecycle)
        req = _req_ni(setup_config, ports, answers=Answers(
            provider_id="openai", model_id="gpt-4", environment="bogus"))
        # When
        outcome = run_workflow(req)
        # Then: typed FAILED/PYTHON_ENVIRONMENT before the environment stage
        # begins - no venv creation, no probe, no install/config/runtime call
        assert outcome.status is InitializerStatus.FAILED
        assert outcome.exit_code == 61
        assert outcome.failure_kind is FailureKind.PYTHON_ENVIRONMENT
        assert outcome.stages == ()
        spy.assert_zero_calls()
        assert probe.calls == []
        assert lifecycle.started == [] and lifecycle.stopped == []

    def test_bogus_environment_with_missing_provider_yields_64_first(
            self, setup_config) -> None:
        # Given: an unsupported environment AND missing provider/model
        ports = _ports(setup_config)
        spy = _PortSpy(ports)
        req = _req_ni(setup_config, ports, answers=Answers(environment="bogus"))
        # When
        outcome = run_workflow(req)
        # Then: the existing provider/model up-front check remains first
        assert outcome.status is InitializerStatus.FAILED
        assert outcome.exit_code == 64
        spy.assert_zero_calls()

    def test_omitted_and_blank_environment_keep_default_new(
            self, setup_config, monkeypatch) -> None:
        # Given: three runs - environment omitted, empty, and whitespace-only
        monkeypatch.setenv("SEAM_TEST_KEY", "k")
        for value in (None, "", "   "):
            ports = _ports(setup_config)
            creator = ports.venv_creator
            assert isinstance(creator, _FakeVenv)
            req = _req_ni(setup_config, ports, answers=Answers(
                provider_id="openai", model_id="gpt-4",
                api_key_env="SEAM_TEST_KEY", billable_consent=True,
                environment=value))
            # When
            outcome = run_workflow(req)
            # Then: the established default-new behavior is preserved
            assert outcome.status is InitializerStatus.READY
            assert len(creator.calls) == 1
            assert creator.calls[0][1] == setup_config / ".venv"

    def test_new_aliases_create_default_venv(self, setup_config, monkeypatch) -> None:
        # Given: the recognized new-venv aliases
        monkeypatch.setenv("SEAM_TEST_KEY", "k")
        for value in ("n", "new", "NEW"):
            ports = _ports(setup_config)
            creator = ports.venv_creator
            assert isinstance(creator, _FakeVenv)
            req = _req_ni(setup_config, ports, answers=Answers(
                provider_id="openai", model_id="gpt-4",
                api_key_env="SEAM_TEST_KEY", billable_consent=True,
                environment=value))
            # When
            outcome = run_workflow(req)
            # Then
            assert outcome.status is InitializerStatus.READY
            assert len(creator.calls) == 1
            assert creator.calls[0][1] == setup_config / ".venv"

    def test_existing_alias_uses_venv_path_without_creation(
            self, setup_config, monkeypatch) -> None:
        # Given: the existing-venv alias with a venv_path
        monkeypatch.setenv("SEAM_TEST_KEY", "k")
        ports = _ports(setup_config)
        creator = ports.venv_creator
        assert isinstance(creator, _FakeVenv)
        probe = ports.interpreter_probe
        assert isinstance(probe, _FakeProbe)
        facts = WorkflowFacts()
        req = _req_ni(setup_config, ports, answers=Answers(
            provider_id="openai", model_id="gpt-4",
            api_key_env="SEAM_TEST_KEY", billable_consent=True,
            environment="e", venv_path="/fake/venv"))
        # When
        outcome = run_workflow(req, facts_out=facts)
        # Then: the existing venv is probed and reused; nothing is created
        assert outcome.status is InitializerStatus.READY
        assert facts.environment is not None
        assert facts.environment.kind is EnvironmentKind.EXISTING_VENV
        assert probe.calls == ["/fake/venv"]
        assert creator.calls == []

    def test_base_alias_selects_base_without_creation(
            self, setup_config, monkeypatch) -> None:
        # Given: the base alias with a safe base interpreter
        monkeypatch.setenv("SEAM_TEST_KEY", "k")
        monkeypatch.setattr("seam_init.workflow_env.is_safe_base",
                            lambda info: SafetyReport(safe=True, reasons=()))
        ports = _ports(setup_config)
        creator = ports.venv_creator
        assert isinstance(creator, _FakeVenv)
        facts = WorkflowFacts()
        req = _req_ni(setup_config, ports, answers=Answers(
            provider_id="openai", model_id="gpt-4",
            api_key_env="SEAM_TEST_KEY", billable_consent=True,
            environment="base"))
        # When
        outcome = run_workflow(req, facts_out=facts)
        # Then: the base interpreter is selected; no venv is created
        assert outcome.status is InitializerStatus.READY
        assert facts.environment is not None
        assert facts.environment.kind is EnvironmentKind.BASE
        assert creator.calls == []


def _raise_for(target: Path):
    real_read_bytes = Path.read_bytes

    def _patched(self: Path) -> bytes:
        if self == target:
            raise PermissionError("simulated read denial")
        return real_read_bytes(self)

    return _patched


def _arm_refresh_read_failure(
        monkeypatch: pytest.MonkeyPatch, target: Path, *,
        content_marker: bytes | None = None) -> None:
    real_read_bytes = Path.read_bytes

    def _patched(self: Path) -> bytes:
        if self == target:
            frame = inspect.currentframe()
            caller = ""
            if frame is not None and frame.f_back is not None:
                caller = frame.f_back.f_code.co_name
            del frame
            if caller in ("refresh_config_facts", "_config_sha256"):
                data = real_read_bytes(self)
                if content_marker is None or content_marker in data:
                    raise PermissionError("simulated read denial at fact refresh")
        return real_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", _patched)


class TestConfigReadFailureMapping:

    def test_refresh_oc_read_error_raises_typed_not_raw(
            self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # Given: an existing OpenCode config that passes the existence check
        # but whose read_bytes raises OSError
        cfg = tmp_path / ".opencode"
        cfg.mkdir()
        _ = (cfg / "opencode.jsonc").write_text("{}", encoding="utf-8")
        monkeypatch.setattr(Path, "read_bytes",
                            _raise_for(cfg / "opencode.jsonc"))
        # When / Then: the shared seam raises the typed failure, never raw
        with pytest.raises(InitializerFailure) as excinfo:
            refresh_config_facts(WorkflowFacts(), tmp_path)
        assert excinfo.value.kind is FailureKind.OPENCODE_CONFIG

    def test_refresh_omo_read_error_raises_typed_not_raw(
            self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # Given: an existing OMO config whose read_bytes raises OSError
        cfg = tmp_path / ".omo"
        cfg.mkdir()
        _ = (cfg / "omo.jsonc").write_text("{}", encoding="utf-8")
        monkeypatch.setattr(Path, "read_bytes", _raise_for(cfg / "omo.jsonc"))
        # When / Then
        with pytest.raises(InitializerFailure) as excinfo:
            refresh_config_facts(WorkflowFacts(), tmp_path)
        assert excinfo.value.kind is FailureKind.OMO_CONFIG

    def test_oc_read_error_at_refresh_returns_typed_failed_64(
            self, setup_config: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # Given: a happy-path request, but the OpenCode config read fails when
        # the post-commit fact refresh reads it
        monkeypatch.setenv("SEAM_TEST_KEY", "k")
        _arm_refresh_read_failure(
            monkeypatch, setup_config / ".opencode" / "opencode.jsonc")
        ports = _ports(setup_config)
        req = _req_ni(setup_config, ports)
        # When: the workflow must return a typed outcome, not raise
        outcome = run_workflow(req)
        # Then
        assert outcome.status is InitializerStatus.FAILED
        assert outcome.exit_code == 64
        kinds = {s.kind: s.status for s in outcome.stages}
        assert kinds[StageKind.OPENCODE_CONFIG] is StageStatus.FAILED
        assert _no_in_progress(outcome)

    def test_omo_read_error_at_refresh_returns_typed_failed_66(
            self, setup_config: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # Given: a happy-path request, but the OMO config read fails when the
        # post-commit fact refresh reads it
        monkeypatch.setenv("SEAM_TEST_KEY", "k")
        _arm_refresh_read_failure(
            monkeypatch, setup_config / ".omo" / "omo.jsonc")
        ports = _ports(setup_config)
        req = _req_ni(setup_config, ports)
        # When
        outcome = run_workflow(req)
        # Then
        assert outcome.status is InitializerStatus.FAILED
        assert outcome.exit_code == 66
        kinds = {s.kind: s.status for s in outcome.stages}
        assert kinds[StageKind.OMO_CONFIG] is StageStatus.FAILED
        assert _no_in_progress(outcome)

    def test_repair_refresh_read_error_returns_typed_failed(
            self, setup_config: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # Given: an interactive repair run; the post-edit fact refresh cannot
        # read the newly committed OpenCode config (earlier reads, including
        # the two pre-repair refreshes, still see the openai/gpt-4 model)
        config = json.loads(_PROJECT_CONFIG)
        config["provider"]["anthropic"] = {"models": {"claude-sonnet-4": {}}}
        _ = (setup_config / ".opencode" / "opencode.jsonc").write_text(
            json.dumps(config), encoding="utf-8")
        runtime = _FakeRuntime(
            setup_config, models=("openai/gpt-4", "anthropic/claude-sonnet-4"))
        diagnose = _FakeDiagnose(message_script=["auth", "ready"])
        ports = _ports(setup_config, opencode_runtime=runtime, diagnose_runner=diagnose)
        prompt = _ScriptedPort(
            asks=["n", "", "openai", "gpt-4", "edit", "anthropic", "claude-sonnet-4"],
            confirms=[True, True, True, True, True, True, True],
            secrets=["sk-key-1", "sk-key-2"])
        req = WorkflowRequest(
            project_root=setup_config, seam_source_path=setup_config / "src",
            prompt=prompt, ports=ports, answers=None)
        _arm_refresh_read_failure(
            monkeypatch, setup_config / ".opencode" / "opencode.jsonc",
            content_marker=b'"model": "anthropic')
        # When: the repair-path refresh fails after the committed edit
        outcome = run_workflow(req)
        # Then: the repair caller behaves like the initial path - typed
        # FAILED at the owning config stage, never a raw OSError escape
        assert outcome.status is InitializerStatus.FAILED
        assert outcome.exit_code == 64
        assert _no_in_progress(outcome)
