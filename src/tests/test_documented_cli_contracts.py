from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

from seam_init.models import ExitCode, FailureKind
from seam_init.reporting import READY_COMMAND

from core.review_policy import (
    ReviewDefaults,
    ReviewPolicyInputs,
    resolve_review_policy,
    review_cli_overrides_from_namespace,
)
from core.run_outcome import ReviewOutcome, TerminalOutcome
from tests.documented_cli_contract_cases import (
    test_documented_artifact_tree_uses_writer_names as test_documented_artifact_tree_uses_writer_names,
    test_documented_trace_boundaries_are_truthful as test_documented_trace_boundaries_are_truthful,
    test_generic_cpu_docker_contract_never_pulls as test_generic_cpu_docker_contract_never_pulls,
    test_optional_generic_cpu_docker as test_optional_generic_cpu_docker,
    test_optional_real_opencode_phase_0_to_3 as test_optional_real_opencode_phase_0_to_3,
)
from tests.documented_cli_contract_support import (
    DOCUMENTS,
    FLAG_ROW,
    FLAG_TABLE,
    ROOT,
    ParsedCli,
    documented_examples,
    review_outcome,
)
from tests.e2e import e2e_test_v3 as target


def test_documented_python_examples_parse_with_real_v3_parser() -> None:
    parser = target.build_parser()
    names: set[str] = set()

    for path in DOCUMENTS:
        for example in documented_examples(path):
            parsed = ParsedCli.model_validate(vars(parser.parse_args(example.argv)))
            names.add(example.name)
            assert (parsed.project_dir is None) != (parsed.continue_from is None)
            assert (parsed.continue_from is not None) is (
                example.name == "e2e-continuation"
            )
            assert parsed.save_agent_trace is (
                True
                if example.name == "trace-direct"
                else False
                if example.name == "e2e-continuation"
                else None
            )
            assert (parsed.workflow_path is not None) is (
                example.name != "e2e-continuation"
            )
            assert parsed.container_retention == "retain"
            assert parsed.review_gate is (
                example.name in {"readme-zh-direct", "readme-en-direct", "e2e-direct"}
            )
            if example.name in {
                "readme-zh-direct",
                "readme-en-direct",
                "e2e-direct",
            }:
                assert parsed.server_url == "http://127.0.0.1:4098"
            if example.name == "e2e-direct":
                assert parsed.max_phase5_iter == 5
                assert parsed.max_review_iter == [3]
                assert parsed.review_fail_closed is True
                assert parsed.keep_temp_dir is True
                assert parsed.opencode_readiness == "message"
                assert parsed.opencode_message_timeout == 120

    assert names == {
        "readme-zh-direct",
        "readme-en-direct",
        "e2e-direct",
        "e2e-continuation",
        "trace-direct",
    }


def test_e2e_flag_table_matches_every_public_python_option() -> None:
    parser = target.build_parser()
    public_flags = {
        option
        for action in parser._actions
        for option in action.option_strings
        if option.startswith("--") and option != "--help"
    }
    guide = (ROOT / "src" / "docs" / "E2E_TESTING.md").read_text(encoding="utf-8")
    table = FLAG_TABLE.search(guide)
    assert table is not None
    documented_flags = set(FLAG_ROW.findall(table.group("body")))

    assert documented_flags == public_flags


def test_documented_parser_defaults_and_review_resolution_are_exact() -> None:
    parser = target.build_parser()
    parsed = ParsedCli.model_validate(
        vars(parser.parse_args(["--project-dir", "/tmp/cuda-project"]))
    )
    continuation = ParsedCli.model_validate(
        vars(parser.parse_args(["--continue-from", "/reports/parent/summary.json"]))
    )
    overrides = review_cli_overrides_from_namespace(parsed, parser)
    policy = resolve_review_policy(
        ReviewPolicyInputs(
            cli=overrides,
            workflow=ReviewDefaults(None, None),
            framework=ReviewDefaults(None, None),
        )
    )

    assert parsed.server_url is None
    assert parsed.continue_from is None
    assert continuation.project_dir is None
    assert parsed.max_phase5_iter == 5
    assert parsed.max_review_iter is None
    assert parsed.review_fail_closed is None
    assert parsed.container_retention == "retain"
    assert parsed.save_agent_trace is None
    assert parsed.keep_temp_dir is False
    assert parsed.review_gate is False
    assert parsed.agent is None
    assert parsed.output_dir is None
    assert parsed.user_constraints is None
    assert parsed.framework_config is None
    assert parsed.verbose is False
    assert parsed.workflow_path is None
    assert parsed.server_auto_start is True
    assert parsed.server_no_auto_start is False
    assert parsed.server_port == 0
    assert parsed.opencode_readiness == "message"
    assert parsed.opencode_message_timeout == 120
    assert int(policy.max_iterations) == 3
    assert policy.fail_closed is True


