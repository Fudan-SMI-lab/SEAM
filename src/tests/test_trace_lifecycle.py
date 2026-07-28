from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import harness.run as run
from tests.run_finalizer_test_support import FinalizerScenario, finalization_request

from tests.trace_lifecycle_export_cases import (
    test_disabled_trace_performs_no_client_or_seed_work as test_disabled_trace_performs_no_client_or_seed_work,
    test_enabled_trace_exports_every_registered_seed as test_enabled_trace_exports_every_registered_seed,
    test_enabled_trace_without_registered_seeds_is_incomplete as test_enabled_trace_without_registered_seeds_is_incomplete,
    test_incomplete_child_is_reported_without_claiming_complete as test_incomplete_child_is_reported_without_claiming_complete,
    test_trace_telemetry_failure_is_outcome_neutral as test_trace_telemetry_failure_is_outcome_neutral,
)
from tests.trace_lifecycle_finalization_cases import (
    test_exporter_exception_preserves_frozen_outcome_and_continuation_seal as test_exporter_exception_preserves_frozen_outcome_and_continuation_seal,
    test_final_telemetry_persists_session_and_server_cleanup_diagnostics as test_final_telemetry_persists_session_and_server_cleanup_diagnostics,
    test_only_requested_container_cleanup_failure_maps_pass_to_exit_two as test_only_requested_container_cleanup_failure_maps_pass_to_exit_two,
    test_trace_runs_before_cleanup_and_final_telemetry as test_trace_runs_before_cleanup_and_final_telemetry,
)


def test_summary_reports_trace_status_without_migration_errors(
    tmp_path: Path,
) -> None:
    # Given an incomplete raw trace status and unrelated migration errors.
    trace_status = run.TraceLifecycleStatus(
        requested=True,
        enabled=True,
        complete=False,
        path=str(tmp_path / "trace" / "manifest.json"),
        errors=("incomplete_child",),
    )
    request = finalization_request(
        tmp_path,
        FinalizerScenario(errors=("migration display error",)),
    )
    request = replace(request, trace_status_source=lambda: trace_status)

    # When the frozen outcome is finalized.
    result = run.finalize_run(request)

    # Then trace facts are separate and migration errors remain unchanged.
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["trace"] == {
        "requested": True,
        "enabled": True,
        "complete": False,
        "path": str(tmp_path / "trace" / "manifest.json"),
        "errors": ["incomplete_child"],
    }
    assert result.summary.trace == trace_status
    assert result.summary.errors == ("migration display error",)
