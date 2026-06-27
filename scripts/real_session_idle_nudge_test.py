#!/usr/bin/env python3
"""Real OpenCode integration test for idle-refetch + TODO nudge.

Runs against a LIVE OpenCode server (not mocks). Standalone, not part of the
pytest suite. Usage:

    NO_PROXY=127.0.0.1,localhost,::1 no_proxy=127.0.0.1,localhost,::1 \
        PYTHONPATH=src python3 scripts/real_session_idle_nudge_test.py \
        --server-url http://127.0.0.1:4097 [--agent sisyphus]

Exit code 0 = all checks passed.
"""
from __future__ import annotations

import argparse
import sys
import time

from harness.session.manager import MigrationSessionManager, IdleOutcome


def _new_manager(server_url: str, agent: str | None) -> MigrationSessionManager:
    mgr = MigrationSessionManager(base_url=server_url)
    if agent:
        mgr.override_agent(agent)
    return mgr


def check_basic_idle_refetch(mgr: MigrationSessionManager) -> bool:
    """Target 1: a normal request returns a non-empty final answer, and that
    answer equals the latest message in history after idle (i.e. refetch is
    consistent with server state)."""
    print("\n[CHECK 1] basic idle refetch")
    sid = mgr.create_session(role="itest_refetch", lifecycle="ephemeral",
                             title="itest-refetch")
    try:
        reply = mgr.send_command(
            sid,
            "Reply with exactly this single line and nothing else: REFETCH_MARKER_OK",
            timeout=180,
        )
        print(f"  returned text: {reply!r}")
        latest = mgr._last_message_text_tolerant(sid)
        print(f"  latest history text head: {latest[:120]!r}")
        ok_marker = "REFETCH_MARKER_OK" in reply
        # After idle, the returned text should be the latest assistant message.
        consistent = reply.strip() == latest.strip() or reply.strip() in latest
        print(f"  marker_present={ok_marker} consistent_with_history={consistent}")
        return bool(ok_marker and consistent)
    finally:
        mgr.cleanup_session(sid)


def check_await_idle_state(mgr: MigrationSessionManager) -> bool:
    """Target 1 infra: _await_idle_state returns IDLE for a settled session."""
    print("\n[CHECK 2] _await_idle_state returns IDLE after a settled turn")
    sid = mgr.create_session(role="itest_idle", lifecycle="ephemeral",
                             title="itest-idle")
    try:
        mgr.send_command(sid, "Say OK and nothing else.", timeout=180)
        outcome = mgr._await_idle_state(
            sid, timeout_s=30, interval_s=1.0, return_on_todo_pending=True
        )
        print(f"  outcome={outcome}")
        return outcome == IdleOutcome.IDLE
    finally:
        mgr.cleanup_session(sid)


def check_todo_nudge(mgr: MigrationSessionManager) -> bool:
    """Target 2: induce a TODO list, then verify the convergence path returns a
    final answer (and the nudge mechanism does not hang or corrupt output).

    Agent TODO behavior is non-deterministic, so this check is lenient: it
    asserts the call completes with a non-empty answer within timeout. We log
    whether a nudge was actually sent by counting POSTs via a wrapper.

    The prompt deliberately tries to induce a real "stopped with an unfinished
    TODO list" state: the agent is told to register a multi-step TODO list,
    complete ONLY the first step, then stop and report an intermediate result
    while leaving the remaining steps pending. Whether the agent actually
    leaves a TODO pending is non-deterministic, so the assertion stays lenient,
    but the prompt maximizes the chance of a genuine nudge trigger."""
    print("\n[CHECK 3] TODO nudge convergence (lenient, induce unfinished TODO)")
    sid = mgr.create_session(role="itest_nudge", lifecycle="ephemeral",
                             title="itest-nudge")

    post_count = {"n": 0}
    original_post = mgr._post_message_only

    def counting_post(session_id, text, agent, timeout):  # type: ignore[no-untyped-def]
        post_count["n"] += 1
        print(f"  [nudge POST #{post_count['n']}] sent")
        return original_post(session_id, text, agent=agent, timeout=timeout)

    mgr._post_message_only = counting_post  # type: ignore[method-assign]
    # Shorten stabilize wait so the test is quick if a nudge triggers.
    mgr._todo_stabilize_wait_s = 3.0
    try:
        prompt = (
            "Use the TODO tool to register exactly these three pending steps:\n"
            "  1. Print the number 1\n"
            "  2. Print the number 2\n"
            "  3. Print the number 3\n"
            "Then perform ONLY step 1: reply with the single line "
            "INTERMEDIATE_STEP_1_DONE and STOP immediately.\n"
            "Do NOT perform step 2 or step 3 yet. Leave steps 2 and 3 marked as "
            "pending / in_progress in the TODO list. Do not mark them completed. "
            "Do not do any work beyond step 1. End your turn now."
        )
        start = time.time()
        reply = mgr.send_command(sid, prompt, timeout=240)
        elapsed = time.time() - start
        triggered = post_count["n"] >= 1
        print(f"  elapsed={elapsed:.1f}s nudges_sent={post_count['n']} "
              f"nudge_triggered={triggered}")
        print(f"  reply head: {reply[:200]!r}")
        if triggered:
            print("  -> real nudge fired and session converged to a final reply")
        else:
            print("  -> agent settled without leaving an unfinished TODO "
                  "(no nudge needed); lenient pass")
        return bool(reply.strip())
    finally:
        mgr._post_message_only = original_post  # type: ignore[method-assign]
        mgr.cleanup_session(sid)