def test_documented_parser_conflicts_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parser = target.build_parser()
    conflict_cases = (
        ["--project-dir", ".", "--continue-from", "summary.json"],
        ["--project-dir", ".", "--save-agent-trace", "--no-save-agent-trace"],
        ["--project-dir", ".", "--review-fail-closed", "--no-review-fail-closed"],
        [
            "--project-dir",
            ".",
            "--container-retention",
            "retain",
            "--container-retention",
            "delete",
        ],
    )
    for argv in conflict_cases:
        with pytest.raises(SystemExit) as raised:
            _ = parser.parse_args(argv)
        assert raised.value.code == 2

    duplicate_review = ParsedCli.model_validate(
        vars(
            parser.parse_args(
                [
                    "--project-dir",
                    ".",
                    "--max-review-iter",
                    "2",
                    "--max-review-iter",
                    "3",
                ]
            )
        )
    )
    with pytest.raises(SystemExit) as duplicate:
        _ = review_cli_overrides_from_namespace(duplicate_review, parser)
    assert duplicate.value.code == 2

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "e2e_test_v3",
            "--continue-from",
            "/reports/parent/summary.json",
            "--workflow-path",
            "workflow.yaml",
        ],
    )
    with pytest.raises(SystemExit) as workflow_conflict:
        _ = target.main()
    assert workflow_conflict.value.code == 2


@pytest.mark.parametrize(
    ("validation", "review", "strict", "expected"),
    (
        (True, ReviewOutcome.DISABLED, True, TerminalOutcome.PASSED),
        (True, ReviewOutcome.ACCEPTED, True, TerminalOutcome.PASSED),
        (True, ReviewOutcome.REJECT_EXHAUSTED, True, TerminalOutcome.FAILED),
        (
            True,
            ReviewOutcome.REJECT_EXHAUSTED,
            False,
            TerminalOutcome.PASSED_WITH_REVIEWS,
        ),
        (True, ReviewOutcome.UNKNOWN, False, TerminalOutcome.FAILED),
        (True, ReviewOutcome.SESSION_ERROR, False, TerminalOutcome.FAILED),
        (True, ReviewOutcome.IMPROVEMENT_ERROR, False, TerminalOutcome.FAILED),
        (False, ReviewOutcome.DISABLED, False, TerminalOutcome.FAILED),
    ),
)
def test_documented_review_matrix_uses_run_outcome_authority(
    validation: bool,
    review: ReviewOutcome,
    strict: bool,
    expected: TerminalOutcome,
) -> None:
    assert (
        review_outcome(validation=validation, review=review, strict=strict) is expected
    )


def test_parser_rejects_seal_manifest_with_continue_from_in_either_order() -> None:
    # Given the public V3 parser.
    parser = target.build_parser()

    # When --seal-manifest precedes or follows --continue-from.
    # Then parse_args exits with the argparse conflict code (2) for both orders.
    for argv in (
        ["--continue-from", "summary.json", "--seal-manifest"],
        ["--seal-manifest", "--continue-from", "summary.json"],
    ):
        with pytest.raises(SystemExit) as raised:
            _ = parser.parse_args(argv)
        assert raised.value.code == 2


_STALE_DIRECT_RUN_CLAIMS = (
    "Ordinary direct runs currently do not create the sealed root run manifest",
    "当前普通直接运行尚未生成 continuation 所需的 sealed root run manifest",
    "普通 direct V3 会写 resource manifest 和 summary，但不会创建",
)


