from __future__ import annotations

from pathlib import Path

from core.resource_manifest import (
    FactProvenance,
    capture_local_environment,
)
from harness.run import FinalizationHooks, finalize_run
from harness.run.resource_manifest_hook import resource_manifest_finalization_hook
from tests.resource_manifest_test_support import manifest_store
from tests.run_finalizer_test_support import FinalizerScenario, finalization_request


def test_real_local_probe_records_bounded_runtime_without_package_listing() -> None:
    # Given the Python process running the Task 16 launcher.
    # When the framework captures its real base or virtual environment.
    result = capture_local_environment("launcher-python")

    # Then all required runtime facts are observed and only inventory hash is stored.
    facts = result.environment.facts
    names = {fact.name for fact in facts}
    assert "interpreter.realpath" in names
    assert "interpreter.sys_executable" in names
    assert "interpreter.sys_prefix" in names
    assert "interpreter.sys_base_prefix" in names
    assert "packages.inventory_sha256" in names
    assert all("==" not in (fact.value or "") for fact in facts)
    assert all(
        fact.provenance is FactProvenance.FRAMEWORK_OBSERVED
        for fact in result.receipt.verified_facts
    )


def test_task5_hook_seals_manifest_without_changing_outcome(tmp_path: Path) -> None:
    # Given an unsealed resource manifest in the same external report directory.
    store = manifest_store(tmp_path)
    hooks = FinalizationHooks(
        post_cleanup_manifest=resource_manifest_finalization_hook(store)
    )

    # When Task 5 finalization invokes its post-cleanup manifest seam.
    result = finalize_run(
        finalization_request(
            store.path.parent,
            FinalizerScenario(hooks=hooks),
        )
    )

    # Then workflow outcome stays PASS and the sealed resource artifact is receipted.
    assert result.exit_code == 0
    assert result.summary.overall_status == "PASS"
    assert result.summary.telemetry_paths["resource_manifest_json"] == str(store.path)
    assert store.read().sealed is True


def test_duplicate_hook_seal_is_diagnostic_not_outcome_mutation(
    tmp_path: Path,
) -> None:
    # Given a successful finalization that already sealed the resource manifest.
    store = manifest_store(tmp_path)
    hook = resource_manifest_finalization_hook(store)
    _ = finalize_run(
        finalization_request(
            store.path.parent,
            FinalizerScenario(hooks=FinalizationHooks(post_cleanup_manifest=hook)),
        )
    )

    # When finalization is repeated against the sealed manifest.
    repeated = finalize_run(
        finalization_request(
            store.path.parent,
            FinalizerScenario(hooks=FinalizationHooks(post_cleanup_manifest=hook)),
        )
    )

    # Then Task 5 reports the sidecar failure but preserves the frozen PASS outcome.
    assert repeated.exit_code == 0
    assert repeated.summary.overall_status == "PASS"
    assert repeated.diagnostics[-1].detail.startswith("sealed:")
