"""In-process unit tests for the SEAM initializer CLI.

Class style mirroring the repo convention. Every test uses an explicit
Given/When/Then block. Covers argparse shape, the prompt-port boundary, the
non-interactive stdin-free contract, and main() exit-code taxonomy.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
from pathlib import Path

import pytest

from seam_init.answers import Answers, AnswersLoadError, load_answers
from seam_init.cli import (
    InteractivePort,
    NonInteractivePort,
    NonInteractivePromptReached,
    PromptPort,
    build_parser,
    main,
    parse_args,
)
from seam_init.omo_install import SubscriptionSelection, TriState
from seam_init.subscription_map import FamilySubscriptionSelector, select_subscription

_PROJECT_SEAM_INIT = Path(__file__).resolve().parent.parent.parent / ".seam-init"


@pytest.fixture(autouse=True)
def _assert_no_repo_seam_init_artifact():
    # Given: the repository must not carry a .seam-init directory before tests
    assert not _PROJECT_SEAM_INIT.exists(), f".seam-init exists before test: {_PROJECT_SEAM_INIT}"
    yield
    # Then: no CLI test may create .seam-init (or any report) in the repository
    assert not _PROJECT_SEAM_INIT.exists(), f".seam-init created during test: {_PROJECT_SEAM_INIT}"


def _all_default() -> SubscriptionSelection:
    return SubscriptionSelection(
        claude=TriState.NO, openai=False, gemini=False, copilot=False,
        opencode_zen=False, zai_coding_plan=False, opencode_go=False,
        kimi_for_coding=False, bailian_coding_plan=False,
        minimax_cn_coding_plan=False, minimax_coding_plan=False,
        vercel_ai_gateway=False)


def _only(**flags: object) -> SubscriptionSelection:
    return dataclasses.replace(_all_default(), **flags)


class _RecordingPort:
    """Prompt port double that records every call and never reads stdin."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, object]] = []

    def ask(self, prompt: str, *, default: str | None = None) -> str:
        self.calls.append(("ask", prompt, default))
        return "recorded-provider"

    def secret(self, prompt: str) -> str:
        self.calls.append(("secret", prompt, None))
        return "recorded-secret"

    def confirm(self, prompt: str, *, default: bool = False) -> bool:
        self.calls.append(("confirm", prompt, default))
        return False


class _EOFPort:
    """Prompt port double that simulates Ctrl-D (EOF) on every prompt."""

    def ask(self, prompt: str, *, default: str | None = None) -> str:
        raise EOFError()

    def secret(self, prompt: str) -> str:
        raise EOFError()

    def confirm(self, prompt: str, *, default: bool = False) -> bool:
        raise EOFError()


class TestBuildParser:
    def test_parser_advertises_non_interactive_and_answers(self) -> None:
        # Given
        parser = build_parser()
        # When
        actions = {a.dest for a in parser._actions}
        # Then
        assert "non_interactive" in actions
        assert "answers" in actions

    def test_parser_has_help_action(self) -> None:
        # Given
        parser = build_parser()
        # When
        option_strings = {
            tok for a in parser._actions for tok in a.option_strings
        }
        # Then
        assert "-h" in option_strings
        assert "--help" in option_strings

    def test_parser_returns_argument_parser(self) -> None:
        # Given / When
        parser = build_parser()
        # Then
        assert isinstance(parser, argparse.ArgumentParser)


class TestParseArgs:
    def test_help_request_raises_system_exit_zero(self) -> None:
        # Given / When / Then
        with pytest.raises(SystemExit) as exc:
            _ = parse_args(["--help"])
        assert exc.value.code == 0

    def test_non_interactive_flag_is_stored_true(self) -> None:
        # Given / When
        args = parse_args(["--non-interactive", "--answers", "a.json"])
        # Then
        assert args.non_interactive is True

    def test_answers_is_typed_as_path(self) -> None:
        # Given / When
        args = parse_args(["--non-interactive", "--answers", "a.json"])
        # Then
        assert isinstance(args.answers, Path)
        assert args.answers.name == "a.json"

    def test_no_args_yields_non_interactive_false(self) -> None:
        # Given / When
        args = parse_args([])
        # Then
        assert args.non_interactive is False
        assert args.answers is None