def test_guides_do_not_claim_direct_runs_never_generate_root_manifests() -> None:
    # Given every public guide that has historically described continuation.
    guides = (
        ROOT / "README.md",
        ROOT / "README.en.md",
        ROOT / "src" / "docs" / "E2E_TESTING.md",
    )

    # When each guide is inspected for the stale "direct runs never seal" claim.
    # Then none of the guides retain the disproved limitation wording.
    for path in guides:
        text = path.read_text(encoding="utf-8")
        for stale in _STALE_DIRECT_RUN_CLAIMS:
            assert stale not in text, f"stale claim {stale!r} still in {path}"


def test_e2e_guide_documents_direct_sealing_model() -> None:
    # Given the authoritative E2E guide.
    guide = (ROOT / "src" / "docs" / "E2E_TESTING.md").read_text(encoding="utf-8")

    # Then it documents manifest-sealing.v1.json as opt-in, outcome-neutral,
    # sidecar/summary-projected observability, with success-only eligibility.
    assert "manifest-sealing.v1.json" in guide
    for status in ("not_requested", "succeeded", "failed"):
        assert status in guide
    assert "continuation_eligible" in guide
    assert "opt-in" in guide or "opt in" in guide
    assert "outcome-neutral" in guide or "outcome neutral" in guide
    assert "summary.json" in guide
    # Sealing failure must not change migration PASS/FAIL or the exit code.
    assert "does not change" in guide or "不改写" in guide or "不改变" in guide


_ALL_CONTINUATION_GUIDES = (
    ROOT / "README.md",
    ROOT / "README.en.md",
    ROOT / "README.zh.md",
    ROOT / "docs" / "User_Guide.md",
    ROOT / "src" / "README.md",
    ROOT / "src" / "docs" / "E2E_TESTING.md",
)


def test_all_guides_document_exact_environment_id_plus_namespace_authority() -> None:
    # Given every public guide that describes continuation environment binding.
    # Then each guide requires an exact environment_id plus a matching namespace,
    # explicitly states that namespace alone / list order / fact count are not
    # authority, and fails closed on missing or ambiguous references.
    for path in _ALL_CONTINUATION_GUIDES:
        text = path.read_text(encoding="utf-8")
        assert "environment_id" in text, (
            f"{path.name} must document the exact environment_id requirement"
        )
        assert "namespace" in text
        assert (
            "namespace 单独不是 authority" in text
            or "namespace alone is never authority" in text
        ), f"{path.name} must state namespace alone is not authority"
        assert "list-order" in text or "list order" in text
        assert "fact-count" in text or "fact count" in text
        assert "fail closed" in text or "fail-closed" in text
        assert "namespace-only" not in text, (
            f"{path.name} retains the disproved namespace-only authority claim"
        )


def test_e2e_guide_documents_optional_sqlite_and_typing_extensions_floor() -> None:
    # Given the authoritative E2E guide.
    guide = (ROOT / "src" / "docs" / "E2E_TESTING.md").read_text(encoding="utf-8")

    # Then it documents the optional [sqlite] extra and base typing_extensions,
    # and keeps the Linux / Python 3.10+ production floor without 3.8/3.9 claims.
    assert "[sqlite]" in guide
    assert "pysqlite3-binary" in guide
    assert "typing_extensions" in guide
    assert "3.10" in guide
    assert "3.8" not in guide
    assert "3.9" not in guide


def test_readmes_keep_linux_python_3_10_floor() -> None:
    # Given the public READMEs.
    for path in (ROOT / "README.md", ROOT / "README.en.md", ROOT / "docs" / "User_Guide.md"):
        text = path.read_text(encoding="utf-8")
        # Then the production floor is Linux / Python 3.10+ with no 3.8/3.9 claim.
        assert "3.10" in text
        assert "Linux" in text
        assert "3.8" not in text
        assert "3.9" not in text


_INITIALIZER_DOCS = (
    ROOT / "README.md",
    ROOT / "README.en.md",
    ROOT / "README.zh.md",
    ROOT / "docs" / "User_Guide.md",
)
_FENCED_BASH = re.compile(r"```bash\s*(?P<body>.*?)```", re.DOTALL)
_NOT_RUNNABLE_PHRASES = ("not runnable-ready", "NOT runnable-ready", "不可直接运行")
_OPTIONAL_READY_FLAGS = ("--dashboard", "--review", "--seal-manifest")


