"""Failing-first red proofs: heuristic environment binding must fail closed.

These characterization tests lock the *desired* environment-authority
contract for Wave-2 Todo 4 of the v1.2.1 remote-update remediation
workplan:

    * ``_bind_environment`` must not disambiguate same-namespace /
      same-executable environments using a non-null fact-count tiebreaker.
    * ``prepare_runtime_report_request`` must not force-bind a Phase-5
      environment reference to ``environments[0]`` / ``ns_envs[0]`` when
      the accepted attempt does not match exactly one environment.

At the c6cbed3 baseline both heuristics are present
(``max(executable_matches, key=lambda e: sum(1 for f in e.facts if
f.value is not None))`` and ``target = ns_envs[0] if ns_envs else
current.environments[0]``), so each test below fails for its intended
contract rather than for import/setup error.
"""

from __future__ import annotations

from pathlib import Path

from core.artifact_store import ArtifactStore
from core.execution_env_context import (
    EnvironmentProbe,
    EnvironmentProbeRequest,
)
from core.phase5_attempt_receipt import BackendKind
from core.resource_manifest import (
    ResourceManifestStore,
    ResourceManifestUpdate,
)
from core.run_outcome import (
    AcceptedAttemptId,
    PhaseId,
    ReviewOutcome,
    RunOutcome,
    TerminalAnchor,
    WorkflowTerminal,
)
from core.v3_runtime_report import AcceptedReplaySource
from core.v3_runtime_report_integration import (
    RuntimeReportingInputs,
    _bind_environment,
    prepare_runtime_report_request,
)
from tests.phase5_receipt_test_support import (
    accepted_receipt,
    authority,
    issued_authority,
    save_attempt,
)
from tests.v3_environment_output_test_support import (
    RUN_ID,
    add_base_environment,
    runtime_store,
)

_NAMESPACE = "container:cid-123"
_EXECUTABLE = "/usr/local/bin/python"


def _probe_request(
    environment_id: str,
    *,
    package_inventory_hash: str,
) -> EnvironmentProbeRequest:
    """Build a successful probe; only the package hash varies across calls.

    Both environments share the same namespace and the same executable.
    The variation is the package hash alone — purely to drive a non-null
    fact-count difference without changing identity, which is what the
    c6cbed3 ``max(non-null-count)`` heuristic relies on.
    """
    return EnvironmentProbeRequest(
        probe_id=f"probe-{environment_id}",
        environment_id=environment_id,
        namespace=_NAMESPACE,
        probe=EnvironmentProbe(
            status="ok",
            interpreter_realpath=_EXECUTABLE,
            sys_executable=_EXECUTABLE,
            sys_prefix="/usr/local",
            sys_base_prefix="/usr/local",
            python_implementation="CPython",
            python_version="3.11.9",
            platform="Linux",
            architecture="x86_64",
            package_inventory_hash=package_inventory_hash,
        ),
    )


def _add_probed_environment(
    store: ResourceManifestStore,
    request: EnvironmentProbeRequest,
) -> None:
    captured = store.context._capture_environment_probe(request)  # noqa: SLF001
    current = store.read()
    _ = store.write(
        ResourceManifestUpdate(
            expected_revision=current.revision,
            environments=(captured.environment,),
            probe_receipts=(captured.receipt,),
        )
    )


def _accepted_source_for_executable(
    tmp_path: Path,
    *,
    executable: str,
):
    receipt = accepted_receipt(tmp_path, backend_kind=BackendKind.CONTAINER).model_copy(
        update={
            "run_id": RUN_ID,
            "invocation": accepted_receipt(tmp_path).invocation.model_copy(
                update={"argv": (executable, "validate.py")}
            ),
        }
    )
    path = tmp_path / "accepted-phase5.receipt.json"
    _ = path.write_text(receipt.model_dump_json(indent=2), encoding="utf-8")
    return receipt, AcceptedReplaySource(path, issued_authority(path, receipt))


def test_bind_environment_rejects_non_null_count_tiebreaker(tmp_path: Path) -> None:
    """``_bind_environment`` must refuse ambiguous same-executable environments.

    Given two same-namespace environments whose ``interpreter.sys_executable``
    matches the accepted receipt and whose non-null fact counts are equal
    (same probed values, distinct package hashes).
    When ``_bind_environment`` resolves the accepted attempt.
    Then no Phase-5 environment reference may be written: there is no
    exact identity match, and the c6cbed3 non-null-count tiebreaker is a
    forbidden heuristic that would otherwise pick one arbitrarily.
    """
    store = runtime_store(tmp_path, effective_backend="container")
    _add_probed_environment(
        store, _probe_request("execution-a", package_inventory_hash="a" * 64)
    )
    _add_probed_environment(
        store, _probe_request("execution-b", package_inventory_hash="b" * 64)
    )
    _receipt, source = _accepted_source_for_executable(tmp_path, executable=_EXECUTABLE)

    _bind_environment(store, source)

    references = store.read().phase5_environment_references
    assert references == (), (
        "Binding must fail closed when the executable matches more than one "
        "environment; the c6cbed3 non-null-count tiebreaker is a forbidden "
        "heuristic that picks one of the ambiguous environments."
    )


def test_prepare_runtime_report_request_rejects_force_bind_fallback(
    tmp_path: Path,
) -> None:
    """``prepare_runtime_report_request`` must not force-bind to ``environments[0]``.

    Given one host-namespace environment whose executable does not match
    the accepted receipt, plus an artifact store that exposes the matching
    authority. When ``prepare_runtime_report_request`` builds the report
    request. Then no Phase-5 environment reference may be force-bound via
    the ``ns_envs[0] if ns_envs else current.environments[0]`` fallback;
    the integration must fail closed instead.
    """
    manifest_store = runtime_store(tmp_path)
    add_base_environment(manifest_store)

    artifact_base = tmp_path / "artifacts"
    artifact_base.mkdir()
    artifact_store = ArtifactStore.create_exclusive(str(artifact_base), RUN_ID)

    # Save a real shell attempt into the artifact store. save_attempt uses
    # argv=("python", "validate.py"), which never equals the host env's
    # interpreter.sys_executable, so _bind_environment alone cannot bind.
    receipt_path = save_attempt(artifact_store, tmp_path, exit_code=0)
    attempt_authority = authority(artifact_store, receipt_path)

    receipt = accepted_receipt(tmp_path).model_copy(
        update={
            "run_id": RUN_ID,
            "attempt_id": attempt_authority.attempt_id,
        }
    )
    outcome = RunOutcome(
        validation_succeeded=True,
        review_outcome=ReviewOutcome.DISABLED,
        review_fail_closed=True,
        workflow_terminal=WorkflowTerminal("complete"),
        terminal_anchor=TerminalAnchor(phase_id=PhaseId("phase_5_validation")),
        executed_phases=(PhaseId("phase_5_validation"),),
        accepted_attempt_id=AcceptedAttemptId(receipt.attempt_id),
        review_rounds=(),
    )
    inputs = RuntimeReportingInputs(
        manifest_store=manifest_store,
        artifact_store=artifact_store,
        outcome=outcome,
        expected_run_id=RUN_ID,
    )

    _ = prepare_runtime_report_request(inputs)

    references = manifest_store.read().phase5_environment_references
    assert references == (), (
        "prepare_runtime_report_request must not force-bind a Phase-5 "
        "environment reference to environments[0]/ns_envs[0] when the "
        "accepted attempt does not match exactly one environment; the "
        "c6cbed3 force-bind fallback is a forbidden heuristic."
    )