def check_forced_nudge(mgr: MigrationSessionManager) -> bool:
    """Target 2 (deterministic): force the TODO probe to report 'incomplete'
    for the first probes so a REAL nudge POST is sent to the live server, then
    let it converge. Only the TODO judgement is overridden; all HTTP traffic
    (POST message, status, refetch, nudge) hits the real OpenCode server."""
    print("\n[CHECK 4] forced TODO nudge against live server")
    sid = mgr.create_session(role="itest_forced", lifecycle="ephemeral",
                             title="itest-forced")

    probe_state = {"n": 0}
    original_todo = mgr._session_has_incomplete_todos

    def forced_todo(session_id):  # type: ignore[no-untyped-def]
        # Report incomplete for the first 2 probes, then defer to real logic.
        probe_state["n"] += 1
        if probe_state["n"] <= 2:
            print(f"  [forced TODO probe #{probe_state['n']}] -> incomplete")
            return True
        return original_todo(session_id)

    post_count = {"n": 0}
    original_post = mgr._post_message_only

    def counting_post(session_id, text, agent, timeout):  # type: ignore[no-untyped-def]
        post_count["n"] += 1
        print(f"  [real nudge POST #{post_count['n']}] sent to server")
        return original_post(session_id, text, agent=agent, timeout=timeout)

    mgr._session_has_incomplete_todos = forced_todo  # type: ignore[method-assign]
    mgr._post_message_only = counting_post  # type: ignore[method-assign]
    mgr._todo_stabilize_wait_s = 2.0
    mgr._max_todo_nudges = 2
    try:
        start = time.time()
        reply = mgr.send_command(
            sid,
            "Reply with exactly this single line and nothing else: FORCED_NUDGE_OK",
            timeout=240,
        )
        elapsed = time.time() - start
        print(f"  elapsed={elapsed:.1f}s real_nudges_sent={post_count['n']}")
        print(f"  reply head: {reply[:160]!r}")
        # Expect: at least one real nudge was sent, and a non-empty final answer.
        return bool(post_count["n"] >= 1 and reply.strip())
    finally:
        mgr._session_has_incomplete_todos = original_todo  # type: ignore[method-assign]
        mgr._post_message_only = original_post  # type: ignore[method-assign]
        mgr.cleanup_session(sid)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--server-url", default="http://127.0.0.1:4097")
    parser.add_argument("--agent", default="")
    args = parser.parse_args()

    mgr = _new_manager(args.server_url, args.agent or None)
    print(f"server={args.server_url} active_agent={mgr.active_agent}")

    results: dict[str, bool] = {}
    results["basic_idle_refetch"] = check_basic_idle_refetch(mgr)
    results["await_idle_state"] = check_await_idle_state(mgr)
    results["todo_nudge"] = check_todo_nudge(mgr)
    results["forced_nudge"] = check_forced_nudge(mgr)

    print("\n==== RESULTS ====")
    for name, ok in results.items():
        print(f"  {name}: {'PASS' if ok else 'FAIL'}")
    all_ok = all(results.values())
    print(f"OVERALL: {'PASS' if all_ok else 'FAIL'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
