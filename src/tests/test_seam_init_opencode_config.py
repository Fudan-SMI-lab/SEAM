"""Given/When/Then tests for OpenCode provider/model configuration.

Covers:
- Discovery of project, project-root, and global config candidates
- Zero-secret structural summary
- Merge authorization
- Custom OpenAI-compatible provider with exact camelCase baseURL/apiKey
- Idempotent semantic merge
- Empirical project-path observation proof before any transaction
- Pending-auth facts when the API key is skipped
- Interactive provider/model selection through PromptPort
- API-key flow with plaintext-risk confirmation and secret()
- Live v1 schema validation before commit (injectable boundary)
- OpenCode provider API model validation before commit
- Zero test-key characters across stdout, state.json, and result facts
"""
from __future__ import annotations

import json
import socket
import sys
from pathlib import Path
from typing import final

import pytest

from core.jsonc import parse_config_object
from seam_init.models import AuthState, ModelId, ProviderId, ProviderSelection
from seam_init.opencode_config import (
    CustomProviderSpec,
    ConfigTargetPolicy,
    DiscoveredConfig,
    JsonDict,
    OpencodeCommand,
    OpencodeConfigRequest,
    OpencodeSchemaValidator,
    SubprocessRuntimePort,
    apply_selection,
    build_custom_provider_override,
    collect_api_key,
    configure_opencode,
    discover_config_candidates,
    discover_project_config,
    prove_project_path_observed,
    summarize_structure,
)

_CANARY = "sk-test-canary-0123456789abcdef"


# --- test doubles ---------------------------------------------------------


@final
class _FakePrompt:
    """Scripted PromptPort; records calls, never reads stdin/getpass."""

    def __init__(
        self,
        *,
        confirms: list[bool] | None = None,
        secrets: list[str] | None = None,
        asks: list[str] | None = None,
    ) -> None:
        self._confirms = list(confirms or [])
        self._secrets = list(secrets or [])
        self._asks = list(asks or [])
        self.confirm_calls: list[tuple[str, bool]] = []
        self.secret_calls: list[str] = []
        self.ask_calls: list[tuple[str, object]] = []

    def ask(self, prompt: str, *, default: str | None = None) -> str:
        self.ask_calls.append((prompt, default))
        if not self._asks:
            raise AssertionError(f"unexpected ask: {prompt!r}")
        return self._asks.pop(0)

    def secret(self, prompt: str) -> str:
        self.secret_calls.append(prompt)
        if not self._secrets:
            raise AssertionError(f"unexpected secret: {prompt!r}")
        return self._secrets.pop(0)

    def confirm(self, prompt: str, *, default: bool = False) -> bool:
        self.confirm_calls.append((prompt, default))
        if not self._confirms:
            raise AssertionError(f"unexpected confirm: {prompt!r}")
        return self._confirms.pop(0)


@final
class _FakeRuntime:
    """RuntimePort double: returns canned debug config + models."""

    def __init__(
        self,
        *,
        config: JsonDict | None = None,
        models: tuple[str, ...] | None = None,
    ) -> None:
        self._config = config
        self._models = models

    def debug_config(self) -> JsonDict | None:
        return self._config

    def debug_models(self, config_bytes: bytes | None = None) -> tuple[str, ...] | None:
        _ = config_bytes
        return self._models


@final
class _FakeSchemaValidator:
    """SchemaValidator double: always returns the configured result."""

    def __init__(self, valid: bool = True) -> None:
        self._valid = valid
        self.calls: list[bytes] = []

    def validate(self, config_bytes: bytes) -> bool:
        self.calls.append(config_bytes)
        return self._valid


def _write_project_config(project_root: Path, content: str) -> Path:
    cfg = project_root / ".opencode" / "opencode.jsonc"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(content, encoding="utf-8")
    return cfg


def _write_fake_opencode(tmp_path: Path) -> Path:
    script = tmp_path / "fake_opencode.py"
    script.write_text(
        "from __future__ import annotations\n"
        "import http.server, json, os, pathlib, sys, time\n"
        "args = sys.argv[1:]\n"
        "log = os.environ.get('ARGV_CAPTURE')\n"
        "if log:\n"
        "    with pathlib.Path(log).open('a', encoding='utf-8') as handle:\n"
        "        handle.write(json.dumps(args) + '\\n')\n"
        "if args and args[0] == 'serve':\n"
        "    mode = os.environ.get('MODEL_SERVER_MODE', 'ready')\n"
        "    if mode == 'exit':\n"
        "        raise SystemExit(2)\n"
        "    if mode == 'never':\n"
        "        time.sleep(30)\n"
        "    port = int(args[args.index('--port') + 1])\n"
        "    candidate = os.environ.get('OPENCODE_CONFIG')\n"
        "    model_capture = os.environ.get('MODEL_CONFIG_CAPTURE')\n"
        "    content = os.environ.get('OPENCODE_CONFIG_CONTENT')\n"
        "    if model_capture and (content or candidate):\n"
        "        text = content or pathlib.Path(candidate).read_text(encoding='utf-8')\n"
        "        pathlib.Path(model_capture).write_text(text, encoding='utf-8')\n"
        "    class Handler(http.server.BaseHTTPRequestHandler):\n"
        "        def do_GET(self):\n"
        "            status = int(os.environ.get('MODEL_HTTP_STATUS', '200'))\n"
        "            self.send_response(status)\n"
        "            self.end_headers()\n"
        "            if status == 200:\n"
        "                body = os.environ.get('MODEL_API_BODY', '{\"providers\": []}')\n"
        "                self.wfile.write(body.encode('utf-8'))\n"
        "        def log_message(self, format, *args):\n"
        "            return\n"
        "    http.server.ThreadingHTTPServer(('127.0.0.1', port), Handler).serve_forever()\n"
        "elif args[-2:] == ['debug', 'config']:\n"
        "    debug_exit = os.environ.get('DEBUG_EXIT')\n"
        "    if debug_exit:\n"
        "        raise SystemExit(int(debug_exit))\n"
        "    debug_sleep = os.environ.get('DEBUG_SLEEP')\n"
        "    if debug_sleep:\n"
        "        time.sleep(float(debug_sleep))\n"
        "    debug_stdout = os.environ.get('DEBUG_STDOUT')\n"
        "    if debug_stdout:\n"
        "        print(debug_stdout)\n"
        "        raise SystemExit(0)\n"
        "    config = os.environ.get('OPENCODE_CONFIG')\n"
        "    if config:\n"
        "        text = pathlib.Path(config).read_text(encoding='utf-8')\n"
        "        capture = os.environ.get('SCHEMA_CAPTURE')\n"
        "        if capture:\n"
        "            pathlib.Path(capture).write_text(text, encoding='utf-8')\n"
        "        value = json.loads(text)\n"
        "        if 'invalidField' in value:\n"
        "            raise SystemExit(2)\n"
        "        print(json.dumps(value))\n"
        "    else:\n"
        "        print(json.dumps({'config_files': [os.environ['OBSERVED_PATH']]}))\n"
        "else:\n"
        "    raise SystemExit(3)\n",
        "utf-8",
    )
    return script


