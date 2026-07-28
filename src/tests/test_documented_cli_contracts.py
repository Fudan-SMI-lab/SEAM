from __future__ import annotations

import sys

import pytest

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
