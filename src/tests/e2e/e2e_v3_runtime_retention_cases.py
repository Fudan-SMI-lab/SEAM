from __future__ import annotations

from pathlib import Path

import pytest

from core.resource_retention import ContainerRetention
from harness.session.opencode_contract import JsonObject
from tests.opencode_contract_test_helpers import object_list_member

from .e2e_v3_runtime_backend import FakeContainerRuntime
from .e2e_v3_runtime_fixture import RuntimeScenario, read_json, run_runtime_scenario


def _fact_value(manifest: JsonObject, name: str) -> str | None:
    for fact in object_list_member(manifest, "facts"):
        if fact.get("name") == name:
            value = fact.get("value")
            assert value is None or isinstance(value, str)
            return value
    raise AssertionError(f"missing resource fact: {name}")


def test_v3_runtime_retains_owned_container_without_auto_remove_or_delete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a framework-created image container under default retain policy.
    runtime = FakeContainerRuntime()
    scenario = RuntimeScenario(
        run_hex="6" * 32,
        container_source="image",
        container_runtime=runtime,
    )

    # When the public V3 lifecycle completes and records retention.
    result = run_runtime_scenario(tmp_path, monkeypatch, scenario)

    # Then creation/validation occurred but destructive commands are prohibited.
    summary = read_json(result.report_dir / "summary.json")
    manifest = read_json(result.report_dir / "resource-manifest.v1.json")
    create = next(call for call in runtime.calls if call[1] == "run")
    assert result.exit_code == 0
    assert summary["overall_status"] == "PASS"
    assert runtime.count("run") == 1
    assert runtime.count("exec") >= 2
    assert runtime.count("stop") == runtime.count("rm") == 0
    assert "--rm" not in create
    assert _fact_value(manifest, "retention.effective") == "retain"
    assert _fact_value(manifest, "retention.cleanup_result") == "retained"


def test_v3_runtime_never_deletes_external_existing_container(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a user/external existing container and an explicit delete request.
    runtime = FakeContainerRuntime()
    scenario = RuntimeScenario(
        run_hex="7" * 32,
        container_source="existing_container",
        container_runtime=runtime,
        container_retention=ContainerRetention.DELETE,
    )

    # When V3 resolves ownership before finalization.
    result = run_runtime_scenario(tmp_path, monkeypatch, scenario)

    # Then it attaches and executes but never creates, stops, or removes the resource.
    manifest = read_json(result.report_dir / "resource-manifest.v1.json")
    assert result.exit_code == 0
    assert runtime.count("inspect") >= 1
    assert runtime.count("exec") >= 2
    assert runtime.count("run") == 0
    assert runtime.count("stop") == runtime.count("rm") == 0
    assert _fact_value(manifest, "ownership.resource_owner_kind") == "external"
    assert _fact_value(manifest, "retention.effective") == "retain"


def test_v3_runtime_deletes_owned_container_in_stop_remove_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given an owned image container with authenticated delete policy.
    runtime = FakeContainerRuntime()
    scenario = RuntimeScenario(
        run_hex="8" * 32,
        container_source="image",
        container_runtime=runtime,
        container_retention=ContainerRetention.DELETE,
    )

    # When authorized cleanup executes after evidence capture.
    result = run_runtime_scenario(tmp_path, monkeypatch, scenario)

    # Then stop precedes one remove and no implicit --rm bypass exists.
    manifest = read_json(result.report_dir / "resource-manifest.v1.json")
    actions = [call[1] for call in runtime.calls]
    assert result.exit_code == 0
    assert actions.index("stop") < actions.index("rm")
    assert runtime.count("stop") == runtime.count("rm") == 1
    assert "--rm" not in next(call for call in runtime.calls if call[1] == "run")
    assert _fact_value(manifest, "retention.cleanup_result") == "deleted"
    assert _fact_value(manifest, "retention.post_state") == "absent"


def test_v3_runtime_owned_cleanup_failure_preserves_pass_and_exits_two(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given an owned delete whose runtime remove command fails after stop.
    runtime = FakeContainerRuntime(fail_remove=True)
    scenario = RuntimeScenario(
        run_hex="9" * 32,
        container_source="image",
        container_runtime=runtime,
        container_retention=ContainerRetention.DELETE,
    )

    # When the typed cleanup error crosses the real finalizer.
    result = run_runtime_scenario(tmp_path, monkeypatch, scenario)

    # Then migration stays PASS, exit becomes 2, and remove is never retried.
    summary = read_json(result.report_dir / "summary.json")
    manifest = read_json(result.report_dir / "resource-manifest.v1.json")
    assert result.exit_code == 2
    assert summary["overall_status"] == "PASS"
    assert runtime.count("stop") == runtime.count("rm") == 1
    assert _fact_value(manifest, "retention.cleanup_result") == "failed"
    assert _fact_value(manifest, "retention.post_state") == "stopped"
    assert _fact_value(manifest, "retention.continuation_available") == "false"