def _documented_script_commands(path: Path) -> list[str]:
    commands: list[str] = []
    for block in _FENCED_BASH.finditer(path.read_text(encoding="utf-8")):
        for raw_line in block.group("body").splitlines():
            line = raw_line.strip().rstrip("\\").strip()
            if line and not line.startswith("#") and "src/scripts/" in line:
                commands.append(line)
    return commands


def _assert_status_row(text: str, path: Path, status: str, exit_text: str) -> None:
    assert any(status in line and exit_text in line for line in text.splitlines()), (
        f"{path.name} must document {status} with exit {exit_text}"
    )


def test_every_quickstart_leads_with_init_seam() -> None:
    # Given every public doc that carries a quickstart.
    # When its fenced bash blocks are parsed in order.
    # Then the first src/scripts command is the interactive initializer.
    for path in _INITIALIZER_DOCS:
        commands = _documented_script_commands(path)
        assert commands, f"{path.name} documents no src/scripts command"
        assert commands[0] == "bash src/scripts/init_seam.sh", (
            f"{path.name} quickstart must lead with "
            f"'bash src/scripts/init_seam.sh', got {commands[0]!r}"
        )


def test_initializer_status_table_matches_production_exit_codes() -> None:
    # Given the production status/exit-code authority in seam_init.models.
    failed_range = f"{int(min(FailureKind))}-{int(max(FailureKind))}"
    # When each public doc is inspected.
    # Then READY/PENDING_AUTH/FAILED are documented against the same codes.
    for path in _INITIALIZER_DOCS:
        text = path.read_text(encoding="utf-8")
        _assert_status_row(text, path, "READY", str(int(ExitCode.READY)))
        _assert_status_row(text, path, "PENDING_AUTH", str(int(ExitCode.PENDING_AUTH)))
        _assert_status_row(text, path, "FAILED", failed_range)


def test_ready_handoff_matches_production_command() -> None:
    # Given the production READY handoff constant.
    # When each public doc is inspected.
    # Then the exact command is shown, /path/to/project is explained, and the
    # optional flags documented there are accepted by the real run script.
    script = (ROOT / "src" / "scripts" / "run_seam.sh").read_text(encoding="utf-8")
    for flag in _OPTIONAL_READY_FLAGS:
        assert flag in script, f"run_seam.sh no longer accepts {flag}"
    for path in _INITIALIZER_DOCS:
        text = path.read_text(encoding="utf-8")
        assert READY_COMMAND in text, (
            f"{path.name} must show the exact READY handoff command"
        )
        assert text.count("/path/to/project") >= 2, (
            f"{path.name} must explain replacing /path/to/project"
        )
        handoff = text.index(READY_COMMAND)
        for flag in _OPTIONAL_READY_FLAGS:
            assert flag in text[handoff:], (
                f"{path.name} must document {flag} alongside the READY handoff"
            )


def test_pending_auth_is_documented_as_not_runnable_ready() -> None:
    # Given PENDING_AUTH defers authentication/consent in production.
    # When each public doc is inspected.
    # Then no doc presents PENDING_AUTH as runnable-ready and each requires
    # authenticating, consenting, and rerunning the initializer.
    for path in _INITIALIZER_DOCS:
        text = path.read_text(encoding="utf-8")
        assert any(phrase in text for phrase in _NOT_RUNNABLE_PHRASES), (
            f"{path.name} must state PENDING_AUTH is not runnable-ready"
        )
        assert text.count("bash src/scripts/init_seam.sh") >= 2, (
            f"{path.name} must document rerunning the initializer after PENDING_AUTH"
        )


def test_initializer_docs_avoid_forbidden_platform_claims() -> None:
    # Given the MVP contract: Linux-only, no mandatory Node, no global config mode.
    for path in _INITIALIZER_DOCS:
        text = path.read_text(encoding="utf-8")
        assert "Windows" not in text, f"{path.name} must not claim Windows support"
        assert "Node" not in text, f"{path.name} must not claim mandatory Node"
        assert "global config" not in text, (
            f"{path.name} must not claim a global config mode"
        )
