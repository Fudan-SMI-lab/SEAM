"""Failing-first red proofs: explicit continuation-target environment authority.

These characterization tests lock the *desired* explicit-authority contract
for Wave-2 Todo 4 of the v1.2.1 remote-update remediation workplan:

    * The resource-manifest model carries an additive typed
      ``continuation_target`` field (``ContinuationTargetReference``)
      that is optional so older pre-environment manifests remain
      parseable.
    * ``prepare_runtime_report_request`` records the explicit
      continuation-target reference from the exact Phase-2 environment
      the direct runner selected, never from list order.  A pre-existing
      same-ID environment still receives the target after identity
      verification.
    * Manifest validation refuses references that are detached or whose
      namespace disagrees with the recorded environment.
    * ``merge_update`` treats the reference as immutable once it is set so
      the exact Phase-2 target cannot be redirected after the fact.
    * Terminal continuation resolves the target via the explicit field
      when no authenticated accepted-attempt reference exists; it must
      not fall back to ``environments[0]`` or any list-order heuristic.
    * A new Phase-2 environment and its target are written in a SINGLE
      ``ResourceManifestUpdate`` so no crash window can leave a valid
      Phase-2 record without its explicit authority.
    * A pre-existing same-ID environment whose namespace or interpreter
      executable conflicts with the Phase-2 request is refused, never
      silently accepted.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import TypeAdapter

from core.continuation import resolve_terminal_parent
from core.execution_env_context import (
    EnvironmentProbe,
    EnvironmentProbeRequest,
    Phase2EnvironmentReport,
    Phase2EnvironmentRequest,
)
from core.resource_manifest import (
    ContinuationTargetReference,
    ResourceManifest,
    ResourceManifestError,
    ResourceManifestErrorKind,
    ResourceManifestStore,
    ResourceManifestUpdate,
)
from core.resource_manifest_status import TerminalResourceStatus
from core.terminal_continuation_environment import _target_environment_id
from core.terminal_continuation_models import (
    TerminalContinuationError,
    TerminalContinuationErrorKind,
)
from core.v3_runtime_report_integration import (
    RuntimeReportingInputs,
    prepare_runtime_report_request,
)
from tests.run_finalizer_test_support import FinalizerScenario, finalization_request
from tests.terminal_run_continuation_hydration_support import (
    create_hydration_parent,
)
from tests.terminal_run_continuation_parent_scenarios import ParentRun
from tests.v3_environment_output_test_support import (
    RUN_ID,
    add_project_venv,
    runtime_store,
)
from tests.terminal_run_continuation_test_support import tree_bytes

_ENV_ID = "phase2-project-venv"
_NAMESPACE = "host"


def _record_environment_with_namespace(
    store: ResourceManifestStore,
    *,
    environment_id: str,
    namespace: str,
    executable: str = "/workspace/.venv/bin/python",
    package_inventory_hash: str = "a" * 64,
) -> None:
    captured = store.context._capture_environment_probe(
        EnvironmentProbeRequest(
            probe_id=f"probe-{environment_id}",
            environment_id=environment_id,
            namespace=namespace,
            probe=EnvironmentProbe(
                status="ok",
                interpreter_realpath=executable,
                sys_executable=executable,
                sys_prefix="/workspace/.venv",
                sys_base_prefix="/usr",
                python_implementation="CPython",
                python_version="3.11.9",
                platform="Linux",
                architecture="x86_64",
                package_inventory_hash=package_inventory_hash,
            ),
        )
    )
    current = store.read()
    _ = store.write(
        ResourceManifestUpdate(
            expected_revision=current.revision,
            environments=(captured.environment,),
            probe_receipts=(captured.receipt,),
        )
    )


def _read_manifest_payload(store: ResourceManifestStore) -> dict[str, object]:
    return TypeAdapter(dict[str, object]).validate_python(
        store.read().model_dump(by_alias=True, mode="json")
    )


def _write_manifest_payload(
    store: ResourceManifestStore,
    payload: dict[str, object],
) -> None:
    _ = store.path.write_text(
        TypeAdapter(dict[str, object]).dump_json(payload, indent=2).decode("utf-8"),
        encoding="utf-8",
    )


def _phase2_request(
    *,
    environment_id: str = _ENV_ID,
    namespace: str = _NAMESPACE,
    python_path: str = "/usr/local/bin/python",
) -> Phase2EnvironmentRequest:
    return Phase2EnvironmentRequest(
        environment_id=environment_id,
        namespace=namespace,
        report=Phase2EnvironmentReport(
            env_type="base_env",
            venv_path="/usr/local",
            python_path=python_path,
            installed_packages=("numpy==1.26.4",),
        ),
    )


def _prepare(
    store: ResourceManifestStore,
    phase2: Phase2EnvironmentRequest,
) -> None:
    outcome = finalization_request(
        store.path.parent, FinalizerScenario()
    ).authoritative_outcome
    assert outcome is not None
    _ = prepare_runtime_report_request(
        RuntimeReportingInputs(
            manifest_store=store,
            artifact_store=None,
            outcome=outcome,
            expected_run_id=RUN_ID,
            phase2_environment=phase2,
        )
    )


def test_resource_manifest_accepts_absent_continuation_target(tmp_path: Path) -> None:
    """Older manifests without the additive field remain parseable.

    Given a sealed resource manifest that pre-dates the explicit field.
    When the model revalidates the JSON payload without the field.
    Then ``continuation_target`` is ``None`` rather than a parser error,
    so pre-environment manifests continue to load.
    """
    store = runtime_store(tmp_path)
    payload = _read_manifest_payload(store)
    _ = payload.pop("continuation_target", None)
    _write_manifest_payload(store, payload)

    legacy = store.read()

    assert legacy.continuation_target is None


def test_prepare_runtime_report_records_continuation_target_from_phase2(
    tmp_path: Path,
) -> None:
    """Phase-2 recording must persist the explicit continuation target.

    Given a runtime store and a Phase-2 environment request from the
    runner.  When ``prepare_runtime_report_request`` records the Phase-2
    environment.  Then the resulting manifest carries a
    ``continuation_target`` whose environment_id and namespace match the
    exact Phase-2 request, so continuation authority never relies on list
    order or namespace alone.
    """
    store = runtime_store(tmp_path)
    _prepare(store, _phase2_request())

    target = store.read().continuation_target
    assert target is not None
    assert target.environment_id == _ENV_ID
    assert target.namespace == _NAMESPACE


def test_prepare_runtime_report_sets_target_for_pre_existing_same_id_environment(
    tmp_path: Path,
) -> None:
    """A pre-existing same-ID Phase-2 environment still receives the target.

    Given a store where ``phase2-project-venv`` was already recorded
    (e.g. via ``add_project_venv``) without an explicit target.  When
    ``prepare_runtime_report_request`` re-records the same Phase-2
    environment.  Then the manifest's ``continuation_target`` is set to
    ``phase2-project-venv`` — the early return that left the target
    absent is gone.
    """
    store = runtime_store(tmp_path)
    add_project_venv(store, tmp_path)
    assert store.read().continuation_target is None

    _prepare(store, _phase2_request(python_path=str((tmp_path / ".venv" / "bin" / "python").resolve())))

    target = store.read().continuation_target
    assert target is not None
    assert target.environment_id == _ENV_ID


def test_prepare_runtime_report_rejects_same_id_namespace_conflict(
    tmp_path: Path,
) -> None:
    """A same-ID record from a different namespace is refused, never accepted.

    Given a container-backend store where ``phase2-project-venv`` exists
    under namespace ``container:cid-123``.  When
    ``prepare_runtime_report_request`` tries to record the Phase-2
    environment under namespace ``host``.  Then the returned request
    degrades its manifest_store to ``None`` and the on-disk manifest is
    unchanged: the target is NOT set to the impostor, and the existing
    record is NOT overwritten.
    """
    store = runtime_store(tmp_path, effective_backend="container")
    _record_environment_with_namespace(
        store,
        environment_id=_ENV_ID,
        namespace="container:cid-123",
    )
    manifest_before = store.read()
    assert manifest_before.continuation_target is None

    outcome = finalization_request(
        store.path.parent, FinalizerScenario()
    ).authoritative_outcome
    assert outcome is not None
    prepared = prepare_runtime_report_request(
        RuntimeReportingInputs(
            manifest_store=store,
            artifact_store=None,
            outcome=outcome,
            expected_run_id=RUN_ID,
            phase2_environment=_phase2_request(namespace=_NAMESPACE),
        )
    )

    assert prepared.manifest_store is None
    manifest_after = store.read()
    assert manifest_after.continuation_target is None
    assert manifest_after.revision == manifest_before.revision


def test_prepare_runtime_report_rejects_same_id_executable_conflict(
    tmp_path: Path,
) -> None:
    """A same-ID record with a different interpreter executable is refused.

    Given a store where ``phase2-project-venv`` exists with executable
    ``/opt/different/python``.  When ``prepare_runtime_report_request``
    tries to record the Phase-2 environment with a different executable.
    Then the returned request degrades its manifest_store to ``None``
    and the on-disk manifest is unchanged: the target is NOT set to the
    incompatible identity.
    """
    store = runtime_store(tmp_path)
    _record_environment_with_namespace(
        store,
        environment_id=_ENV_ID,
        namespace=_NAMESPACE,
        executable="/opt/different/python",
    )
    manifest_before = store.read()

    outcome = finalization_request(
        store.path.parent, FinalizerScenario()
    ).authoritative_outcome
    assert outcome is not None
    prepared = prepare_runtime_report_request(
        RuntimeReportingInputs(
            manifest_store=store,
            artifact_store=None,
            outcome=outcome,
            expected_run_id=RUN_ID,
            phase2_environment=_phase2_request(python_path="/usr/local/bin/python"),
        )
    )

    assert prepared.manifest_store is None
    manifest_after = store.read()
    assert manifest_after.continuation_target is None
    assert manifest_after.revision == manifest_before.revision


def test_prepare_runtime_report_rejects_unknown_executable_from_failed_probe(
    tmp_path: Path,
) -> None:
    """Unknown executable evidence refuses target establishment.

    Given a store where ``phase2-project-venv`` was captured via a
    FAILED probe (``status='error'``): every observed fact has
    ``FactStatus.ERROR`` and ``value=None``, so there is no known
    ``interpreter.sys_executable`` to compare against.  When
    ``prepare_runtime_report_request`` tries to set the continuation
    target.  Then the returned request degrades its manifest_store to
    ``None`` and the on-disk manifest is unchanged: absence of known
    executable evidence is NOT compatible identity proof.
    """
    store = runtime_store(tmp_path)
    captured = store.context._capture_environment_probe(
        EnvironmentProbeRequest(
            probe_id=f"probe-{_ENV_ID}",
            environment_id=_ENV_ID,
            namespace=_NAMESPACE,
            probe=EnvironmentProbe(
                status="error",
                interpreter_realpath=None,
                sys_executable=None,
                sys_prefix=None,
                sys_base_prefix=None,
                python_implementation=None,
                python_version=None,
                platform=None,
                architecture=None,
                package_inventory_hash=None,
                error="probe timed out",
            ),
        )
    )
    current = store.read()
    _ = store.write(
        ResourceManifestUpdate(
            expected_revision=current.revision,
            environments=(captured.environment,),
            probe_receipts=(captured.receipt,),
        )
    )
    manifest_before = store.read()
    assert manifest_before.continuation_target is None

    outcome = finalization_request(
        store.path.parent, FinalizerScenario()
    ).authoritative_outcome
    assert outcome is not None
    prepared = prepare_runtime_report_request(
        RuntimeReportingInputs(
            manifest_store=store,
            artifact_store=None,
            outcome=outcome,
            expected_run_id=RUN_ID,
            phase2_environment=_phase2_request(python_path="/usr/local/bin/python"),
        )
    )

    assert prepared.manifest_store is None
    manifest_after = store.read()
    assert manifest_after.continuation_target is None
    assert manifest_after.revision == manifest_before.revision


def test_prepare_runtime_report_rejects_multiple_distinct_known_executables(
    tmp_path: Path,
) -> None:
    """Multiple distinct known executables refuse target establishment.

    Given a store where ``phase2-project-venv`` has two known
    ``interpreter.sys_executable`` values from different provenance
    sources (FRAMEWORK_OBSERVED ``/probed/python`` and AGENT_REPORTED
    ``/reported/python``).  When ``prepare_runtime_report_request``
    tries to set the continuation target using a Phase-2 request whose
    ``python_path`` MATCHES the first known value.  Then the returned
    request degrades its manifest_store to ``None`` and the on-disk
    manifest is unchanged: ambiguity is NOT resolved by selecting the
    first value, ranking by provenance, or counting facts — even when
    the request happens to match one of the conflicting values.
    """
    store = runtime_store(tmp_path)
    _record_environment_with_namespace(
        store,
        environment_id=_ENV_ID,
        namespace=_NAMESPACE,
        executable="/probed/python",
    )
    from core.resource_manifest import build_phase2_environment

    phase2_conflict = Phase2EnvironmentRequest(
        environment_id=_ENV_ID,
        namespace=_NAMESPACE,
        report=Phase2EnvironmentReport(
            env_type="venv",
            venv_path="/workspace/.venv",
            python_path="/reported/python",
            installed_packages=(),
        ),
    )
    conflicting_env = build_phase2_environment(phase2_conflict)
    current = store.read()
    _ = store.write(
        ResourceManifestUpdate(
            expected_revision=current.revision,
            environments=(conflicting_env,),
        )
    )
    manifest_before = store.read()
    assert manifest_before.continuation_target is None

    outcome = finalization_request(
        store.path.parent, FinalizerScenario()
    ).authoritative_outcome
    assert outcome is not None
    prepared = prepare_runtime_report_request(
        RuntimeReportingInputs(
            manifest_store=store,
            artifact_store=None,
            outcome=outcome,
            expected_run_id=RUN_ID,
            phase2_environment=_phase2_request(python_path="/probed/python"),
        )
    )

    assert prepared.manifest_store is None
    manifest_after = store.read()
    assert manifest_after.continuation_target is None
    assert manifest_after.revision == manifest_before.revision


def test_prepare_runtime_report_preserves_richer_facts_on_compatible_existing(
    tmp_path: Path,
) -> None:
    """A compatible existing environment's framework-observed facts survive.

    Given a store where ``phase2-project-venv`` was captured via a
    framework probe (richer evidence than a bare Phase-2 record).  When
    ``prepare_runtime_report_request`` re-records the matching Phase-2
    request.  Then the target is set AND the probed facts (e.g.
    ``interpreter.realpath``) are preserved — the Phase-2 agent-reported
    facts do not overwrite or downgrade them.
    """
    store = runtime_store(tmp_path)
    probed_executable = "/workspace/.venv/bin/python"
    _record_environment_with_namespace(
        store,
        environment_id=_ENV_ID,
        namespace=_NAMESPACE,
        executable=probed_executable,
    )
    manifest_before = store.read()
    fact_count_before = len(
        manifest_before.environments[0].facts
    )

    _prepare(store, _phase2_request(python_path=probed_executable))

    manifest_after = store.read()
    env_after = manifest_after.environments[0]
    assert manifest_after.continuation_target is not None
    assert manifest_after.continuation_target.environment_id == _ENV_ID
    assert len(env_after.facts) == fact_count_before


def test_new_phase2_environment_and_target_share_single_atomic_revision(
    tmp_path: Path,
) -> None:
    """No crash window: new environment and target land in one revision.

    Given a store with no Phase-2 environment.  When
    ``prepare_runtime_report_request`` records the Phase-2 environment
    for the first time.  Then exactly ONE store revision is consumed
    (revision delta == 1), proving the environment and its target
    authority were committed atomically — there is no intermediate
    committed manifest containing the newly recorded environment without
    its explicit target.
    """
    store = runtime_store(tmp_path)
    revision_before = store.read().revision

    _prepare(store, _phase2_request())

    revision_after = store.read().revision
    assert revision_after == revision_before + 1, (
        "New Phase-2 environment and continuation target must be written "
        "in a single atomic ResourceManifestUpdate so no crash window can "
        f"leave the environment without its authority (got delta "
        f"{revision_after - revision_before})."
    )


def test_resource_manifest_rejects_continuation_target_namespace_mismatch(
    tmp_path: Path,
) -> None:
    """Validation refuses a namespace-mismatched continuation target.

    Given a manifest whose ``continuation_target`` namespace disagrees
    with the referenced environment's recorded namespace.  When the
    manifest is revalidated.  Then validation raises a typed
    ``ResourceManifestError`` rather than letting the mismatched
    reference reach child session creation.
    """
    store = runtime_store(tmp_path)
    _record_environment_with_namespace(
        store, environment_id=_ENV_ID, namespace=_NAMESPACE
    )
    payload = _read_manifest_payload(store)
    payload["continuation_target"] = {
        "environment_id": _ENV_ID,
        "namespace": "container:impostor",
    }
    _write_manifest_payload(store, payload)

    with pytest.raises(ResourceManifestError) as refusal:
        _ = store.read()

    assert refusal.value.kind is ResourceManifestErrorKind.RUN_CONTEXT_MISMATCH


def test_resource_manifest_rejects_continuation_target_referencing_absent_environment(
    tmp_path: Path,
) -> None:
    """Validation refuses a detached continuation-target reference.

    Given a manifest whose ``continuation_target`` references an
    identifier that is not present in ``environments``.  When the
    manifest is revalidated.  Then validation raises a typed
    ``ResourceManifestError``.
    """
    store = runtime_store(tmp_path)
    payload = _read_manifest_payload(store)
    payload["continuation_target"] = {
        "environment_id": _ENV_ID,
        "namespace": _NAMESPACE,
    }
    _write_manifest_payload(store, payload)

    with pytest.raises(ResourceManifestError) as refusal:
        _ = store.read()

    assert refusal.value.kind is ResourceManifestErrorKind.RUN_CONTEXT_MISMATCH


def test_resource_manifest_update_continuation_target_is_immutable_once_set(
    tmp_path: Path,
) -> None:
    """The continuation target must be set once and never mutated.

    Given a manifest whose ``continuation_target`` already points to the
    recorded Phase-2 environment.  When a later update tries to redirect
    it to a different reference.  Then ``store.write`` refuses with a
    typed manifest error, preserving the exact Phase-2 authority the
    direct runner recorded.
    """
    store = runtime_store(tmp_path)
    _record_environment_with_namespace(
        store, environment_id=_ENV_ID, namespace=_NAMESPACE
    )
    current = store.read()
    target = ContinuationTargetReference(
        environment_id=_ENV_ID,
        namespace=_NAMESPACE,
    )
    _ = store.write(
        ResourceManifestUpdate(
            expected_revision=current.revision,
            continuation_target=target,
        )
    )
    established = store.read()
    assert established.continuation_target == target

    _record_environment_with_namespace(
        store, environment_id="execution-python", namespace=_NAMESPACE
    )
    target_current = store.read()

    with pytest.raises(ResourceManifestError) as refusal:
        _ = store.write(
            ResourceManifestUpdate(
                expected_revision=target_current.revision,
                continuation_target=ContinuationTargetReference(
                    environment_id="execution-python",
                    namespace=_NAMESPACE,
                ),
            )
        )

    assert refusal.value.kind is ResourceManifestErrorKind.RUN_CONTEXT_MISMATCH


def test_target_environment_id_uses_explicit_field_when_no_accepted_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Terminal continuation must read the explicit field, not list order.

    Given an authenticated parent manifest with two recorded environments
    and a valid explicit ``continuation_target`` referencing one of them,
    with no accepted-attempt reference.  When ``_target_environment_id``
    resolves the retained target.  Then it returns the explicitly
    referenced identifier rather than raising
    ``RESOURCE_CONTEXT_AMBIGUOUS`` or guessing ``environments[0]``.
    """
    parent = _parent_with_manifest(
        tmp_path,
        monkeypatch,
        environment_ids=("execution-python", _ENV_ID),
        continuation_target=ContinuationTargetReference(
            environment_id=_ENV_ID,
            namespace=_NAMESPACE,
        ),
    )
    resolved = _resolve_parent(parent)

    target_id = _target_environment_id(resolved, accepted=None)

    assert target_id == _ENV_ID


