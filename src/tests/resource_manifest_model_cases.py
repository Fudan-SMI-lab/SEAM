from __future__ import annotations

from importlib import import_module
from importlib.util import find_spec


PUBLIC_API = (
    "BackendFactRequest",
    "EnvironmentProbe",
    "EnvironmentProbeRequest",
    "EnvironmentRecord",
    "EnvironmentType",
    "FactProvenance",
    "FactStatus",
    "OpenCodeFactRequest",
    "Phase2EnvironmentReport",
    "Phase2EnvironmentRequest",
    "Phase5EnvironmentReference",
    "Phase5ReferenceRequest",
    "ProbeReceipt",
    "ProbedEnvironment",
    "ProvenanceFact",
    "ResourceManifest",
    "ResourceManifestContext",
    "ResourceManifestError",
    "ResourceManifestErrorKind",
    "ResourceManifestIdentity",
    "ResourceManifestStore",
    "ResourceManifestUpdate",
    "build_backend_facts",
    "build_initial_manifest",
    "build_opencode_facts",
    "build_phase2_environment",
    "build_phase5_reference",
    "capture_launcher_facts",
    "capture_local_environment",
    "probe_environment_record",
)


def test_resource_manifest_module_is_available() -> None:
    # Given Task 16 requires a public core resource-manifest boundary.
    # When Python resolves that boundary.
    spec = find_spec("core.resource_manifest")

    # Then the implementation module must exist.
    assert spec is not None


def test_resource_manifest_exposes_typed_public_contract() -> None:
    # Given the Task 16 module is importable.
    module = import_module("core.resource_manifest")

    # When its supported contract is inspected.
    missing = tuple(name for name in PUBLIC_API if not hasattr(module, name))

    # Then all typed construction, revision, and probe seams are explicit.
    assert missing == ()
