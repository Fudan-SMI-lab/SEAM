from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from core.resource_manifest import (
    BackendFactRequest,
    EnvironmentProbe,
    ResourceManifestError,
    ResourceManifestErrorKind,
    ResourceManifestUpdate,
    capture_local_environment,
)
from core.resource_manifest_paths import (
    ResourceDirectoryBinding,
    bind_resource_directory,
    require_bound_resource_directory,
)
from harness.run import FinalizationHooks, finalize_run
from harness.run.finalizer import finalize_run as task5_finalize_run
from harness.run.resource_manifest_hook import resource_manifest_finalization_hook
from tests.resource_manifest_test_support import manifest_store
from tests.run_finalizer_test_support import FinalizerScenario, finalization_request


def _directory_link(link: Path, target: Path) -> None:
    if os.name == "nt":
        created = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            encoding="mbcs",
            errors="replace",
            check=False,
        )
        if created.returncode != 0:
            pytest.skip(f"junction unavailable: {created.stderr or created.stdout}")
        return
    link.symlink_to(target, target_is_directory=True)


def test_backend_names_are_typed_variants() -> None:
    # Given a misspelled effective backend.
    # When it crosses the typed request boundary.
    with pytest.raises(ValidationError):
        _ = BackendFactRequest.model_validate(
            {
                "requested_workflow": "wf.yaml",
                "effective_workflow": "wf.yaml",
                "requested_backend": "container",
                "effective_backend": "contianer",
            }
        )

    # Then unknown backend variants cannot silently become local execution.


@pytest.mark.parametrize(
    "probe",
    [
        EnvironmentProbe.model_construct(
            status="ok",
            interpreter_realpath="/usr/bin/python",
            sys_executable="/usr/bin/python",
            sys_prefix="/usr",
            sys_base_prefix="/usr",
            python_implementation="CPython",
            python_version="3.10.12",
            platform="Linux",
            architecture="x86_64",
            package_inventory_hash="d" * 64,
            error="contradictory error",
        ),
        EnvironmentProbe.model_construct(
            status="error",
            interpreter_realpath="/usr/bin/python",
            sys_executable="/usr/bin/python",
            sys_prefix="/usr",
            sys_base_prefix="/usr",
            python_implementation="CPython",
            python_version="3.10.12",
            platform="Linux",
            architecture="x86_64",
            package_inventory_hash="d" * 64,
            error="probe failed",
        ),
    ],
)
def test_probe_status_rejects_incompatible_fields(probe: EnvironmentProbe) -> None:
    # Given a probe combines success and error fields.
    # When the model is validated from its serialized boundary.
    with pytest.raises(ValidationError):
        _ = EnvironmentProbe.model_validate(probe.model_dump())

    # Then probe status has one unambiguous payload shape.


def test_local_capture_supports_maximum_environment_id() -> None:
    # Given the maximum valid environment identifier.
    environment_id = "e" * 128

    # When the framework captures the local environment.
    captured = capture_local_environment(environment_id)

    # Then its generated receipt ID remains within the same public bound.
    assert captured.environment.environment_id == environment_id
    assert len(captured.receipt.probe_id) <= 128


def test_store_rejects_report_directory_swap(tmp_path: Path) -> None:
    # Given a store bound to a real report directory.
    store = manifest_store(tmp_path)
    original = store.path.parent
    moved = original.with_name("moved-report")
    outside = tmp_path / "outside-report"
    outside.mkdir()
    _ = original.rename(moved)
    _directory_link(original, outside)

    # When a write follows the post-bind path swap.
    try:
        with pytest.raises(ResourceManifestError) as refusal:
            _ = store.write(ResourceManifestUpdate(expected_revision=1))
    finally:
        if original.is_symlink():
            original.unlink()
        elif os.name == "nt" and original.exists():
            os.rmdir(original)
        if moved.exists():
            shutil.rmtree(moved)

    # Then the store refuses the redirected namespace.
    assert refusal.value.kind is ResourceManifestErrorKind.UNSAFE_PATH
    assert not (outside / "resource-manifest.v1.json").exists()


def test_bound_report_directory_accepts_non_reparse_attribute_churn(
    tmp_path: Path,
) -> None:
    # Given an unchanged report directory whose non-reparse Windows attribute changed.
    report_dir = tmp_path / "reports" / "run-safe"
    report_dir.mkdir(parents=True)
    binding = bind_resource_directory(report_dir, report_dir.parent)
    churned = ResourceDirectoryBinding(
        binding.path,
        binding.device,
        binding.inode,
        binding.mode,
        binding.attributes ^ 0x10000000,
    )

    # When the bound directory identity is revalidated.
    resolved = require_bound_resource_directory(churned)

    # Then irrelevant attribute churn does not fabricate a replacement.
    assert resolved == binding.path


@pytest.mark.skipif(
    os.name != "nt", reason="Windows path aliases are platform-specific"
)
def test_task5_export_preserves_short_output_identity(tmp_path: Path) -> None:
    # Given Task 5's public finalizer and a Task 16 hook share an 8.3 report alias.
    store = manifest_store(tmp_path)
    get_short_path = (
        __import__("ctypes").WinDLL("kernel32", use_last_error=True).GetShortPathNameW
    )
    required = get_short_path(str(store.path.parent), None, 0)
    assert required > 0
    ctypes = __import__("ctypes")
    buffer = ctypes.create_unicode_buffer(required)
    assert get_short_path(str(store.path.parent), buffer, required) > 0
    short_report = Path(buffer.value)
    if short_report == store.path.parent:
        pytest.skip("the test volume did not expose an 8.3 alias")
    hooks = FinalizationHooks(
        post_cleanup_manifest=resource_manifest_finalization_hook(store, short_report)
    )

    # When finalization runs through the unchanged Task 5 export.
    result = finalize_run(
        finalization_request(short_report, FinalizerScenario(hooks=hooks))
    )

    # Then the export identity and caller-supplied output spelling are preserved.
    assert finalize_run is task5_finalize_run
    assert result.summary.output_dir == str(short_report)
    assert "resource_manifest_json" in result.summary.telemetry_paths