class TestMain:
    def test_help_returns_zero(self) -> None:
        # Given / When
        code = main(["--help"])
        # Then
        assert code == 0

    def test_non_interactive_without_answers_returns_usage_error(self) -> None:
        # Given / When
        code = main(["--non-interactive"])
        # Then: argparse-style usage error code (2), not a hang.
        assert code == 2

    def test_non_interactive_with_missing_answers_file_returns_usage_error(
        self,
        tmp_path: Path,
    ) -> None:
        # Given
        missing = tmp_path / "nope.json"
        # When
        code = main(["--non-interactive", "--answers", str(missing)])
        # Then: typed AnswersLoadError -> usage error (2), no hang.
        assert code == 2

    def test_non_interactive_with_valid_answers_returns_int_in_range(
        self,
        tmp_path: Path,
    ) -> None:
        # Given
        answers = tmp_path / "answers.json"
        _ = answers.write_text('{"provider_id": "openai"}', encoding="utf-8")
        # When: workflow_runner injected to return PENDING_AUTH (60).
        code = main(
            ["--non-interactive", "--answers", str(answers)],
            workflow_runner=lambda p, a: 60,
        )
        # Then
        assert code in {0, 60, *range(61, 70)}

    def test_non_interactive_never_calls_prompt_port(self, tmp_path: Path) -> None:
        # Given
        answers = tmp_path / "answers.json"
        _ = answers.write_text("{}", encoding="utf-8")
        recorder = _RecordingPort()
        # When: runner receives the port but must not use it for prompting.
        seen_port: list[object] = []
        def _runner(prompt, answers):
            seen_port.append(prompt)
            return 60
        code = main(
            ["--non-interactive", "--answers", str(answers)],
            prompt_port=recorder,
            workflow_runner=_runner,
        )
        # Then: the recorder was never consulted; runner got the port but didn't call it.
        assert recorder.calls == []
        assert code == 60
        assert len(seen_port) == 1

    def test_interactive_passes_prompt_to_runner(self) -> None:
        # Given: the CLI dispatches to the workflow runner with the prompt.
        seen: list[tuple[PromptPort, Answers | None]] = []
        def _runner(prompt: PromptPort, answers: Answers | None) -> int:
            seen.append((prompt, answers))
            return 0
        # When
        code = main([], prompt_port=_RecordingPort(), workflow_runner=_runner)
        # Then: the runner received the prompt and None answers (interactive).
        assert code == 0
        assert len(seen) == 1
        assert seen[0][1] is None

    def test_interactive_runner_eof_propagates(self) -> None:
        # Given: a runner that raises EOFError via the prompt port.
        # When: the CLI does NOT swallow EOFError (workflow handles it internally).
        def _eof_runner(prompt: PromptPort, answers: Answers | None) -> int:
            prompt.ask("anything")  # _EOFPort raises EOFError
            return 0
        with pytest.raises(EOFError):
            main([], prompt_port=_EOFPort(), workflow_runner=_eof_runner)

    def test_inline_secret_in_answers_file_returns_usage_error(
        self,
        tmp_path: Path,
    ) -> None:
        # Given: an inline secret VALUE (not an env-var name) must be rejected.
        answers = tmp_path / "answers.json"
        _ = answers.write_text('{"api_key": "sk-leaked"}', encoding="utf-8")
        # When
        code = main(["--non-interactive", "--answers", str(answers)])
        # Then: rejected before any stage runs; usage error (2).
        assert code == 2


class TestNonInteractivePort:
    def test_ask_raises_non_interactive_prompt_reached(self) -> None:
        # Given
        port = NonInteractivePort()
        # When / Then
        with pytest.raises(NonInteractivePromptReached):
            _ = port.ask("anything")

    def test_secret_raises_non_interactive_prompt_reached(self) -> None:
        # Given
        port = NonInteractivePort()
        # When / Then
        with pytest.raises(NonInteractivePromptReached):
            _ = port.secret("anything")

    def test_confirm_raises_non_interactive_prompt_reached(self) -> None:
        # Given
        port = NonInteractivePort()
        # When / Then
        with pytest.raises(NonInteractivePromptReached):
            _ = port.confirm("anything")


class TestPromptPortProtocol:
    def test_interactive_port_satisfies_protocol(self) -> None:
        # Given / When / Then
        assert isinstance(InteractivePort(), PromptPort)

    def test_non_interactive_port_satisfies_protocol(self) -> None:
        # Given / When / Then
        assert isinstance(NonInteractivePort(), PromptPort)

    def test_recording_port_satisfies_protocol(self) -> None:
        # Given / When / Then
        assert isinstance(_RecordingPort(), PromptPort)


class TestAnswersLoadErrorContract:
    def test_main_returns_usage_error_for_non_object_root(self, tmp_path: Path) -> None:
        # Given: a JSON array root is not a valid answers object.
        bad = tmp_path / "a.json"
        _ = bad.write_text("[1, 2, 3]", encoding="utf-8")
        # When: main() catches AnswersLoadError internally and returns 2.
        code = main(["--non-interactive", "--answers", str(bad)])
        # Then
        assert code == 2

    def test_load_answers_directly_raises_for_non_object_root(
        self,
        tmp_path: Path,
    ) -> None:
        # Given
        bad = tmp_path / "a.json"
        _ = bad.write_text("[1, 2, 3]", encoding="utf-8")
        # When / Then: the loader boundary raises the typed error.
        with pytest.raises(AnswersLoadError):
            _ = load_answers(bad)

    def test_load_answers_directly_raises_for_inline_secret(
        self,
        tmp_path: Path,
    ) -> None:
        # Given: an inline api_key VALUE must be rejected at the boundary.
        bad = tmp_path / "a.json"
        _ = bad.write_text('{"api_key": "sk-leaked"}', encoding="utf-8")
        # When / Then
        with pytest.raises(AnswersLoadError):
            _ = load_answers(bad)


