"""Given/When/Then tests for version-aware OMO model mapping configuration.

Covers the R5 production architecture (replacing R4's flawed capability design):
- Fresh projects with NO target and NO legacy create authorized .omo/omo.jsonc
- Schema capability backed by the REAL vendored omo.schema.json document
- Version gating via semver (major==5); fake/unsupported versions return None
- Candidate validation uses schema authority + strict SEAM model-membership rules
- Legacy/snapshot/sidecar/_migrations/bracket-sections preserved
- Commit-time validation rollback with REAL injected failure (not unused fake)
- Production adapter offline, absent binary, no-network
- Zero-canary hygiene
"""
from __future__ import annotations

import hashlib
import json
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import final

import pytest

from seam_init.config_transaction import (
    ConfigTransaction,
    TransactionId,
    TransactionResult,
)
from seam_init.omo_adapters import OmoCommand, SubprocessOmoCapabilityPort
from seam_init.omo_config import (
    CURRENT_SCHEMA_URL,
    CURRENT_TARGET_REL,
    DefaultHome,
    DryRunResult,
    DryRunStatus,
    GLOBAL_LEGACY_REL,
    HomePathPolicy,
    LEGACY_SIDECAR_REL,
    SNAPSHOT_SUFFIXES,
    JsonDict,
    OmoCapabilityPort,
    OmoConfigRequest,
    OmoConfigResult,
    SchemaCapability,
    configure_omo,
    discover_legacy_sources_all,
)
from seam_init.omo_schema import (
    SchemaAssetError,
    extract_reasoning_values,
    extract_schema_url,
    load_schema_document,
    validate_against_schema,
)
from seam_init.omo_sources import seam_validate, validate_model_catalog
from seam_init.omo_version import is_supported_version, parse_version

_CANARY = "sk-test-canary-0123456789abcdef"
_REAL_SCHEMA = load_schema_document()
_REAL_URL = extract_schema_url(_REAL_SCHEMA)
_REAL_REASONING = extract_reasoning_values(_REAL_SCHEMA)
_DEFAULT_MODELS = ("zhipuai/glm-5.2",)
_MULTI_MODELS = ("openai/gpt-5.6-sol", "zhipuai/glm-5.2", "kimi-for-coding/k3")
_SENTINEL = object()


def _real_cap(version: str = "5.0.0-beta.5") -> SchemaCapability:
    return SchemaCapability(
        schema_url=_REAL_URL, reasoning_values=_REAL_REASONING,
        version=version, schema_document=_REAL_SCHEMA,
    )


@final
class _FakeHome:
    def __init__(self, home: Path) -> None:
        self._home = home

    def home(self) -> Path:
        return self._home


@final
class _FakeCapabilityPort:
    def __init__(
        self, *,
        capability: SchemaCapability | None | object = _SENTINEL,
        dry_run: DryRunResult | None = None,
    ) -> None:
        self._capability = _real_cap() if capability is _SENTINEL else capability
        self._dry_run = dry_run or DryRunResult(DryRunStatus.UNSUPPORTED, None)
        self.migrate_calls = 0

    def resolve_capability(self) -> SchemaCapability | None:
        cap = self._capability
        return cap if isinstance(cap, SchemaCapability) else None

    def migrate_dry_run(self) -> DryRunResult:
        self.migrate_calls += 1
        return self._dry_run


@final
class _FakeRuntime:
    def __init__(self, *, models: tuple[str, ...] | None = _DEFAULT_MODELS) -> None:
        self._models = models

    def debug_config(self) -> JsonDict | None:
        return None

    def debug_models(self, config_bytes: bytes | None = None) -> tuple[str, ...] | None:
        return self._models


@final
class _FakePrompt:
    def __init__(
        self, *,
        confirms: list[bool] | None = None,
        asks: list[str] | None = None,
    ) -> None:
        self._confirms = list(confirms or [])
        self._asks = list(asks or [])

    def ask(self, prompt: str, *, default: str | None = None) -> str:
        if not self._asks:
            raise AssertionError(f"unexpected ask: {prompt!r}")
        return self._asks.pop(0)

    def secret(self, prompt: str) -> str:
        raise AssertionError(f"unexpected secret: {prompt!r}")

    def confirm(self, prompt: str, *, default: bool = False) -> bool:
        if not self._confirms:
            raise AssertionError(f"unexpected confirm: {prompt!r}")
        return self._confirms.pop(0)


_LEGACY_CONFIG: dict[str, object] = {
    "$schema": "https://raw.githubusercontent.com/code-yeongyu/oh-my-opencode/dev/assets/oh-my-opencode.schema.json",
    "agents": {
        "sisyphus": {"model": "zhipuai/glm-5.2", "variant": "max"},
        "hephaestus": {"model": "zhipuai/glm-5.2", "variant": "max"},
    },
    "categories": {"deep": {"model": "zhipuai/glm-5.2", "variant": "max"}},
}
_SIDECAR = {"appliedMigrations": ["model-version:openai/gpt-5.4->openai/gpt-5.5"]}


def _write_legacy(root: Path, config: Mapping[str, object] | None = None) -> Path:
    path = root / ".opencode" / "oh-my-openagent.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config or _LEGACY_CONFIG, indent=2) + "\n", encoding="utf-8")
    return path