def _free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def _assert_port_closed(port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
        client.settimeout(0.2)
        assert client.connect_ex(("127.0.0.1", port)) != 0


def _runtime_observed(
    project_root: Path, *, models: tuple[str, ...] | None = None,
) -> _FakeRuntime:
    target = (project_root / ".opencode" / "opencode.jsonc").resolve()
    cfg: JsonDict = {"config_files": [str(target)], "provider": {}}
    return _FakeRuntime(config=cfg, models=models)


def _runtime_with_path(
    path: Path, *, models: tuple[str, ...] | None,
) -> _FakeRuntime:
    cfg: JsonDict = {"config_files": [str(path.resolve())]}
    return _FakeRuntime(config=cfg, models=models)


def _read_merged(project_root: Path) -> JsonDict:
    text = (project_root / ".opencode" / "opencode.jsonc").read_text("utf-8")
    parsed = parse_config_object(text)
    assert isinstance(parsed.value, dict)
    return parsed.value


def _provider_options(config: JsonDict, provider_id: str) -> JsonDict:
    providers = config.get("provider")
    assert isinstance(providers, dict)
    provider = providers.get(provider_id)
    assert isinstance(provider, dict)
    options = provider.get("options")
    assert isinstance(options, dict)
    return options


def _selection(
    provider: str = "myco", model: str = "my-model",
) -> ProviderSelection:
    return ProviderSelection(
        provider_id=ProviderId(provider),
        model_id=ModelId(model),
        base_url="https://api.myco.com/v1",
        auth_state=AuthState.PROVIDED,
    )


def _custom(
    provider: str = "myco", model: str = "my-model",
) -> CustomProviderSpec:
    return CustomProviderSpec(
        provider_id=provider,
        base_url="https://api.myco.com/v1",
        model_id=model,
        model_name="My Model",
    )


def _request(
    project_root: Path,
    *,
    prompt: _FakePrompt,
    runtime: _FakeRuntime,
    schema_validator: _FakeSchemaValidator | None = None,
    selection: ProviderSelection | None = None,
    api_key: str | None = None,
    custom: CustomProviderSpec | None = None,
    target_policy: ConfigTargetPolicy = ConfigTargetPolicy.PROJECT_DOT_OPENCODE,
) -> OpencodeConfigRequest:
    return OpencodeConfigRequest(
        project_root=project_root,
        prompt=prompt,
        runtime=runtime,
        schema_validator=schema_validator or _FakeSchemaValidator(),
        selection=selection,
        api_key=api_key,
        custom=custom,
        target_policy=target_policy,
    )


_MINIMAL_CONFIG = (
    "{\n"
    '  "$schema": "https://opencode.ai/config.json",\n'
    "  // existing plugin preserved by the structural merge\n"
    '  "plugin": ["legacy-plugin"],\n'
    '  "provider": {\n'
    '    "existing": {"options": {"apiKey": "sk-existing-secret"}}\n'
    "  }\n"
    "}\n"
)


# --- discover_project_config ---------------------------------------------


class TestDiscoverProjectConfig:
    def test_discovers_and_parses_jsonc_with_comments(self, tmp_path: Path) -> None:
        # Given: a project config with a line comment and trailing comma.
        _ = _write_project_config(
            tmp_path,
            "{\n  // comment\n  \"$schema\": \"x\",\n"
            '  "plugin": ["foo",]\n}\n',
        )
        # When
        discovered = discover_project_config(tmp_path)
        # Then: comments stripped, trailing comma tolerated, object parsed.
        assert discovered is not None
        assert isinstance(discovered, DiscoveredConfig)
        assert discovered.value["$schema"] == "x"
        assert discovered.value["plugin"] == ["foo"]

    def test_returns_none_when_no_config(self, tmp_path: Path) -> None:
        # Given: no .opencode directory at all.
        discovered = discover_project_config(tmp_path)
        assert discovered is None

    def test_raises_typed_error_on_invalid_jsonc(self, tmp_path: Path) -> None:
        # Given: malformed JSONC.
        _ = _write_project_config(tmp_path, "{ not valid")
        # When / Then
        with pytest.raises(ValueError):
            _ = discover_project_config(tmp_path)


# --- discover_config_candidates (NEW: gap 5) -----------------------------


class TestDiscoverConfigCandidates:
    def test_discovers_project_config(self, tmp_path: Path) -> None:
        # Given: a project config.
        _ = _write_project_config(tmp_path, '{"$schema": "x"}\n')
        # When
        candidates = discover_config_candidates(tmp_path)
        # Then: at least the project candidate.
        assert len(candidates) >= 1
        assert candidates[0].source == "project"

    def test_discovers_project_root_opencode_json(self, tmp_path: Path) -> None:
        # Given: an opencode.json in project root.
        root_cfg = tmp_path / "opencode.json"
        root_cfg.write_text('{"model": "openai/gpt-4"}\n', "utf-8")
        # When
        candidates = discover_config_candidates(tmp_path)
        # Then: the project-root candidate is discovered.
        sources = [c.source for c in candidates]
        assert "project-root" in sources

    def test_returns_empty_tuple_when_no_configs(self, tmp_path: Path) -> None:
        # Given: no configs anywhere.
        candidates = discover_config_candidates(tmp_path)
        assert candidates == ()

    def test_project_candidate_comes_first(self, tmp_path: Path) -> None:
        # Given: both project and project-root configs.
        _ = _write_project_config(tmp_path, '{"$schema": "a"}\n')
        root_cfg = tmp_path / "opencode.json"
        root_cfg.write_text('{"model": "x"}\n', "utf-8")
        # When
        candidates = discover_config_candidates(tmp_path)
        # Then: project (.opencode/opencode.jsonc) is first.
        assert candidates[0].source == "project"


# --- summarize_structure -------------------------------------------------


class TestSummarizeStructure:
    def test_lists_provider_names_and_model_counts(self) -> None:
        value: JsonDict = {
            "$schema": "https://opencode.ai/config.json",
            "provider": {
                "alpha": {"models": {"a1": {"name": "A1"}, "a2": {"name": "A2"}}},
                "beta": {},
            },
            "plugin": ["x", "y"],
        }
        text = str(summarize_structure(value))
        assert "alpha(2 models)" in text
        assert "beta(0 models)" in text
        assert "plugins:2" in text

    def test_never_reveals_apikey_values(self) -> None:
        value: JsonDict = {
            "provider": {"alpha": {"options": {"apiKey": _CANARY}}},
        }
        assert _CANARY not in str(summarize_structure(value))

    def test_handles_empty_config(self) -> None:
        value: JsonDict = {}
        assert "providers:(none)" in str(summarize_structure(value))


# --- build_custom_provider_override --------------------------------------


class TestBuildCustomProviderOverride:
    def test_uses_exact_camelcase_baseURL_and_apiKey(self) -> None:
        spec = _custom()
        override = build_custom_provider_override(spec, _CANARY)
        serialized = json.dumps(override)
        assert '"baseURL": "https://api.myco.com/v1"' in serialized
        assert '"apiKey": "' + _CANARY + '"' in serialized
        assert "base_url" not in serialized
        assert "api_key" not in serialized

    def test_api_key_lands_only_in_provider_options(self) -> None:
        spec = _custom()
        override = build_custom_provider_override(spec, _CANARY)
        serialized = json.dumps(override)
        assert serialized.count(_CANARY) == 1

    def test_skipped_key_omits_apiKey_field(self) -> None:
        spec = _custom()
        override = build_custom_provider_override(spec, None)
        assert "apiKey" not in json.dumps(override)

    def test_top_level_model_is_provider_slash_model(self) -> None:
        spec = _custom(provider="myco", model="gpt-x")
        override = build_custom_provider_override(spec, None)
        assert override["model"] == "myco/gpt-x"

    def test_uses_openai_compatible_npm(self) -> None:
        spec = _custom()
        override = build_custom_provider_override(spec, None)
        serialized = json.dumps(override)
        assert '"@ai-sdk/openai-compatible"' in serialized


# --- apply_selection + idempotence ---------------------------------------


class TestApplySelection:
    def test_custom_provider_merge_preserves_schema_and_plugins(self) -> None:
        base: JsonDict = {
            "$schema": "https://opencode.ai/config.json",
            "plugin": ["legacy"],
            "permissions": {"edit": True},
        }
        merged = apply_selection(base, _selection(), _CANARY, _custom())
        assert merged["$schema"] == "https://opencode.ai/config.json"
        assert merged["plugin"] == ["legacy"]
        assert merged["permissions"] == {"edit": True}
        assert '"myco"' in json.dumps(merged)
        assert merged["model"] == "myco/my-model"

    def test_merge_is_idempotent_for_custom_provider(self) -> None:
        base: JsonDict = {"$schema": "x", "plugin": ["foo"]}
        once = apply_selection(base, _selection(), _CANARY, _custom())
        twice = apply_selection(once, _selection(), _CANARY, _custom())
        assert twice == once

    def test_native_provider_only_sets_top_level_model(self) -> None:
        base: JsonDict = {
            "provider": {"openai": {"options": {"apiKey": "sk-x"}}},
        }
        merged = apply_selection(
            base,
            ProviderSelection(
                provider_id=ProviderId("openai"),
                model_id=ModelId("gpt-4"),
            ),
            None,
            None,
        )
        assert merged["model"] == "openai/gpt-4"
        serialized = json.dumps(merged)
        assert '"apiKey": "sk-x"' in serialized

    def test_native_provider_supplied_key_has_one_exact_location(self) -> None:
        # Given: a native provider has unrelated fields but no key.
        base: JsonDict = {
            "provider": {
                "openai": {
                    "name": "OpenAI",
                    "options": {"baseURL": "https://proxy.example/v1"},
                },
            },
        }
        # When
        merged = apply_selection(
            base,
            ProviderSelection(
                provider_id=ProviderId("openai"),
                model_id=ModelId("gpt-4"),
            ),
            _CANARY,
            None,
        )
        # Then: canary exists exactly once at provider.openai.options.apiKey.
        serialized = json.dumps(merged)
        assert serialized.count(_CANARY) == 1
        provider = merged["provider"]
        assert isinstance(provider, dict)
        openai = provider["openai"]
        assert isinstance(openai, dict)
        options = openai["options"]
        assert isinstance(options, dict)
        assert options["apiKey"] == _CANARY
        assert options["baseURL"] == "https://proxy.example/v1"
        assert openai["name"] == "OpenAI"
        assert merged["model"] == "openai/gpt-4"

    def test_does_not_mutate_base(self) -> None:
        base: JsonDict = {"$schema": "keep", "plugin": ["foo"]}
        snapshot = json.dumps(base, sort_keys=True)
        _ = apply_selection(base, _selection(), _CANARY, _custom())
        assert json.dumps(base, sort_keys=True) == snapshot


# --- prove_project_path_observed -----------------------------------------


class TestProveProjectPathObserved:
    def test_returns_true_when_path_in_debug_output(self, tmp_path: Path) -> None:
        debug = _runtime_observed(tmp_path)
        cfg = debug.debug_config()
        assert prove_project_path_observed(
            cfg, tmp_path, ".opencode/opencode.jsonc",
        ) is True

    def test_returns_false_when_debug_is_none(self, tmp_path: Path) -> None:
        assert prove_project_path_observed(
            None, tmp_path, ".opencode/opencode.jsonc",
        ) is False

    def test_returns_false_when_path_absent(self, tmp_path: Path) -> None:
        debug: JsonDict = {"provider": {}}
        assert prove_project_path_observed(
            debug, tmp_path, ".opencode/opencode.jsonc",
        ) is False


# --- configure_opencode happy path ---------------------------------------


class TestConfigureOpencodeHappyPath:
    def test_custom_provider_with_key_commits_via_transaction(
        self, tmp_path: Path,
    ) -> None:
        # Given: existing config + observed runtime + authorized prompt.
        _ = _write_project_config(tmp_path, _MINIMAL_CONFIG)
        prompt = _FakePrompt(confirms=[True])
        runtime = _runtime_observed(tmp_path, models=("myco/my-model",))
        request = _request(
            tmp_path, prompt=prompt, runtime=runtime,
            selection=_selection(), api_key=_CANARY, custom=_custom(),
        )
        # When
        result = configure_opencode(request)
        # Then: committed; facts cover the expected lifecycle.
        assert result.committed is True
        assert result.pending_auth is False
        kinds = {f.kind for f in result.facts}
        assert "MERGE_AUTHORIZED" in kinds
        assert "PATH_OBSERVED" in kinds
        assert "AUTH_PROVIDED" in kinds
        assert "SCHEMA_VALID" in kinds
        assert "MODEL_VALIDATED" in kinds
        assert "COMMITTED" in kinds
        # And: the merged file has exact camelCase keys.
        merged = _read_merged(tmp_path)
        merged_str = json.dumps(merged)
        assert '"baseURL": "https://api.myco.com/v1"' in merged_str
        assert '"apiKey": "' + _CANARY + '"' in merged_str
        assert merged["model"] == "myco/my-model"
        assert merged["$schema"] == "https://opencode.ai/config.json"
        assert merged["plugin"] == ["legacy-plugin"]

    def test_summary_emitted_before_authorize(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        _ = _write_project_config(tmp_path, _MINIMAL_CONFIG)
        prompt = _FakePrompt(confirms=[False])
        runtime = _runtime_observed(tmp_path)
        request = _request(
            tmp_path, prompt=prompt, runtime=runtime,
            selection=_selection(), api_key=None, custom=_custom(),
        )
        result = configure_opencode(request)
        out = capsys.readouterr().out
        assert "OpenCode config summary:" in out
        assert "existing" in out
        assert result.committed is False
        assert "MERGE_DECLINED" in {f.kind for f in result.facts}

    def test_normalization_warning_precedes_write_and_decline_preserves_bytes(
        self, tmp_path: Path,
    ) -> None:
        # Given: JSONC with comments/trailing comma and a declining prompt.
        original = (
            b'{\n  // keep this exact backup\n  "$schema": "https://opencode.ai/config.json",\n}\n'
        )
        cfg = tmp_path / ".opencode" / "opencode.jsonc"
        cfg.parent.mkdir(parents=True)
        cfg.write_bytes(original)
        prompt = _FakePrompt(confirms=[False])
        request = _request(
            tmp_path, prompt=prompt, runtime=_runtime_observed(tmp_path),
            selection=_selection(), api_key=_CANARY, custom=_custom(),
        )
        # When
        result = configure_opencode(request)
        # Then: prompt is explicit and decline retains exact bytes with no transaction.
        assert result.committed is False
        warning = prompt.confirm_calls[0][0].lower()
        assert "comments" in warning
        assert "trailing" in warning
        assert "normalized" in warning or "lost" in warning
        assert "owner-only backup" in warning
        assert cfg.read_bytes() == original
        assert not (tmp_path / ".seam-init").exists()


# --- configure_opencode failure paths -------------------------------------


class TestConfigureOpencodeFailures:
    def test_declined_merge_leaves_original_bytes_intact(self, tmp_path: Path) -> None:
        cfg = _write_project_config(tmp_path, _MINIMAL_CONFIG)
        original = cfg.read_bytes()
        prompt = _FakePrompt(confirms=[False])
        runtime = _runtime_observed(tmp_path)
        request = _request(
            tmp_path, prompt=prompt, runtime=runtime,
            selection=_selection(), api_key=_CANARY, custom=_custom(),
        )
        result = configure_opencode(request)
        assert result.committed is False
        assert cfg.read_bytes() == original
        assert result.transaction is None

    def test_unobserved_project_path_aborts_before_write(self, tmp_path: Path) -> None:
        cfg = _write_project_config(tmp_path, _MINIMAL_CONFIG)
        original = cfg.read_bytes()
        prompt = _FakePrompt(confirms=[True])
        runtime = _FakeRuntime(config={"provider": {}}, models=("myco/my-model",))
        request = _request(
            tmp_path, prompt=prompt, runtime=runtime,
            selection=_selection(), api_key=_CANARY, custom=_custom(),
        )
        result = configure_opencode(request)
        assert result.committed is False
        assert cfg.read_bytes() == original
        assert "PATH_UNOBSERVED" in {f.kind for f in result.facts}

    def test_no_project_config_returns_failure_without_prompt(self, tmp_path: Path) -> None:
        prompt = _FakePrompt()
        runtime = _runtime_observed(tmp_path)
        request = _request(
            tmp_path, prompt=prompt, runtime=runtime,
            selection=_selection(), api_key=_CANARY, custom=_custom(),
        )
        result = configure_opencode(request)
        assert result.committed is False
        assert "NO_PROJECT_CONFIG" in {f.kind for f in result.facts}
        assert prompt.confirm_calls == []

    def test_schema_validation_failure_leaves_original_intact(self, tmp_path: Path) -> None:
        # Given: schema validator returns False.
        cfg = _write_project_config(tmp_path, _MINIMAL_CONFIG)
        original = cfg.read_bytes()
        prompt = _FakePrompt(confirms=[True])
        runtime = _runtime_observed(tmp_path, models=("myco/my-model",))
        bad_validator = _FakeSchemaValidator(valid=False)
        request = _request(
            tmp_path, prompt=prompt, runtime=runtime,
            schema_validator=bad_validator,
            selection=_selection(), api_key=_CANARY, custom=_custom(),
        )
        # When
        result = configure_opencode(request)
        # Then: aborted; original bytes intact; SCHEMA_INVALID fact.
        assert result.committed is False
        assert cfg.read_bytes() == original
        assert "SCHEMA_INVALID" in {f.kind for f in result.facts}

    def test_unknown_model_aborts_before_write(self, tmp_path: Path) -> None:
        # Given: model-list does NOT contain the selected model.
        cfg = _write_project_config(tmp_path, _MINIMAL_CONFIG)
        original = cfg.read_bytes()
        prompt = _FakePrompt(confirms=[True])
        runtime = _runtime_observed(tmp_path, models=("other/unknown",))
        request = _request(
            tmp_path, prompt=prompt, runtime=runtime,
            selection=_selection(), api_key=_CANARY, custom=_custom(),
        )
        # When
        result = configure_opencode(request)
        # Then: aborted; original bytes intact; MODEL_NOT_FOUND fact.
        assert result.committed is False
        assert cfg.read_bytes() == original
        assert "MODEL_NOT_FOUND" in {f.kind for f in result.facts}


class TestCandidateTargetSelection:
    def test_only_global_candidate_is_read_only(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # Given: only the global alternative exists.
        home = tmp_path / "home"
        global_cfg = home / ".config" / "opencode" / "opencode.json"
        global_cfg.parent.mkdir(parents=True)
        original = b'{"model":"global/x"}\n'
        global_cfg.write_bytes(original)

        def fake_home() -> Path:
            return home

        monkeypatch.setattr(Path, "home", fake_home)
        request = _request(
            tmp_path, prompt=_FakePrompt(), runtime=_FakeRuntime(),
            selection=_selection(), api_key=_CANARY, custom=_custom(),
        )
        # When
        result = configure_opencode(request)
        # Then: global remains read-only and no default project file appears.
        assert result.committed is False
        assert "TARGET_NOT_FOUND" in {f.kind for f in result.facts}
        assert global_cfg.read_bytes() == original
        assert not (tmp_path / ".opencode" / "opencode.jsonc").exists()
        assert not (tmp_path / ".seam-init").exists()

    def test_only_root_candidate_can_be_explicitly_selected(self, tmp_path: Path) -> None:
        # Given: only project-root opencode.json exists and policy selects it.
        root_cfg = tmp_path / "opencode.json"
        root_cfg.write_text('{"provider":{"myco":{}}}\n', "utf-8")
        runtime = _runtime_with_path(root_cfg, models=("myco/my-model",))
        request = _request(
            tmp_path, prompt=_FakePrompt(confirms=[True]), runtime=runtime,
            selection=_selection(), api_key=_CANARY, custom=_custom(),
            target_policy=ConfigTargetPolicy.PROJECT_ROOT,
        )
        # When
        result = configure_opencode(request)
        # Then: the proven root candidate, not the default path, is written.
        assert result.committed is True
        assert '"model": "myco/my-model"' in root_cfg.read_text("utf-8")
        assert not (tmp_path / ".opencode" / "opencode.jsonc").exists()

    def test_multiple_candidates_write_only_policy_target(self, tmp_path: Path) -> None:
        # Given: both project candidates exist, but only root is observed.
        dot_cfg = _write_project_config(tmp_path, _MINIMAL_CONFIG)
        dot_original = dot_cfg.read_bytes()
        root_cfg = tmp_path / "opencode.json"
        root_cfg.write_text('{"provider":{"myco":{}}}\n', "utf-8")
        runtime = _runtime_with_path(root_cfg, models=("myco/my-model",))
        request = _request(
            tmp_path, prompt=_FakePrompt(confirms=[True]), runtime=runtime,
            selection=_selection(), api_key=_CANARY, custom=_custom(),
            target_policy=ConfigTargetPolicy.PROJECT_ROOT,
        )
        # When
        result = configure_opencode(request)
        # Then: root changes and dot-config remains byte-identical.
        assert result.committed is True
        assert dot_cfg.read_bytes() == dot_original
        assert '"model": "myco/my-model"' in root_cfg.read_text("utf-8")

    def test_no_observed_candidate_leaves_all_bytes_intact(self, tmp_path: Path) -> None:
        # Given: target exists, but runtime observes neither candidate.
        dot_cfg = _write_project_config(tmp_path, _MINIMAL_CONFIG)
        original = dot_cfg.read_bytes()
        request = _request(
            tmp_path, prompt=_FakePrompt(confirms=[True]),
            runtime=_FakeRuntime(config={"config_files": []}, models=("myco/my-model",)),
            selection=_selection(), api_key=_CANARY, custom=_custom(),
        )
        # When
        result = configure_opencode(request)
        # Then
        assert result.committed is False
        assert "PATH_UNOBSERVED" in {f.kind for f in result.facts}
        assert dot_cfg.read_bytes() == original
        assert not (tmp_path / ".seam-init").exists()


# --- skipped key produces pending-auth facts ------------------------------


class TestSkippedKeyPendingAuth:
    def test_skipped_key_produces_pending_auth_fact_and_no_apiKey(
        self, tmp_path: Path,
    ) -> None:
        _ = _write_project_config(tmp_path, _MINIMAL_CONFIG)
        prompt = _FakePrompt(confirms=[True, False])  # authorize + decline risk
        runtime = _runtime_observed(tmp_path, models=("myco/my-model",))
        request = _request(
            tmp_path, prompt=prompt, runtime=runtime,
            selection=_selection(), api_key=None, custom=_custom(),
        )
        result = configure_opencode(request)
        assert result.committed is True
        assert result.pending_auth is True
        assert "AUTH_SKIPPED" in {f.kind for f in result.facts}
        merged = _read_merged(tmp_path)
        options = _provider_options(merged, "myco")
        assert "apiKey" not in options
        assert options["baseURL"] == "https://api.myco.com/v1"


# --- interactive selection (NEW: gap 1) ----------------------------------


class TestInteractiveSelection:
    def test_interactive_native_provider_selection(self, tmp_path: Path) -> None:
        # Given: selection is None; prompt provides provider + model.
        _ = _write_project_config(tmp_path, _MINIMAL_CONFIG)
        prompt = _FakePrompt(
            confirms=[True, False],
            asks=["openai", "gpt-4"],
        )
        runtime = _runtime_observed(
            tmp_path, models=("openai/gpt-4",),
        )
        request = _request(
            tmp_path, prompt=prompt, runtime=runtime,
            selection=None,
        )
        # When
        result = configure_opencode(request)
        # Then: committed with native provider.
        assert result.committed is True
        merged = _read_merged(tmp_path)
        assert merged["model"] == "openai/gpt-4"

    def test_interactive_native_provider_collects_key(self, tmp_path: Path) -> None:
        # Given: native selection has no existing auth and risk is accepted.
        _ = _write_project_config(tmp_path, _MINIMAL_CONFIG)
        prompt = _FakePrompt(
            confirms=[True, True],
            asks=["openai", "gpt-4"],
            secrets=[_CANARY],
        )
        runtime = _runtime_observed(tmp_path, models=("openai/gpt-4",))
        request = _request(tmp_path, prompt=prompt, runtime=runtime, selection=None)
        # When
        result = configure_opencode(request)
        # Then: native key is collected once and committed only in provider options.
        assert result.committed is True
        assert result.pending_auth is False
        assert len(prompt.secret_calls) == 1
        serialized = json.dumps(_read_merged(tmp_path))
        assert serialized.count(_CANARY) == 1
        assert '"apiKey": "' + _CANARY + '"' in serialized

    def test_interactive_native_key_decline_is_pending_auth(self, tmp_path: Path) -> None:
        # Given: native selection has no auth and risk consent is declined.
        _ = _write_project_config(tmp_path, _MINIMAL_CONFIG)
        prompt = _FakePrompt(
            confirms=[True, False],
            asks=["openai", "gpt-4"],
        )
        runtime = _runtime_observed(tmp_path, models=("openai/gpt-4",))
        request = _request(tmp_path, prompt=prompt, runtime=runtime, selection=None)
        # When
        result = configure_opencode(request)
        # Then
        assert result.committed is True
        assert result.pending_auth is True
        assert "AUTH_SKIPPED" in {f.kind for f in result.facts}
        assert prompt.secret_calls == []

    def test_interactive_custom_provider_selection(self, tmp_path: Path) -> None:
        # Given: selection is None; prompt defines a custom provider.
        _ = _write_project_config(tmp_path, _MINIMAL_CONFIG)
        prompt = _FakePrompt(
            confirms=[True, False],  # merge authorize + decline risk
            asks=[
                "custom",          # select_provider_model: "custom"
                "acme",            # provider id
                "https://api.acme.com/v1",  # base URL
                "acme-1",          # model id
                "Acme One",        # model name
            ],
        )
        runtime = _runtime_observed(
            tmp_path, models=("acme/acme-1",),
        )
        request = _request(
            tmp_path, prompt=prompt, runtime=runtime,
            selection=None,
        )
        # When
        result = configure_opencode(request)
        # Then: committed with custom provider.
        assert result.committed is True
        merged = _read_merged(tmp_path)
        assert merged["model"] == "acme/acme-1"
        merged_str = json.dumps(merged)
        assert '"npm": "@ai-sdk/openai-compatible"' in merged_str
        assert '"baseURL": "https://api.acme.com/v1"' in merged_str

    def test_selection_cancelled_returns_without_write(self, tmp_path: Path) -> None:
        _ = _write_project_config(tmp_path, _MINIMAL_CONFIG)
        prompt = _FakePrompt(confirms=[True], asks=[""])  # empty → cancel
        runtime = _runtime_observed(tmp_path)
        request = _request(tmp_path, prompt=prompt, runtime=runtime, selection=None)
        result = configure_opencode(request)
        assert result.committed is False
        assert "SELECTION_CANCELLED" in {f.kind for f in result.facts}


# --- API-key flow with secret() (NEW: gap 2) ------------------------------


class TestApiKeyFlow:
    def test_collect_api_key_confirm_then_secret(self) -> None:
        # Given: prompt confirms risk and provides a key.
        prompt = _FakePrompt(confirms=[True], secrets=["my-secret-key"])
        # When
        key = collect_api_key(prompt)
        # Then: key returned; risk-confirmation prompt was issued.
        assert key == "my-secret-key"
        assert len(prompt.confirm_calls) == 1
        assert "plaintext" in prompt.confirm_calls[0][0].lower()

    def test_collect_api_key_declined_returns_none(self) -> None:
        # Given: prompt declines the plaintext-risk confirmation.
        prompt = _FakePrompt(confirms=[False])
        # When
        key = collect_api_key(prompt)
        # Then: None returned; secret() was never called.
        assert key is None
        assert prompt.secret_calls == []

    def test_collect_api_key_empty_returns_none(self) -> None:
        # Given: prompt confirms but enters empty key.
        prompt = _FakePrompt(confirms=[True], secrets=["  "])
        key = collect_api_key(prompt)
        assert key is None

    def test_existing_native_key_suppresses_secret_prompt(self, tmp_path: Path) -> None:
        # Given: the selected native provider already has a stored key.
        _ = _write_project_config(tmp_path, _MINIMAL_CONFIG)
        prompt = _FakePrompt(confirms=[True])
        runtime = _runtime_observed(tmp_path, models=("existing/existing-model",))
        selection = ProviderSelection(
            provider_id=ProviderId("existing"), model_id=ModelId("existing-model"),
        )
        request = _request(
            tmp_path, prompt=prompt, runtime=runtime,
            selection=selection, api_key=None,
        )
        # When
        result = configure_opencode(request)
        # Then: only merge consent occurs; the existing key remains private.
        assert result.committed is True
        assert result.pending_auth is False
        assert len(prompt.confirm_calls) == 1
        assert prompt.secret_calls == []
        assert "plaintext" not in prompt.confirm_calls[0][0].lower()
        merged = _read_merged(tmp_path)
        assert _provider_options(merged, "existing")["apiKey"] == "sk-existing-secret"
        report = "".join(str(f.detail) for f in result.facts) + str(result.safe_detail)
        assert "sk-existing-secret" not in report

    def test_interactive_custom_provider_collects_key_via_secret(
        self, tmp_path: Path,
    ) -> None:
        # Given: selection=None and api_key=None → interactive custom + key flow.
        _ = _write_project_config(tmp_path, _MINIMAL_CONFIG)
        prompt = _FakePrompt(
            confirms=[True, True],  # merge authorize + plaintext-risk
            secrets=[_CANARY],
            asks=[
                "custom", "myco", "https://api.myco.com/v1",
                "my-model", "My Model",
            ],
        )
        runtime = _runtime_observed(tmp_path, models=("myco/my-model",))
        request = _request(tmp_path, prompt=prompt, runtime=runtime, selection=None)
        # When
        result = configure_opencode(request)
        # Then: committed with the key from secret().
        assert result.committed is True
        assert result.pending_auth is False
        assert len(prompt.secret_calls) == 1
        merged = _read_merged(tmp_path)
        merged_str = json.dumps(merged)
        assert _CANARY not in str(result.safe_detail)
        assert '"apiKey": "' + _CANARY + '"' in merged_str

    def test_interactive_custom_provider_declined_key_is_pending_auth(
        self, tmp_path: Path,
    ) -> None:
        # Given: selection=None, api_key=None, but plaintext-risk declined.
        _ = _write_project_config(tmp_path, _MINIMAL_CONFIG)
        prompt = _FakePrompt(
            confirms=[True, False],  # merge authorize + decline risk
            asks=[
                "custom", "myco", "https://api.myco.com/v1",
                "my-model", "My Model",
            ],
        )
        runtime = _runtime_observed(tmp_path, models=("myco/my-model",))
        request = _request(tmp_path, prompt=prompt, runtime=runtime, selection=None)
        # When
        result = configure_opencode(request)
        # Then: committed but pending auth; secret() never called.
        assert result.committed is True
        assert result.pending_auth is True
        assert prompt.secret_calls == []
        merged = _read_merged(tmp_path)
        options = _provider_options(merged, "myco")
        assert "apiKey" not in options


# --- schema validation (NEW: gap 3) ---------------------------------------


class TestSchemaValidation:
    def test_schema_validator_called_before_commit(self, tmp_path: Path) -> None:
        _ = _write_project_config(tmp_path, _MINIMAL_CONFIG)
        prompt = _FakePrompt(confirms=[True])
        runtime = _runtime_observed(tmp_path, models=("myco/my-model",))
        validator = _FakeSchemaValidator(valid=True)
        request = _request(
            tmp_path, prompt=prompt, runtime=runtime,
            schema_validator=validator,
            selection=_selection(), api_key=_CANARY, custom=_custom(),
        )
        result = configure_opencode(request)
        assert result.committed is True
        assert len(validator.calls) == 1


class TestProductionAdapters:
    def test_runtime_adapter_queries_provider_api_and_stops_server(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Given: a local fake OpenCode server and a candidate with a secret.
        script = _write_fake_opencode(tmp_path)
        observed = tmp_path / ".opencode" / "opencode.jsonc"
        capture = tmp_path / "argv.jsonl"
        config_capture = tmp_path / "model-candidate.json"
        port = _free_local_port()
        monkeypatch.setenv("OBSERVED_PATH", str(observed.resolve()))
        monkeypatch.setenv("ARGV_CAPTURE", str(capture))
        monkeypatch.setenv("MODEL_CONFIG_CAPTURE", str(config_capture))
        monkeypatch.setenv("MODEL_API_BODY", json.dumps({"providers": [
            {"id": "myco", "models": {"my-model": {}}},
            {"id": "openai", "models": {"gpt-4": {}}},
        ]}))
        command = OpencodeCommand(
            argv=(sys.executable, str(script)), cwd=tmp_path, timeout_seconds=2,
            model_server_port=port,
        )
        adapter = SubprocessRuntimePort(command)
        # When
        config = adapter.debug_config()
        models = adapter.debug_models(json.dumps({
            "provider": {"myco": {"options": {"apiKey": _CANARY}}},
        }).encode())
        # Then: the supported API returns models and the child is cleaned up.
        assert config is not None
        assert prove_project_path_observed(config, tmp_path, ".opencode/opencode.jsonc")
        assert models == ("myco/my-model", "openai/gpt-4")
        calls = capture.read_text("utf-8").splitlines()
        assert json.loads(calls[0])[-2:] == ["debug", "config"]
        assert json.loads(calls[1])[0] == "serve"
        assert all(json.loads(call)[-2:] != ["debug", "models"] for call in calls)
        assert _CANARY not in config_capture.read_text("utf-8")
        _assert_port_closed(port)

    def test_runtime_adapter_returns_empty_models_for_valid_empty_api(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Given
        script = _write_fake_opencode(tmp_path)
        port = _free_local_port()
        monkeypatch.setenv("MODEL_API_BODY", '{"providers": []}')
        adapter = SubprocessRuntimePort(OpencodeCommand(
            argv=(sys.executable, str(script)), cwd=tmp_path,
            timeout_seconds=2, model_server_port=port,
        ))
        # When / Then
        assert adapter.debug_models() == ()
        _assert_port_closed(port)

    def test_runtime_adapter_returns_none_when_server_exits(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Given
        script = _write_fake_opencode(tmp_path)
        port = _free_local_port()
        monkeypatch.setenv("MODEL_SERVER_MODE", "exit")
        adapter = SubprocessRuntimePort(OpencodeCommand(
            argv=(sys.executable, str(script)), cwd=tmp_path,
            timeout_seconds=0.5, model_server_port=port,
        ))
        # When / Then
        assert adapter.debug_models() is None
        _assert_port_closed(port)

    def test_runtime_adapter_returns_none_when_server_never_ready(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Given
        script = _write_fake_opencode(tmp_path)
        port = _free_local_port()
        monkeypatch.setenv("MODEL_SERVER_MODE", "never")
        adapter = SubprocessRuntimePort(OpencodeCommand(
            argv=(sys.executable, str(script)), cwd=tmp_path,
            timeout_seconds=0.2, model_server_port=port,
        ))
        # When / Then
        assert adapter.debug_models() is None
        _assert_port_closed(port)

    def test_runtime_adapter_returns_none_for_model_api_non_200(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Given
        script = _write_fake_opencode(tmp_path)
        port = _free_local_port()
        monkeypatch.setenv("MODEL_HTTP_STATUS", "503")
        adapter = SubprocessRuntimePort(OpencodeCommand(
            argv=(sys.executable, str(script)), cwd=tmp_path,
            timeout_seconds=2, model_server_port=port,
        ))
        # When / Then
        assert adapter.debug_models() is None
        _assert_port_closed(port)

    @pytest.mark.parametrize("body", ["{bad json", '{"providers": {}}'])
    def test_runtime_adapter_returns_none_for_malformed_model_api(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str,
    ) -> None:
        # Given
        script = _write_fake_opencode(tmp_path)
        port = _free_local_port()
        monkeypatch.setenv("MODEL_API_BODY", body)
        adapter = SubprocessRuntimePort(OpencodeCommand(
            argv=(sys.executable, str(script)), cwd=tmp_path,
            timeout_seconds=2, model_server_port=port,
        ))
        # When / Then
        assert adapter.debug_models() is None
        _assert_port_closed(port)

    @pytest.mark.parametrize(
        ("name", "value", "timeout"),
        [("DEBUG_EXIT", "2", 2.0), ("DEBUG_SLEEP", "10", 0.2),
         ("DEBUG_STDOUT", "{bad json", 2.0)],
    )
    def test_debug_config_returns_none_for_subprocess_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        name: str, value: str, timeout: float,
    ) -> None:
        # Given
        script = _write_fake_opencode(tmp_path)
        monkeypatch.setenv(name, value)
        adapter = SubprocessRuntimePort(OpencodeCommand(
            argv=(sys.executable, str(script)), cwd=tmp_path,
            timeout_seconds=timeout,
        ))
        # When / Then
        assert adapter.debug_config() is None

    def test_runtime_adapter_returns_none_when_executable_unavailable(self, tmp_path: Path) -> None:
        # Given
        adapter = SubprocessRuntimePort(OpencodeCommand(
            argv=(str(tmp_path / "missing-opencode"),), cwd=tmp_path, timeout_seconds=0.1,
        ))
        # When / Then
        assert adapter.debug_config() is None
        assert adapter.debug_models() is None

    def test_schema_adapter_uses_redacted_private_candidate(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Given: a fake live validator and a candidate containing the canary.
        script = _write_fake_opencode(tmp_path)
        capture = tmp_path / "schema-candidate.json"
        monkeypatch.setenv("SCHEMA_CAPTURE", str(capture))
        command = OpencodeCommand(
            argv=(sys.executable, str(script)), cwd=tmp_path, timeout_seconds=2,
        )
        validator = OpencodeSchemaValidator(command)
        candidate = json.dumps({
            "$schema": "https://opencode.ai/config.json",
            "provider": {"myco": {"options": {"apiKey": _CANARY}}},
            "model": "myco/my-model",
        }).encode()
        # When
        valid = validator.validate(candidate)
        # Then: live command validates shape while no secret reaches the temp file.
        assert valid is True
        validated_text = capture.read_text("utf-8")
        assert _CANARY not in validated_text
        assert "<REDACTED>" in validated_text

    def test_schema_adapter_fails_closed_when_unavailable(self, tmp_path: Path) -> None:
        # Given
        validator = OpencodeSchemaValidator(OpencodeCommand(
            argv=(str(tmp_path / "missing-opencode"),), cwd=tmp_path, timeout_seconds=0.1,
        ))
        # When / Then
        assert validator.validate(b'{"model":"myco/my-model"}') is False


# --- model-list validation (NEW: gap 4) -----------------------------------


class TestModelListValidation:
    def test_model_in_list_proceeds(self, tmp_path: Path) -> None:
        _ = _write_project_config(tmp_path, _MINIMAL_CONFIG)
        prompt = _FakePrompt(confirms=[True])
        runtime = _runtime_observed(tmp_path, models=("myco/my-model", "other/x"))
        request = _request(
            tmp_path, prompt=prompt, runtime=runtime,
            selection=_selection(), api_key=_CANARY, custom=_custom(),
        )
        result = configure_opencode(request)
        assert result.committed is True
        assert "MODEL_VALIDATED" in {f.kind for f in result.facts}

    def test_model_list_none_fails_closed_without_transaction(self, tmp_path: Path) -> None:
        # Given: opencode model data is unavailable.
        cfg = _write_project_config(tmp_path, _MINIMAL_CONFIG)
        original = cfg.read_bytes()
        prompt = _FakePrompt(confirms=[True])
        runtime = _runtime_observed(tmp_path, models=None)
        request = _request(
            tmp_path, prompt=prompt, runtime=runtime,
            selection=_selection(), api_key=_CANARY, custom=_custom(),
        )
        # When
        result = configure_opencode(request)
        # Then: fail closed before transaction; exact bytes remain.
        assert result.committed is False
        assert "MODEL_DATA_UNAVAILABLE" in {f.kind for f in result.facts}
        assert "MODEL_VALIDATED" not in {f.kind for f in result.facts}
        assert cfg.read_bytes() == original
        assert not (tmp_path / ".seam-init").exists()


# --- secret hygiene: zero test-key characters ----------------------------


class TestSecretHygiene:
    def test_stdout_state_and_facts_have_zero_canary(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        _ = _write_project_config(tmp_path, _MINIMAL_CONFIG)
        prompt = _FakePrompt(confirms=[True])
        runtime = _runtime_observed(tmp_path, models=("myco/my-model",))
        request = _request(
            tmp_path, prompt=prompt, runtime=runtime,
            selection=_selection(), api_key=_CANARY, custom=_custom(),
        )
        result = configure_opencode(request)
        out = capsys.readouterr().out
        assert result.committed is True
        assert _CANARY not in out
        state_path = tmp_path / ".seam-init" / "state.json"
        if state_path.is_file():
            assert _CANARY not in state_path.read_text("utf-8")
        report = "".join(str(f.detail) for f in result.facts) + str(result.safe_detail)
        assert _CANARY not in report

    def test_second_commit_is_idempotent(self, tmp_path: Path) -> None:
        _ = _write_project_config(tmp_path, _MINIMAL_CONFIG)
        runtime = _runtime_observed(tmp_path, models=("myco/my-model",))
        first = configure_opencode(_request(
            tmp_path, prompt=_FakePrompt(confirms=[True]), runtime=runtime,
            selection=_selection(), api_key=_CANARY, custom=_custom(),
        ))
        assert first.committed is True
        first_bytes = (tmp_path / ".opencode/opencode.jsonc").read_bytes()
        second = configure_opencode(_request(
            tmp_path, prompt=_FakePrompt(confirms=[True]), runtime=runtime,
            selection=_selection(), api_key=_CANARY, custom=_custom(),
        ))
        second_bytes = (tmp_path / ".opencode/opencode.jsonc").read_bytes()
        assert second.committed is True
        assert second_bytes == first_bytes