def test_target_environment_id_rejects_when_no_explicit_field_and_multiple_environments(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without an explicit field, list order must never substitute.

    Given an authenticated parent manifest with multiple environments, no
    explicit continuation target, and no accepted-attempt reference.
    When ``_target_environment_id`` resolves the target.  Then it fails
    closed with a typed ``TerminalContinuationError`` rather than
    guessing from list order.
    """
    parent = _parent_with_manifest(
        tmp_path,
        monkeypatch,
        environment_ids=("execution-python", _ENV_ID),
        continuation_target=None,
    )
    resolved = _resolve_parent(parent)
    parent_before = tree_bytes(parent.report_dir)

    with pytest.raises(TerminalContinuationError) as refusal:
        _ = _target_environment_id(resolved, accepted=None)

    assert (
        refusal.value.kind
        is TerminalContinuationErrorKind.RESOURCE_CONTEXT_AMBIGUOUS
    )
    assert tree_bytes(parent.report_dir) == parent_before


def _resolve_parent(parent: ParentRun):
    """Resolve a hydrated parent into the typed terminal continuation parent."""
    return resolve_terminal_parent(parent.summary_path)


def _parent_with_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    environment_ids: tuple[str, ...],
    continuation_target: ContinuationTargetReference | None,
) -> ParentRun:
    """Build an authenticated parent whose sealed manifest carries the named
    environments and the explicit continuation target.

    Replaces ``ResourceManifestStore.seal`` so the recorded environments
    and the explicit ``continuation_target`` are written before the
    original seal runs. The hook captures real probe receipts so the
    resulting manifest satisfies authority checks at ``store.read`` time.
    """
    original_seal = ResourceManifestStore.seal

    def seal_with_explicit(
        store: ResourceManifestStore,
        expected_revision: int,
        terminal_status: TerminalResourceStatus,
    ) -> ResourceManifest:
        revised_revision = expected_revision
        for environment_id in environment_ids:
            captured = store.context._capture_environment_probe(
                EnvironmentProbeRequest(
                    probe_id=f"probe-{environment_id}",
                    environment_id=environment_id,
                    namespace=_NAMESPACE,
                    probe=EnvironmentProbe(
                        status="ok",
                        interpreter_realpath="/workspace/.venv/bin/python",
                        sys_executable="/workspace/.venv/bin/python",
                        sys_prefix="/workspace/.venv",
                        sys_base_prefix="/usr",
                        python_implementation="CPython",
                        python_version="3.11.9",
                        platform="Linux",
                        architecture="x86_64",
                        package_inventory_hash="a" * 64,
                    ),
                )
            )
            current = store.read()
            revised = store.write(
                ResourceManifestUpdate(
                    expected_revision=current.revision,
                    environments=(captured.environment,),
                    probe_receipts=(captured.receipt,),
                )
            )
            revised_revision = revised.revision
        if continuation_target is not None:
            current = store.read()
            revised = store.write(
                ResourceManifestUpdate(
                    expected_revision=current.revision,
                    continuation_target=continuation_target,
                )
            )
            revised_revision = revised.revision
        return original_seal(store, revised_revision, terminal_status)

    monkeypatch.setattr(ResourceManifestStore, "seal", seal_with_explicit)
    root = tmp_path.parent / (
        f"auth-{continuation_target.environment_id if continuation_target else 'none'}-"
        f"{'-'.join(environment_ids)}"
    )
    root.mkdir(exist_ok=True)
    try:
        return create_hydration_parent(
            root,
            status="FAIL",
            anchor_phase="phase_5_validation",
            phase_statuses=("passed", "passed", "passed", "failed", "skipped"),
            canonical_phase_ids=(
                "phase_0_detect",
                "phase_2_prepare",
                "phase_4_migrate",
                "phase_5_validation",
                "phase_6_report",
            ),
            workflow_bytes=_minimal_workflow_bytes(),
        )
    finally:
        monkeypatch.setattr(ResourceManifestStore, "seal", original_seal)


def _minimal_workflow_bytes() -> bytes:
    return (
        "name: continuation-authority\n"
        "version: '1.0'\n"
        "globals:\n"
        "  review_fail_closed: true\n"
        "experience:\n"
        "  enabled: false\n"
        "phases:\n"
        "  - id: phase_0_detect\n"
        "    type: builtin\n"
        "    operation: noop\n"
        "    transitions: {on_success: phase_2_prepare}\n"
        "  - id: phase_2_prepare\n"
        "    type: builtin\n"
        "    operation: noop\n"
        "    transitions: {on_success: phase_4_migrate}\n"
        "  - id: phase_4_migrate\n"
        "    type: builtin\n"
        "    operation: noop\n"
        "    transitions: {on_success: phase_5_validation}\n"
        "  - id: phase_5_validation\n"
        "    type: builtin\n"
        "    operation: noop\n"
        "    transitions: {on_success: phase_6_report, on_failure: failed}\n"
        "  - id: phase_6_report\n"
        "    type: builtin\n"
        "    operation: noop\n"
        "    transitions: {on_success: complete}\n"
        "terminals: [complete, failed]\n"
    ).encode()
