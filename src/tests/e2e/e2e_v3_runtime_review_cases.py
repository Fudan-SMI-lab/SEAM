from __future__ import annotations

from pathlib import Path

import pytest

import harness.session.manager as manager_module
from core.phase5_attempt_receipt import load_attempt_receipt

from .e2e_v3_runtime_fakes import SessionScript
from .e2e_v3_runtime_fixture import RuntimeScenario, read_json, run_runtime_scenario


def test_v3_runtime_accepts_review_and_publishes_authoritative_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given one successful real validation and one explicit review acceptance.
    scenario = RuntimeScenario(
        run_hex="a" * 32,
        review_responses=('{"verdict":"accept","reasoning":"approved"}',),
        review_enabled=True,
    )

    # When the public V3 coordinator executes its complete lifecycle.
    result = run_runtime_scenario(tmp_path, monkeypatch, scenario)

    # Then frozen authority, reports, and the actual accepted receipt agree.
    summary = read_json(result.report_dir / "summary.json")
    assert result.exit_code == 0
    assert summary["overall_status"] == "PASS"
    assert result.manager.message_post_count == 1
    assert result.manager.remaining_responses == ()
    receipts = tuple(
        result.report_dir.glob(".sm-artifacts/*/shell_attempts/*.receipt.json")
    )
    assert len(receipts) == 1
    receipt = load_attempt_receipt(receipts[0])
    assert receipt.accepted is True
    assert receipt.complete is True
    assert Path(receipt.artifacts.stdout.path).is_file()
    assert not (result.report_dir / "traceback.txt").exists()


def test_v3_runtime_reject_then_accept_continues_with_exact_review_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a rejection, one routed improvement, and a later acceptance.
    responses = (
        '{"verdict":"reject","reasoning":"fix once"}',
        '{"repair_role":"code_adapter"}',
        '{"fixed":true}',
        '{"verdict":"accept","reasoning":"fixed"}',
    )
    scenario = RuntimeScenario(
        run_hex="b" * 32,
        review_responses=responses,
        review_enabled=True,
    )

    # When the public V3 lifecycle resumes the same logical review gate.
    result = run_runtime_scenario(tmp_path, monkeypatch, scenario)

    # Then the second real shell receipt is accepted and no extra review occurs.
    summary = read_json(result.report_dir / "summary.json")
    assert result.exit_code == 0
    assert summary["overall_status"] == "PASS"
    assert result.manager.message_post_count == 4
    assert result.manager.remaining_responses == ()
    receipts = sorted(
        result.report_dir.glob(".sm-artifacts/*/shell_attempts/*.receipt.json")
    )
    assert [load_attempt_receipt(path).accepted for path in receipts] == [False, True]
    assert not (result.report_dir / "traceback.txt").exists()


@pytest.mark.parametrize(
    ("fail_closed", "expected_exit", "expected_status"),
    [(True, 1, "FAIL"), (False, 0, "PASS")],
)
def test_v3_runtime_review_exhaustion_respects_strict_and_compatibility(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fail_closed: bool,
    expected_exit: int,
    expected_status: str,
) -> None:
    # Given one allowed review round whose explicit judgment is rejection.
    run_hex = ("c" if fail_closed else "d") * 32
    scenario = RuntimeScenario(
        run_hex=run_hex,
        review_responses=('{"verdict":"reject","reasoning":"exhausted"}',),
        review_enabled=True,
        review_fail_closed=fail_closed,
        review_limit=1,
    )

    # When the actual V3 authority maps the exhausted review gate.
    result = run_runtime_scenario(tmp_path, monkeypatch, scenario)

    # Then policy alone changes PASS/FAIL and a second judgment is prohibited.
    summary = read_json(result.report_dir / "summary.json")
    assert result.exit_code == expected_exit
    assert summary["overall_status"] == expected_status
    assert result.manager.message_post_count == 1
    assert result.manager.remaining_responses == ()
    assert not (result.report_dir / "traceback.txt").exists()


def test_v3_runtime_post_acceptance_timeout_logs_attempt_without_repost(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given an accepted POST whose session never converges to idle.
    clock = {"now": 0.0}

    def advance_time() -> float:
        clock["now"] += 1000.0
        return clock["now"]

    monkeypatch.setattr(manager_module.time, "time", advance_time)
    monkeypatch.setattr(manager_module.time, "sleep", lambda _seconds: None)
    scenario = RuntimeScenario(
        run_hex="e" * 32,
        review_responses=('{"verdict":"accept","reasoning":"posted"}',),
        review_enabled=True,
        session_script=SessionScript(
            ('{"verdict":"accept","reasoning":"posted"}',),
            remain_running=True,
        ),
    )

    # When the full harness reaches review transport timeout handling.
    result = run_runtime_scenario(tmp_path, monkeypatch, scenario)

    # Then attempt 1/3 is logged once and no retry POST or PASS is possible.
    observability = (result.report_dir / "phase_observability.json").read_text(
        encoding="utf-8"
    )
    assert result.exit_code == 1
    assert result.manager.message_post_count == 1
    assert '"attempt": 1' in observability
    assert '"max_attempts": 3' in observability
    assert '"retry_decision": "no_repost"' in observability
    assert '"reason": "post_acceptance_timeout"' in observability
    assert "posted" not in observability