def _write_sidecar(root: Path) -> Path:
    path = root / LEGACY_SIDECAR_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_SIDECAR, indent=2) + "\n", encoding="utf-8")
    return path


def _write_snapshots(root: Path) -> dict[str, bytes]:
    orig: dict[str, bytes] = {}
    for s in SNAPSHOT_SUFFIXES:
        p = root / ".opencode" / f"oh-my-openagent{s}.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        b = json.dumps({"agents": {"x": {"model": "a/b"}}}).encode()
        p.write_bytes(b)
        orig[s] = b
    return orig


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _request(
    root: Path, *,
    capability_port: _FakeCapabilityPort | None = None,
    runtime: _FakeRuntime | None = None,
    prompt: _FakePrompt | None = None,
    home: HomePathPolicy | None = None,
) -> OmoConfigRequest:
    return OmoConfigRequest(
        project_root=root,
        prompt=prompt or _FakePrompt(confirms=[True]),
        capability_port=capability_port or _FakeCapabilityPort(),
        runtime=runtime if runtime is not None else _FakeRuntime(),
        home=home or _FakeHome(root / "fake-home"),
    )


def _read_target(root: Path) -> JsonDict:
    data = json.loads((root / CURRENT_TARGET_REL).read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _section(config: JsonDict, name: str) -> JsonDict:
    v = config.get(name)
    assert isinstance(v, dict)
    return v


def _kinds(result: OmoConfigResult) -> set[str]:
    return {f.kind for f in result.facts}


class TestFreshProjectConfigures:
    def test_fresh_no_legacy_no_target_creates_config(self, tmp_path: Path) -> None:
        result = configure_omo(_request(tmp_path))
        assert result.committed is True
        assert result.fresh is True
        assert (tmp_path / CURRENT_TARGET_REL).is_file()

    def test_fresh_creates_nonempty_canonical_mappings(self, tmp_path: Path) -> None:
        configure_omo(_request(tmp_path))
        target = _read_target(tmp_path)
        agents = _section(target, "agents")
        categories = _section(target, "categories")
        assert len(agents) == 11
        assert len(categories) == 8
        for entry in list(agents.values()) + list(categories.values()):
            assert isinstance(entry, dict)
            model = entry.get("model")
            assert isinstance(model, str) and "/" in model
            assert isinstance(entry.get("reasoning"), str)

    def test_fresh_declined_aborts(self, tmp_path: Path) -> None:
        result = configure_omo(_request(
            tmp_path, prompt=_FakePrompt(confirms=[False])))
        assert result.committed is False
        assert "FRESH_DECLINED" in _kinds(result)
        assert not (tmp_path / CURRENT_TARGET_REL).exists()

    def test_fresh_single_model_no_prompt(self, tmp_path: Path) -> None:
        prompt = _FakePrompt(confirms=[True])
        configure_omo(_request(tmp_path, prompt=prompt))
        assert prompt._asks == []

    def test_fresh_multi_model_prompts(self, tmp_path: Path) -> None:
        prompt = _FakePrompt(
            confirms=[True],
            asks=["zhipuai", "zhipuai/glm-5.2", "max"],
        )
        result = configure_omo(_request(
            tmp_path, prompt=prompt, runtime=_FakeRuntime(models=_MULTI_MODELS)))
        assert result.committed is True


class TestVersionGating:
    def test_parse_extracts_semver(self) -> None:
        assert parse_version("5.0.0-beta.5") == (5, 0, 0)
        assert parse_version("4.19.0") == (4, 19, 0)

    def test_supported_major_5(self) -> None:
        assert is_supported_version("5.0.0-beta.5") is True
        assert is_supported_version("5.1.0") is True

    def test_unsupported_versions_rejected(self) -> None:
        assert is_supported_version("3.5.0") is False
        assert is_supported_version("4.19.0") is False
        assert is_supported_version("6.0.0") is False

    def test_malformed_version_rejected(self) -> None:
        assert is_supported_version("not-a-version") is False
        assert is_supported_version("") is False

    def test_adapter_accepts_supported_version(self, tmp_path: Path) -> None:
        port = SubprocessOmoCapabilityPort(_doctor_cmd(tmp_path, "5.0.0-beta.5"))
        cap = port.resolve_capability()
        assert cap is not None
        assert cap.version == "5.0.0-beta.5"
        assert cap.schema_url == CURRENT_SCHEMA_URL

    def test_adapter_rejects_fake_3_5_0(self, tmp_path: Path) -> None:
        port = SubprocessOmoCapabilityPort(_doctor_cmd(tmp_path, "3.5.0"))
        assert port.resolve_capability() is None

    def test_adapter_rejects_unsupported_4_x(self, tmp_path: Path) -> None:
        port = SubprocessOmoCapabilityPort(_doctor_cmd(tmp_path, "4.19.0"))
        assert port.resolve_capability() is None

    def test_adapter_rejects_future_6_x(self, tmp_path: Path) -> None:
        port = SubprocessOmoCapabilityPort(_doctor_cmd(tmp_path, "6.0.0"))
        assert port.resolve_capability() is None

    def test_adapter_rejects_malformed_version(self, tmp_path: Path) -> None:
        port = SubprocessOmoCapabilityPort(_doctor_cmd(tmp_path, "garbage"))
        assert port.resolve_capability() is None

    def test_adapter_rejects_missing_plugin_version(self, tmp_path: Path) -> None:
        script = tmp_path / "fake_omo.py"
        script.write_text(
            "import json\nprint(json.dumps({'systemInfo': {}}))\n",
            encoding="utf-8",
        )
        port = SubprocessOmoCapabilityPort(OmoCommand(
            argv=(sys.executable, str(script)), cwd=tmp_path, timeout_seconds=5))
        assert port.resolve_capability() is None

    def test_adapter_rejects_doctor_configpath_configvalid(self, tmp_path: Path) -> None:
        script = tmp_path / "fake_omo.py"
        script.write_text(
            "import json\nprint(json.dumps({'systemInfo': {\n"
            "  'pluginVersion': '5.0.0',\n"
            "  'configPath': '/wrong/path',\n"
            "  'configValid': False,\n"
            "}}))\n",
            encoding="utf-8",
        )
        port = SubprocessOmoCapabilityPort(OmoCommand(
            argv=(sys.executable, str(script)), cwd=tmp_path, timeout_seconds=5))
        cap = port.resolve_capability()
        assert cap is not None
        assert cap.version == "5.0.0"


class TestSchemaAuthority:
    def test_schema_url_is_canonical(self) -> None:
        assert _REAL_URL == CURRENT_SCHEMA_URL

    def test_reasoning_extracted_from_schema(self) -> None:
        assert "off" in _REAL_REASONING
        assert "max" in _REAL_REASONING
        assert "auto" in _REAL_REASONING
        assert len(_REAL_REASONING) == 8

    def test_validate_rejects_numeric_model(self) -> None:
        bad = _candidate({"agents": {"x": {"model": 12345}}})
        issues = seam_validate(bad, _REAL_SCHEMA, _REAL_REASONING, _DEFAULT_MODELS)
        assert any("expected string" in i for i in issues)

    def test_validate_rejects_object_model(self) -> None:
        bad = _candidate({"agents": {"x": {"model": {"nested": True}}}})
        issues = seam_validate(bad, _REAL_SCHEMA, _REAL_REASONING, _DEFAULT_MODELS)
        assert any("expected string" in i for i in issues)

    def test_validate_rejects_missing_profiles(self) -> None:
        bad = json.dumps({
            "$schema": CURRENT_SCHEMA_URL,
            "agents": {"x": {"model": "zhipuai/glm-5.2"}},
        }).encode()
        issues = seam_validate(bad, _REAL_SCHEMA, _REAL_REASONING, _DEFAULT_MODELS)
        assert any("profiles" in i for i in issues)

    def test_validate_rejects_unknown_top_level_field(self) -> None:
        bad = _candidate({"custom_field": 42})
        issues = seam_validate(bad, _REAL_SCHEMA, _REAL_REASONING, _DEFAULT_MODELS)
        assert any("additional property" in i for i in issues)

    def test_validate_rejects_bad_reasoning_seam_rule(self) -> None:
        bad = _candidate({
            "agents": {"x": {"model": "zhipuai/glm-5.2", "reasoning": "ultra"}},
        })
        issues = seam_validate(bad, _REAL_SCHEMA, _REAL_REASONING, _DEFAULT_MODELS)
        assert any("not canonical" in i for i in issues)

    def test_validate_rejects_slashless_model_seam_rule(self) -> None:
        bad = _candidate({"agents": {"x": {"model": "noslash"}}})
        issues = seam_validate(bad, _REAL_SCHEMA, _REAL_REASONING, _DEFAULT_MODELS)
        assert any("slash" in i for i in issues)

    def test_validate_rejects_unknown_model_seam_rule(self) -> None:
        bad = _candidate({"agents": {"x": {"model": "unknown/fake"}}})
        issues = seam_validate(bad, _REAL_SCHEMA, _REAL_REASONING, _DEFAULT_MODELS)
        assert any("not in runtime list" in i for i in issues)

    def test_validate_accepts_canonical_fresh(self) -> None:
        from seam_init.omo_profile import build_fresh_config
        good = build_fresh_config(CURRENT_SCHEMA_URL, "zhipuai/glm-5.2", "max")
        good_bytes = json.dumps(good, indent=2).encode() + b"\n"
        issues = seam_validate(good_bytes, _REAL_SCHEMA, _REAL_REASONING, _DEFAULT_MODELS)
        assert issues == []

    def test_validate_rejects_malformed_entry(self) -> None:
        bad = _candidate({"agents": {"x": {"model": 12345}}})
        issues = seam_validate(bad, _REAL_SCHEMA, _REAL_REASONING, _DEFAULT_MODELS)
        assert issues

    def test_orchestrator_generates_canonical_from_empty_mappings(self, tmp_path: Path) -> None:
        _write_legacy(tmp_path, {"$schema": "old"})
        result = configure_omo(_request(tmp_path))
        assert result.committed is True
        assert "CANONICAL_GENERATED" in _kinds(result)
        agents = _section(_read_target(tmp_path), "agents")
        assert len(agents) == 11


class TestCapabilityBoundary:
    def test_capability_unavailable_fails_closed(self, tmp_path: Path) -> None:
        _write_legacy(tmp_path)
        result = configure_omo(_request(
            tmp_path, capability_port=_FakeCapabilityPort(capability=None)))
        assert result.committed is False
        assert "CAPABILITY_UNAVAILABLE" in _kinds(result)

    def test_model_data_unavailable_fails_closed(self, tmp_path: Path) -> None:
        _write_legacy(tmp_path)
        result = configure_omo(_request(tmp_path, runtime=_FakeRuntime(models=None)))
        assert result.committed is False
        assert "MODEL_DATA_UNAVAILABLE" in _kinds(result)


class TestMigration:
    def test_legacy_migrates_and_commits(self, tmp_path: Path) -> None:
        _write_legacy(tmp_path)
        result = configure_omo(_request(tmp_path))
        assert result.committed is True
        assert result.migrated is True
        assert (tmp_path / CURRENT_TARGET_REL).is_file()

    def test_dry_run_failure_aborts(self, tmp_path: Path) -> None:
        _write_legacy(tmp_path)
        result = configure_omo(_request(
            tmp_path,
            capability_port=_FakeCapabilityPort(
                dry_run=DryRunResult(DryRunStatus.FAILURE, None))))
        assert result.committed is False
        assert "DRY_RUN_FAILED" in _kinds(result)

    def test_dry_run_unsupported_proceeds(self, tmp_path: Path) -> None:
        _write_legacy(tmp_path)
        result = configure_omo(_request(tmp_path))
        assert result.committed is True

    def test_migration_declined_preserves_files(self, tmp_path: Path) -> None:
        legacy = _write_legacy(tmp_path)
        before = legacy.read_bytes()
        result = configure_omo(_request(
            tmp_path, prompt=_FakePrompt(confirms=[False])))
        assert result.committed is False
        assert legacy.read_bytes() == before
        assert not (tmp_path / CURRENT_TARGET_REL).exists()


class TestModelValidation:
    def test_unknown_model_rejected(self, tmp_path: Path) -> None:
        _write_legacy(tmp_path, {
            "$schema": "old",
            "agents": {"x": {"model": "unknown/fake"}},
            "categories": {},
        })
        result = configure_omo(_request(tmp_path))
        assert result.committed is False

    def test_slashless_model_filled_by_single_runtime(self, tmp_path: Path) -> None:
        _write_legacy(tmp_path, {
            "$schema": "old",
            "agents": {"x": {"model": "noslash", "variant": "max"}},
            "categories": {},
        })
        result = configure_omo(_request(tmp_path))
        assert result.committed is True
        agents = _section(_read_target(tmp_path), "agents")
        model = _section(agents, "x")["model"]
        assert isinstance(model, str) and "/" in model


class TestPreservation:
    def test_snapshots_preserved(self, tmp_path: Path) -> None:
        _write_legacy(tmp_path)
        orig = _write_snapshots(tmp_path)
        configure_omo(_request(tmp_path))
        for s in SNAPSHOT_SUFFIXES:
            p = tmp_path / ".opencode" / f"oh-my-openagent{s}.json"
            assert p.read_bytes() == orig[s]

    def test_sidecar_preserved(self, tmp_path: Path) -> None:
        _write_legacy(tmp_path)
        sidecar = _write_sidecar(tmp_path)
        before = sidecar.read_bytes()
        configure_omo(_request(tmp_path))
        assert sidecar.read_bytes() == before

    def test_global_legacy_preserved(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        gp = home / GLOBAL_LEGACY_REL
        gp.parent.mkdir(parents=True, exist_ok=True)
        gb = json.dumps(_LEGACY_CONFIG).encode()
        gp.write_bytes(gb)
        _write_legacy(tmp_path)
        configure_omo(_request(tmp_path, home=_FakeHome(home)))
        assert gp.read_bytes() == gb

    def test_migrations_field_preserved(self, tmp_path: Path) -> None:
        _write_legacy(tmp_path, {**_LEGACY_CONFIG, "_migrations": ["m1"]})
        configure_omo(_request(tmp_path))
        assert _read_target(tmp_path)["_migrations"] == ["m1"]

    def test_supported_unknown_fields_preserved(self, tmp_path: Path) -> None:
        _write_legacy(tmp_path, {**_LEGACY_CONFIG, "telemetry": {"enabled": False}})
        configure_omo(_request(tmp_path))
        assert _read_target(tmp_path)["telemetry"] == {"enabled": False}

    def test_bracket_sections_preserved(self, tmp_path: Path) -> None:
        cfg = {
            **_LEGACY_CONFIG,
            "[senpi]": {"telemetry": {"enabled": False}},
            "[codex]": {"telemetry": {"enabled": False}},
        }
        _write_legacy(tmp_path, cfg)
        configure_omo(_request(tmp_path))
        target = _read_target(tmp_path)
        assert "[senpi]" in target
        assert "[codex]" in target


class TestConsent:
    def test_normalize_consent_required(self, tmp_path: Path) -> None:
        target = tmp_path / CURRENT_TARGET_REL
        target.parent.mkdir(parents=True, exist_ok=True)
        original = b'{"$schema":"old","profiles":{},"agents":{"x":{"model":"zhipuai/glm-5.2","variant":"max"}}}\n'
        target.write_bytes(original)
        result = configure_omo(_request(
            tmp_path, prompt=_FakePrompt(confirms=[False])))
        assert result.committed is False
        assert "NORMALIZE_DECLINED" in _kinds(result)
        assert target.read_bytes() == original

    def test_legitimate_empty_survives(self, tmp_path: Path) -> None:
        target = tmp_path / CURRENT_TARGET_REL
        target.parent.mkdir(parents=True, exist_ok=True)
        original = b"{}\n"
        target.write_bytes(original)
        result = configure_omo(_request(
            tmp_path, prompt=_FakePrompt(confirms=[False])))
        assert result.committed is False
        assert target.read_bytes() == original


class TestExistingFailsClosed:
    def test_malformed_existing_fails_closed_preserving_bytes(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Given an existing malformed current target
        target = tmp_path / CURRENT_TARGET_REL
        target.parent.mkdir(parents=True, exist_ok=True)
        original = b"{broken\n"
        target.write_bytes(original)
        prompt = _FakePrompt(confirms=[True])
        capability = _FakeCapabilityPort()
        tx_calls = _spy_transactions(monkeypatch)
        # When configuration runs
        result = configure_omo(_request(
            tmp_path, prompt=prompt, capability_port=capability))
        # Then it fails closed before summary, consent, migration, or any write
        assert result.committed is False
        assert result.transaction is None
        assert "EXISTING_MALFORMED" in _kinds(result)
        assert prompt._confirms == [True]  # consent unconsumed: zero prompt calls
        assert capability.migrate_calls == 0
        assert "begin" not in tx_calls and "commit" not in tx_calls
        assert "Current OMO config summary" not in capsys.readouterr().out
        assert target.read_bytes() == original

    def test_undecodable_existing_fails_closed(self, tmp_path: Path) -> None:
        # Given an existing target whose bytes are not UTF-8 decodable
        target = tmp_path / CURRENT_TARGET_REL
        target.parent.mkdir(parents=True, exist_ok=True)
        original = b"\xff\xfe{broken"
        target.write_bytes(original)
        prompt = _FakePrompt(confirms=[True])
        # When configuration runs
        result = configure_omo(_request(tmp_path, prompt=prompt))
        # Then it fails closed as malformed, preserving the exact bytes
        assert result.committed is False
        assert "EXISTING_MALFORMED" in _kinds(result)
        assert prompt._confirms == [True]
        assert target.read_bytes() == original

    def test_read_error_existing_fails_closed_preserving_bytes(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Given an existing target that cannot be read (simulated, not chmod)
        target = tmp_path / CURRENT_TARGET_REL
        target.parent.mkdir(parents=True, exist_ok=True)
        original = b'{"profiles":{}}\n'
        target.write_bytes(original)
        _arm_target_read_failure(monkeypatch, target)
        prompt = _FakePrompt(confirms=[True])
        capability = _FakeCapabilityPort()
        tx_calls = _spy_transactions(monkeypatch)
        # When configuration runs
        result = configure_omo(_request(
            tmp_path, prompt=prompt, capability_port=capability))
        # Then it fails closed before summary, consent, migration, or any write
        assert result.committed is False
        assert result.transaction is None
        assert "EXISTING_UNREADABLE" in _kinds(result)
        assert prompt._confirms == [True]  # consent unconsumed: zero prompt calls
        assert capability.migrate_calls == 0
        assert "begin" not in tx_calls and "commit" not in tx_calls
        assert "Current OMO config summary" not in capsys.readouterr().out
        assert target.read_bytes() == original

    def test_failure_detail_excludes_config_contents(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        # Given a malformed target embedding a secret-looking canary
        target = tmp_path / CURRENT_TARGET_REL
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b'{"apiKey": "' + _CANARY.encode() + b'", broken')
        # When configuration runs
        result = configure_omo(_request(
            tmp_path, prompt=_FakePrompt(confirms=[True])))
        # Then the canary never reaches stdout, facts, or the safe detail
        out = capsys.readouterr().out
        report = "".join(str(f.detail) for f in result.facts) + str(result.safe_detail)
        assert result.committed is False
        assert _CANARY not in out
        assert _CANARY not in report

    def test_absent_target_remains_legitimate_fresh_flow(self, tmp_path: Path) -> None:
        # Given NO existing target (the legitimate fresh path)
        # When configuration runs with consent
        result = configure_omo(_request(
            tmp_path, prompt=_FakePrompt(confirms=[True])))
        # Then the fresh flow still commits (regression guard for over-blocking)
        assert result.committed is True
        assert result.fresh is True
        assert (tmp_path / CURRENT_TARGET_REL).is_file()


class TestCommitRollback:
    def test_no_target_after_validation_failure(self, tmp_path: Path) -> None:
        _write_legacy(tmp_path, {
            "$schema": "old",
            "agents": {"x": {"model": "unknown/fake"}},
            "categories": {},
        })
        configure_omo(_request(tmp_path))
        assert not (tmp_path / CURRENT_TARGET_REL).exists()

    def test_commit_validate_rollback_restores_bytes(self, tmp_path: Path) -> None:
        target = tmp_path / CURRENT_TARGET_REL
        target.parent.mkdir(parents=True, exist_ok=True)
        original = b'{"profiles":{}}\n'
        target.write_bytes(original)
        tx = ConfigTransaction(tmp_path)
        tx_id = tx.begin((Path(CURRENT_TARGET_REL),))
        new_bytes = b'{"profiles":{},"agents":{}}\n'
        result = tx.commit(
            tx_id, {Path(CURRENT_TARGET_REL): new_bytes},
            validate=lambda _: False,
        )
        assert result.committed is False
        assert target.read_bytes() == original

    def test_commit_validate_rollback_restores_absence(self, tmp_path: Path) -> None:
        tx = ConfigTransaction(tmp_path)
        tx_id = tx.begin((Path(CURRENT_TARGET_REL),))
        result = tx.commit(
            tx_id, {Path(CURRENT_TARGET_REL): b'{"profiles":{}}\n'},
            validate=lambda _: False,
        )
        assert result.committed is False
        assert not (tmp_path / CURRENT_TARGET_REL).exists()


class TestIdempotence:
    def test_second_run_identical(self, tmp_path: Path) -> None:
        _write_legacy(tmp_path)
        cap = _FakeCapabilityPort()
        first = configure_omo(_request(
            tmp_path, capability_port=cap, prompt=_FakePrompt(confirms=[True])))
        assert first.committed is True
        first_bytes = (tmp_path / CURRENT_TARGET_REL).read_bytes()
        second = configure_omo(_request(
            tmp_path, capability_port=cap, prompt=_FakePrompt(confirms=[True])))
        assert second.committed is True
        assert (tmp_path / CURRENT_TARGET_REL).read_bytes() == first_bytes

    def test_fresh_second_run_identical(self, tmp_path: Path) -> None:
        cap = _FakeCapabilityPort()
        configure_omo(_request(
            tmp_path, capability_port=cap, prompt=_FakePrompt(confirms=[True])))
        first_bytes = (tmp_path / CURRENT_TARGET_REL).read_bytes()
        second = configure_omo(_request(
            tmp_path, capability_port=cap, prompt=_FakePrompt(confirms=[True])))
        assert second.committed is True
        assert (tmp_path / CURRENT_TARGET_REL).read_bytes() == first_bytes


class TestSecretHygiene:
    def test_zero_canary(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        config_with_canary = {
            "$schema": "old",
            "agents": {"sisyphus": {"model": "zhipuai/glm-5.2", "variant": "max",
                                    "description": _CANARY}},
            "categories": {"deep": {"model": "zhipuai/glm-5.2", "variant": "max"}},
        }
        _write_legacy(tmp_path, config_with_canary)
        result = configure_omo(_request(tmp_path))
        out = capsys.readouterr().out
        assert result.committed is True
        assert _CANARY not in out
        report = "".join(str(f.detail) for f in result.facts) + str(result.safe_detail)
        assert _CANARY not in report


class TestProductionAdapterOffline:
    def test_absent_binary_returns_none_capability(self, tmp_path: Path) -> None:
        port = SubprocessOmoCapabilityPort(OmoCommand(
            argv=(str(tmp_path / "missing"),), cwd=tmp_path, timeout_seconds=0.1))
        assert port.resolve_capability() is None

    def test_absent_binary_dry_run_unsupported(self, tmp_path: Path) -> None:
        port = SubprocessOmoCapabilityPort(OmoCommand(
            argv=(str(tmp_path / "missing"),), cwd=tmp_path, timeout_seconds=0.1))
        assert port.migrate_dry_run().status is DryRunStatus.UNSUPPORTED

    def test_corrupt_doctor_json_returns_none(self, tmp_path: Path) -> None:
        script = tmp_path / "fake_omo.py"
        script.write_text("print('not json at all')\n", encoding="utf-8")
        port = SubprocessOmoCapabilityPort(OmoCommand(
            argv=(sys.executable, str(script)), cwd=tmp_path, timeout_seconds=5))
        assert port.resolve_capability() is None

    def test_no_network_during_resolve(self, tmp_path: Path) -> None:
        port = SubprocessOmoCapabilityPort(_doctor_cmd(tmp_path, "5.0.0-beta.5"))
        cap = port.resolve_capability()
        assert cap is not None
        assert cap.schema_url.startswith("https://")


class TestSchemaAssetIntegrity:
    def test_load_schema_succeeds(self) -> None:
        schema = load_schema_document()
        assert isinstance(schema, dict)
        assert "$id" in schema

    def test_missing_schema_asset_raises(self, tmp_path: Path, monkeypatch) -> None:
        import seam_init.omo_schema as mod
        monkeypatch.setattr(mod, "_asset_path", lambda: tmp_path / "nonexistent.json")
        with pytest.raises(SchemaAssetError):
            load_schema_document()

    def test_corrupt_schema_asset_raises(self, tmp_path: Path, monkeypatch) -> None:
        import seam_init.omo_schema as mod
        bad = tmp_path / "bad.json"
        bad.write_text("{not valid json", encoding="utf-8")
        monkeypatch.setattr(mod, "_asset_path", lambda: bad)
        with pytest.raises(SchemaAssetError):
            load_schema_document()

    def test_adapter_returns_none_when_schema_missing(self, tmp_path: Path, monkeypatch) -> None:
        import seam_init.omo_schema as mod
        monkeypatch.setattr(mod, "_asset_path", lambda: tmp_path / "nonexistent.json")
        port = SubprocessOmoCapabilityPort(_doctor_cmd(tmp_path, "5.0.0-beta.5"))
        assert port.resolve_capability() is None


class TestPortConformance:
    def test_fake_port_satisfies_protocol(self) -> None:
        assert isinstance(_FakeCapabilityPort(), OmoCapabilityPort)

    def test_default_home_satisfies_protocol(self) -> None:
        assert isinstance(DefaultHome(), HomePathPolicy)

    def test_discover_legacy_sources_all_finds_global(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        gp = home / GLOBAL_LEGACY_REL
        gp.parent.mkdir(parents=True, exist_ok=True)
        gp.write_bytes(json.dumps(_LEGACY_CONFIG).encode())
        sources = discover_legacy_sources_all(tmp_path, _FakeHome(home))
        assert len(sources) >= 1


class TestSemverTightness:
    def test_rejects_junk_with_embedded_version(self) -> None:
        with pytest.raises(Exception):
            parse_version("garbage5.0.0junk")

    def test_rejects_bare_two_components(self) -> None:
        with pytest.raises(Exception):
            parse_version("5.0")

    def test_accepts_v_prefix(self) -> None:
        assert parse_version("v5.0.0") == (5, 0, 0)

    def test_accepts_prerelease_suffix(self) -> None:
        assert parse_version("5.0.0-beta.5") == (5, 0, 0)

    def test_rejects_embedded_in_garbage_via_is_supported(self) -> None:
        assert is_supported_version("garbage5.0.0junk") is False


class TestSchemaKeywordCoverage:
    def test_const_rejects_mismatch(self) -> None:
        issues = validate_against_schema({"const": "category"}, "other")
        assert any("const" in i for i in issues)

    def test_const_accepts_match(self) -> None:
        assert validate_against_schema({"const": 1}, 1) == []

    def test_const_bool_not_int(self) -> None:
        assert validate_against_schema({"const": True}, 1) != []

    def test_pattern_rejects_mismatch(self) -> None:
        issues = validate_against_schema({"type": "string", "pattern": "^[a-z]+$"}, "ABC")
        assert any("pattern" in i for i in issues)

    def test_pattern_accepts_match(self) -> None:
        assert validate_against_schema({"type": "string", "pattern": "^[a-z]+$"}, "abc") == []

    def test_min_items_rejects_short(self) -> None:
        issues = validate_against_schema(
            {"type": "array", "items": {"type": "string"}, "minItems": 2}, ["one"])
        assert any("minItems" in i for i in issues)

    def test_min_items_accepts_valid(self) -> None:
        schema: JsonDict = {"type": "array", "items": {"type": "string"}, "minItems": 1}
        assert validate_against_schema(schema, ["one"]) == []

    def test_one_of_rejects_zero_matches(self) -> None:
        issues = validate_against_schema(
            {"oneOf": [{"type": "string"}, {"type": "integer"}]}, True)
        assert any("oneOf" in i for i in issues)

    def test_one_of_rejects_two_matches(self) -> None:
        issues = validate_against_schema(
            {"oneOf": [{"type": "string"}, {"minLength": 0}]}, "x")
        assert any("oneOf" in i for i in issues)

    def test_one_of_accepts_exactly_one(self) -> None:
        schema: JsonDict = {"oneOf": [{"type": "string"}, {"type": "integer"}]}
        assert validate_against_schema(schema, "x") == []

    def test_unsupported_keyword_fails_closed(self) -> None:
        issues = validate_against_schema(
            {"type": "string", "multipleOf": 2}, "x")
        assert any("unsupported" in i.lower() for i in issues)


class TestSchemaHashIntegrity:
    def test_valid_hash_loads(self) -> None:
        schema = load_schema_document()
        assert "$id" in schema

    def test_tampered_valid_json_rejected(self, tmp_path: Path, monkeypatch) -> None:
        import seam_init.omo_schema as mod
        tampered = tmp_path / "tampered.json"
        tampered.write_text(json.dumps({
            "$schema": "http://json-schema.org/draft-07/schema#",
            "$id": "https://wrong.example/schema.json",
            "type": "object",
        }), encoding="utf-8")
        monkeypatch.setattr(mod, "_asset_path", lambda: tampered)
        with pytest.raises(SchemaAssetError, match="hash"):
            load_schema_document()


class TestRuntimeCatalogValidation:
    def test_empty_catalog_rejected(self) -> None:
        assert validate_model_catalog(()) is not None

    def test_slashless_entry_rejected(self) -> None:
        assert validate_model_catalog(("noslash",)) is not None

    def test_valid_catalog_accepted(self) -> None:
        assert validate_model_catalog(("zhipuai/glm-5.2",)) is None

    def test_orchestrator_rejects_empty_catalog(self, tmp_path: Path) -> None:
        _write_legacy(tmp_path)
        result = configure_omo(_request(
            tmp_path, runtime=_FakeRuntime(models=())))
        assert result.committed is False
        assert "CATALOG_INVALID" in _kinds(result)

    def test_orchestrator_rejects_slashless_catalog(self, tmp_path: Path) -> None:
        _write_legacy(tmp_path)
        result = configure_omo(_request(
            tmp_path, runtime=_FakeRuntime(models=("noslash",)))
        )
        assert result.committed is False
        assert "CATALOG_INVALID" in _kinds(result)


class TestMultiProviderAllPaths:
    def test_existing_multi_provider_prompts(self, tmp_path: Path) -> None:
        target = tmp_path / CURRENT_TARGET_REL
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(json.dumps({
            "$schema": "old", "profiles": {},
            "agents": {"x": {"model": "zhipuai/glm-5.2", "variant": "max"}},
            "categories": {},
        }).encode() + b"\n")
        prompt = _FakePrompt(
            confirms=[True],
            asks=["zhipuai", "zhipuai/glm-5.2", "max"],
        )
        result = configure_omo(_request(
            tmp_path, prompt=prompt, runtime=_FakeRuntime(models=_MULTI_MODELS)))
        assert result.committed is True
        agents = _section(_read_target(tmp_path), "agents")
        x = _section(agents, "x")
        assert x["model"] == "zhipuai/glm-5.2"
        assert x["reasoning"] == "max"

    def test_legacy_multi_provider_prompts(self, tmp_path: Path) -> None:
        _write_legacy(tmp_path)
        prompt = _FakePrompt(
            confirms=[True],
            asks=["zhipuai", "zhipuai/glm-5.2", "max"],
        )
        result = configure_omo(_request(
            tmp_path, prompt=prompt, runtime=_FakeRuntime(models=_MULTI_MODELS)))
        assert result.committed is True
        agents = _section(_read_target(tmp_path), "agents")
        for entry in agents.values():
            assert isinstance(entry, dict)
            assert entry["model"] == "zhipuai/glm-5.2"

    def test_existing_multi_provider_applies_to_all(self, tmp_path: Path) -> None:
        target = tmp_path / CURRENT_TARGET_REL
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(json.dumps({
            "$schema": "old", "profiles": {},
            "agents": {
                "sisyphus": {"model": "openai/gpt-5.6-sol"},
                "oracle": {"model": "kimi-for-coding/k3"},
            },
            "categories": {"deep": {"model": "openai/gpt-5.6-sol"}},
        }).encode() + b"\n")
        prompt = _FakePrompt(
            confirms=[True],
            asks=["zhipuai", "zhipuai/glm-5.2", "high"],
        )
        result = configure_omo(_request(
            tmp_path, prompt=prompt, runtime=_FakeRuntime(models=_MULTI_MODELS)))
        assert result.committed is True
        target_data = _read_target(tmp_path)
        for section in ("agents", "categories"):
            sec = _section(target_data, section)
            for entry in sec.values():
                assert isinstance(entry, dict)
                assert entry["model"] == "zhipuai/glm-5.2"
                assert entry["reasoning"] == "high"


class TestCanonicalFromEmpty:
    def test_existing_empty_generates_canonical(self, tmp_path: Path) -> None:
        target = tmp_path / CURRENT_TARGET_REL
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(json.dumps({
            "$schema": "old", "profiles": {},
            "telemetry": {"enabled": False},
        }).encode() + b"\n")
        result = configure_omo(_request(
            tmp_path, prompt=_FakePrompt(confirms=[True])))
        assert result.committed is True
        target_data = _read_target(tmp_path)
        assert len(_section(target_data, "agents")) == 11
        assert len(_section(target_data, "categories")) == 8
        assert target_data["telemetry"] == {"enabled": False}

    def test_legacy_empty_generates_canonical_preserving_fields(self, tmp_path: Path) -> None:
        _write_legacy(tmp_path, {
            "$schema": "old",
            "telemetry": {"enabled": False},
            "[senpi]": {"telemetry": {"enabled": False}},
        })
        result = configure_omo(_request(tmp_path))
        assert result.committed is True
        target_data = _read_target(tmp_path)
        assert len(_section(target_data, "agents")) == 11
        assert target_data["telemetry"] == {"enabled": False}
        assert "[senpi]" in target_data


# --- helpers ---


def _spy_transactions(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    real_begin = ConfigTransaction.begin
    real_commit = ConfigTransaction.commit

    def _begin(self: ConfigTransaction, rel: tuple[Path, ...]) -> TransactionId:
        calls.append("begin")
        return real_begin(self, rel)

    def _commit(
        self: ConfigTransaction, tx_id: TransactionId,
        updates: Mapping[Path, bytes],
        validate: Callable[[Mapping[Path, bytes]], bool] | None = None,
    ) -> TransactionResult:
        calls.append("commit")
        return real_commit(self, tx_id, updates, validate=validate)

    monkeypatch.setattr(ConfigTransaction, "begin", _begin)
    monkeypatch.setattr(ConfigTransaction, "commit", _commit)
    return calls


def _arm_target_read_failure(monkeypatch: pytest.MonkeyPatch, target: Path) -> None:
    real_read_text = Path.read_text

    def _patched(
        self: Path, encoding: str | None = None, errors: str | None = None,
    ) -> str:
        if self == target:
            raise PermissionError("simulated read denial")
        return real_read_text(self, encoding=encoding, errors=errors)

    monkeypatch.setattr(Path, "read_text", _patched)


def _candidate(overrides: Mapping[str, object]) -> bytes:
    base: dict[str, object] = {"$schema": CURRENT_SCHEMA_URL, "profiles": {}}
    base.update(overrides)
    return json.dumps(base).encode()


def _doctor_cmd(tmp_path: Path, version: str) -> OmoCommand:
    script = tmp_path / "fake_omo.py"
    script.write_text(
        f"import json, sys\n"
        f"if 'doctor' in sys.argv:\n"
        f"  print(json.dumps({{'systemInfo': {{'pluginVersion': '{version}'}}}}))\n"
        f"elif 'migrate' in sys.argv:\n"
        f"  print(json.dumps({{'dryRun': True}}))\n",
        encoding="utf-8",
    )
    return OmoCommand(
        argv=(sys.executable, str(script)), cwd=tmp_path, timeout_seconds=5)