class TestBillableConsentParsing:
    @pytest.mark.parametrize("raw", ["false", "true", "yes", "no", "1", "0", "on"])
    def test_string_consent_values_are_declined(self, tmp_path: Path, raw: str) -> None:
        # Given: a JSON string (not a JSON boolean) consent value
        path = tmp_path / "a.json"
        _ = path.write_text(json.dumps({"billable_consent": raw}), encoding="utf-8")
        # When / Then: never accepted as consent
        assert load_answers(path).billable_consent is False

    @pytest.mark.parametrize("raw", [0, 1, -1, 0.5])
    def test_numeric_consent_values_are_declined(self, tmp_path: Path, raw: float) -> None:
        # Given: a JSON number consent value
        path = tmp_path / "a.json"
        _ = path.write_text(json.dumps({"billable_consent": raw}), encoding="utf-8")
        # When / Then
        assert load_answers(path).billable_consent is False

    def test_list_and_object_consent_values_are_declined(self, tmp_path: Path) -> None:
        # Given: JSON array/object consent values
        for raw in ([True], {"value": True}):
            path = tmp_path / "a.json"
            _ = path.write_text(json.dumps({"billable_consent": raw}), encoding="utf-8")
            # When / Then
            assert load_answers(path).billable_consent is False

    def test_missing_and_null_consent_are_declined(self, tmp_path: Path) -> None:
        # Given: no consent key at all, and an explicit JSON null
        missing = tmp_path / "missing.json"
        _ = missing.write_text("{}", encoding="utf-8")
        null = tmp_path / "null.json"
        _ = null.write_text('{"billable_consent": null}', encoding="utf-8")
        # When / Then
        assert load_answers(missing).billable_consent is False
        assert load_answers(null).billable_consent is False

    def test_boolean_consent_round_trips(self, tmp_path: Path) -> None:
        # Given: genuine JSON booleans
        yes = tmp_path / "yes.json"
        _ = yes.write_text('{"billable_consent": true}', encoding="utf-8")
        no = tmp_path / "no.json"
        _ = no.write_text('{"billable_consent": false}', encoding="utf-8")
        # When / Then: the only values that map faithfully
        assert load_answers(yes).billable_consent is True
        assert load_answers(no).billable_consent is False


class TestSubscriptionMapping:
    @pytest.mark.parametrize("provider_id,expected", [
        ("openai", _only(openai=True)),
        ("anthropic", _only(claude=TriState.YES)),
        ("claude", _only(claude=TriState.YES)),
        ("gemini", _only(gemini=True)),
        ("google", _only(gemini=True)),
        ("copilot", _only(copilot=True)),
        ("github-copilot", _only(copilot=True)),
        ("opencode-zen", _only(opencode_zen=True)),
        ("opencode-go", _only(opencode_go=True)),
        ("zai", _only(zai_coding_plan=True)),
        ("kimi", _only(kimi_for_coding=True)),
        ("moonshot", _only(kimi_for_coding=True)),
        ("bailian", _only(bailian_coding_plan=True)),
        ("minimax-cn", _only(minimax_cn_coding_plan=True)),
        ("minimax", _only(minimax_coding_plan=True)),
        ("vercel", _only(vercel_ai_gateway=True)),
        ("my-custom-proxy", _all_default()),
        ("unknown-thing", _all_default()),
        ("", _all_default()),
        ("openai-compatible", _all_default()),
    ])
    def test_provider_family_flag_set(
        self, provider_id: str, expected: SubscriptionSelection,
    ) -> None:
        # Given / When
        produced = select_subscription(provider_id)
        # Then: the exact, deterministic flag set for the provider family
        assert produced == expected

    def test_mapping_is_deterministic_across_calls(self) -> None:
        # Given / When
        first = select_subscription("Anthropic")
        second = select_subscription("anthropic")
        # Then: case-insensitive normalization, identical results
        assert first == second == _only(claude=TriState.YES)

    def test_selector_port_satisfies_protocol(self) -> None:
        # Given / When
        selector = FamilySubscriptionSelector()
        # Then: the port produces the same mapping as the function
        assert selector.select("vercel") == _only(vercel_ai_gateway=True)
        assert selector.select("custom") == _all_default()
