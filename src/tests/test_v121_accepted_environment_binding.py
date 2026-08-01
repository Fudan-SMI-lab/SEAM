"""Accepted-environment binding: target disambiguates duplicate executables.

Locks the ``_bind_environment`` tie-breaker that lets a validated
``continuation_target`` select one environment when duplicate
same-namespace / same-executable matches exist, and proves the
persisted Phase-5 reference resolves through ``_target_environment_id``
with a non-null accepted attempt.
"""

from __future__ import annotations

from pathlib import Path

from core.continuation import ParentAcceptedAttemptReference, ResolvedTerminalParent
from core.continuation_models import TerminalParentStatus
from core.execution_env_context import Phase2EnvironmentReport, Phase2EnvironmentRequest
from core.phase5_attempt_receipt import BackendKind
from core.resource_manifest import (
    ContinuationTargetReference,
    ResourceManifestStore,
    ResourceManifestUpdate,
    build_phase2_environment,
)
from core.run_manifest import (
    CanonicalReference,
    EvidenceDigest,
    RunId,
    RunManifest,
    Sha256Digest,
)
from core.run_manifest_models import SharedWorkspaceMarker
from core.run_outcome import AcceptedAttemptId, PhaseId, ReviewOutcome, TerminalAnchor
from core.terminal_continuation_environment import _target_environment_id
from core.v3_runtime_report import AcceptedReplaySource
from core.v3_runtime_report_integration import _bind_environment
from tests.phase5_receipt_test_support import accepted_receipt, issued_authority
from tests.v3_environment_output_test_support import (
    RUN_ID,
    add_container_environment,
    runtime_store,
)

_NAMESPACE = "container:cid-123"
_EXECUTABLE = "/usr/local/bin/python"
_TARGET_ENV = "phase2-project-venv"
_ZERO_DIGEST = Sha256Digest("0" * 64)
_PHASE5 = PhaseId("phase_5_validation")


def _add_phase2_env(
    store: ResourceManifestStore,
    environment_id: str,
    *,
    executable: str = _EXECUTABLE,
) -> None:
    environment = build_phase2_environment(
        Phase2EnvironmentRequest(
            environment_id=environment_id,
            namespace=_NAMESPACE,
            container_id="cid-123",
            report=Phase2EnvironmentReport(
                env_type="venv",
                venv_path="/usr/local",
                python_path=executable,
                installed_packages=(),
            ),
        )
    )
    current = store.read()
    _ = store.write(
        ResourceManifestUpdate(
            expected_revision=current.revision,
            environments=(environment,),
        )
    )


def _accepted_source(tmp_path: Path, *, executable: str):
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


def _set_target(store: ResourceManifestStore, environment_id: str) -> None:
    current = store.read()
    _ = store.write(
        ResourceManifestUpdate(
            expected_revision=current.revision,
            continuation_target=ContinuationTargetReference(
                environment_id=environment_id,
                namespace=_NAMESPACE,
            ),
        )
    )


def _resolved_parent(store: ResourceManifestStore, tmp_path: Path) -> ResolvedTerminalParent:
    return ResolvedTerminalParent(
        run_id=RunId(RUN_ID),
        status=TerminalParentStatus.FAIL,
        output_project=tmp_path,
        workflow_path=tmp_path / "workflow.yaml",
        workflow_digest=_ZERO_DIGEST,
        summary_digest=_ZERO_DIGEST,
        terminal_anchor=TerminalAnchor(phase_id=_PHASE5),
        run_manifest=RunManifest(
            run_id=RunId(RUN_ID),
            parent_run_id=None,
            inherited_canonical=(),
            resource_references=(),
            parent_evidence_digests=(),
            sealed_evidence=(),
            lineage_root_run_id=RunId(RUN_ID),
            revision=1,
            terminal_anchor=TerminalAnchor(phase_id=_PHASE5),
            workflow_digest=_ZERO_DIGEST,
            shared_workspace=SharedWorkspaceMarker(workspace_digest=_ZERO_DIGEST),
            evidence_sealed=True,
        ),
        resource_manifest=store.read(),
    )


def _accepted_reference(attempt_id: str) -> ParentAcceptedAttemptReference:
    return ParentAcceptedAttemptReference(
        parent_run_id=RunId(RUN_ID),
        attempt_id=AcceptedAttemptId(attempt_id),
        canonical_reference=CanonicalReference(
            phase_id=_PHASE5,
            artifact_name="phase_5_validation_canonical.json",
            digest=_ZERO_DIGEST,
        ),
        receipt_evidence=EvidenceDigest(
            relative_path="shell_attempts/phase_5_validation.receipt.json",
            digest=_ZERO_DIGEST,
            size_bytes=1,
        ),
        review_outcome=ReviewOutcome.DISABLED,
        review_fail_closed=True,
        review_rounds=(),
    )


def test_target_disambiguation_binds_and_resolves_accepted_continuation(
    tmp_path: Path,
) -> None:
    """Duplicate executable matches with a target inside the set bind + resolve.

    Given two same-namespace / same-executable environments and a validated
    ``continuation_target`` selecting one of them.  When ``_bind_environment``
    resolves the accepted attempt.  Then exactly one Phase-5 reference is
    written to the target, and ``_target_environment_id`` with a non-null
    accepted attempt resolves it — the code path that raised
    ``RESOURCE_CONTEXT_AMBIGUOUS`` before the fix.
    """
    store = runtime_store(tmp_path, effective_backend="container")
    add_container_environment(store)
    _add_phase2_env(store, _TARGET_ENV)
    _set_target(store, _TARGET_ENV)

    receipt, source = _accepted_source(tmp_path, executable=_EXECUTABLE)
    _bind_environment(store, source)

    references = store.read().phase5_environment_references
    assert len(references) == 1
    assert references[0].environment_reference.value == _TARGET_ENV

    resolved_id = _target_environment_id(
        _resolved_parent(store, tmp_path),
        _accepted_reference(str(receipt.attempt_id)),
    )
    assert resolved_id == _TARGET_ENV


def test_target_outside_executable_matches_fails_closed(
    tmp_path: Path,
) -> None:
    """Target pointing outside the executable match set must not bind.

    Given two same-executable matches plus a validated target referencing a
    THIRD environment whose executable differs from the receipt.  When
    ``_bind_environment`` resolves the accepted attempt.  Then no Phase-5
    reference is written: target presence alone is insufficient when the
    target is not among the executable matches.
    """
    store = runtime_store(tmp_path, effective_backend="container")
    add_container_environment(store)
    _add_phase2_env(store, _TARGET_ENV)
    _add_phase2_env(store, "other-venv", executable="/opt/different/python")
    _set_target(store, "other-venv")

    _receipt, source = _accepted_source(tmp_path, executable=_EXECUTABLE)
    _bind_environment(store, source)

    assert store.read().phase5_environment_references == ()
