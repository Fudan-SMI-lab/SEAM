from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from pathlib import Path

import pytest
from pydantic import ValidationError

from core.resource_manifest import (
    BackendFactRequest,
    Phase2EnvironmentReport,
    Phase2EnvironmentRequest,
    Phase5ReferenceRequest,
    ResourceManifest,
    ResourceManifestContext,
    ResourceManifestError,
    ResourceManifestErrorKind,
    ResourceManifestStore,
    ResourceManifestUpdate,
    build_phase5_reference,
)
from harness.run import FinalizationHooks, finalize_run
from harness.run.resource_manifest_hook import resource_manifest_finalization_hook
from tests.resource_manifest_test_support import manifest_identity, manifest_store
from tests.run_finalizer_test_support import FinalizerScenario, finalization_request


def test_local_backend_rejects_container_attachment() -> None:
    # Given local execution fields that claim image-created attachment.
    # When the request crosses its typed boundary.
    with pytest.raises(ValidationError):
        _ = BackendFactRequest(
            requested_workflow="wf.yaml",
            effective_workflow="wf.yaml",
            requested_backend="local",
            effective_backend="local",
            attachment_mode="image_created",
        )

    # Then local execution cannot claim a container resource.


def test_environment_namespace_matches_container_identity() -> None:
    # Given a report whose namespace and container ID disagree.
    # When the request crosses its typed boundary.
    with pytest.raises(ValidationError):
        _ = Phase2EnvironmentRequest(
            environment_id="env-a",
            namespace="container:cid-a",
            container_id="cid-b",
            report=Phase2EnvironmentReport(
                venv_path="/workspace/.venv",
                python_path="/workspace/.venv/bin/python",
                installed_packages=(),
            ),
        )

    # Then one environment cannot claim two container identities.


def test_phase5_reference_namespace_matches_target_environment(tmp_path: Path) -> None:
    # Given a host environment persisted with its successful receipt.
    store = manifest_store(tmp_path)
    local = store.context.capture_local_environment("local-env")
    _ = store.write(
        ResourceManifestUpdate(
            expected_revision=1,
            environments=(local.environment,),
            probe_receipts=(local.receipt,),
        )
    )
    foreign_reference = build_phase5_reference(
        Phase5ReferenceRequest(
            attempt_id="phase5-attempt-1",
            environment_id="local-env",
            namespace="container:other",
        )
    )

    # When Phase 5 references the host environment from another namespace.
    with pytest.raises(ResourceManifestError) as refusal:
        _ = store.write(
            ResourceManifestUpdate(
                expected_revision=2,
                phase5_environment_references=(foreign_reference,),
            )
        )

    # Then the cross-namespace reference is rejected.
    assert refusal.value.kind is ResourceManifestErrorKind.RUN_CONTEXT_MISMATCH


def test_create_rejects_invalid_initial_manifest_before_write(tmp_path: Path) -> None:
    # Given a revision-one model missing every required runtime fact.
    report_dir = tmp_path / "e2e-reports" / "run-safe-16"
    report_dir.mkdir(parents=True)
    identity = manifest_identity()
    context = ResourceManifestContext.bind(report_dir, identity)
    malformed = ResourceManifest(
        run_id=identity.run_id,
        workflow_digest=identity.workflow_digest,
        workspace_digest=identity.workspace_digest,
        revision=1,
        sealed=False,
        facts=(),
    )

    # When initial persistence is attempted.
    with pytest.raises(ResourceManifestError) as refusal:
        _ = ResourceManifestStore.create(context, malformed)

    # Then no unreadable revision is written.
    assert refusal.value.kind is ResourceManifestErrorKind.MALFORMED
    assert not (report_dir / "resource-manifest.v1.json").exists()


@pytest.mark.skipif(
    os.name != "nt", reason="Windows path aliases are platform-specific"
)
def test_finalizer_accepts_canonical_manifest_from_short_report_alias(
    tmp_path: Path,
) -> None:
    # Given a canonical manifest store and the same report through its 8.3 alias.
    store = manifest_store(tmp_path)
    get_short_path = ctypes.WinDLL("kernel32", use_last_error=True).GetShortPathNameW
    get_short_path.argtypes = (
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        wintypes.DWORD,
    )
    get_short_path.restype = wintypes.DWORD
    required = get_short_path(str(store.path.parent), None, 0)
    assert required > 0
    buffer = ctypes.create_unicode_buffer(required)
    written = get_short_path(str(store.path.parent), buffer, required)
    assert 0 < written < required
    short_report = buffer.value
    if Path(short_report) == store.path.parent:
        pytest.skip("the test volume did not expose an 8.3 alias")
    hooks = FinalizationHooks(
        post_cleanup_manifest=resource_manifest_finalization_hook(
            store, Path(short_report)
        )
    )

    # When the real Task 5 finalizer uses the short alias.
    result = finalize_run(
        finalization_request(Path(short_report), FinalizerScenario(hooks=hooks))
    )

    # Then the canonical manifest receipt survives validation and freezing.
    assert (
        Path(result.summary.telemetry_paths["resource_manifest_json"]).resolve()
        == store.path.resolve()
    )
    assert store.read().sealed is True
