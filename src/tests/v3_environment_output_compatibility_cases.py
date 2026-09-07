from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from core.execution_env_context import (
    Phase2EnvironmentReport,
    Phase2EnvironmentRequest,
)
from core.execution_env_records import build_phase2_environment
from core.resource_retention import ContainerCleanupStatus
from core.v3_runtime_report import RuntimeReportRequest
from harness.run import (
    FinalizationHooks,
    FinalizationStage,
    RunArtifactUpdate,
    finalize_run,
)
from harness.run.v3_runtime_reporting import V3RuntimeReportRecorder
from tests.run_finalizer_test_support import FinalizerScenario, finalization_request
from tests.v3_environment_output_test_support import (
    RUN_ID,
    runtime_store,
    seal_lifecycle,
)


class _TelemetryUnavailable(RuntimeError):
    pass


def test_phase2_base_environment_accepts_vendor_metadata_without_venv_claim() -> None:
    # Given the actual base-aware Phase 2 output shape with unrelated vendor metadata.
    report = Phase2EnvironmentReport.model_validate(
        {
            "env_type": "base_env",
            "venv_path": "/usr/local",
            "python_path": "/usr/local/bin/python",
            "installed_packages": ["torch==2.8.0"],
            "vendor_stack": {"api_mode": "cuda_compatible"},
            "decision_reason": "vendor runtime already available",
        }
    )

    # When the report becomes a qualified environment record.
    environment = build_phase2_environment(
        Phase2EnvironmentRequest(
            environment_id="phase2-base",
            namespace="host",
            report=report,
        )
    )

    # Then base selection is preserved rather than fabricated as a project venv.
    environment_type = next(
        fact.value for fact in environment.facts if fact.name == "environment.type"
    )
    assert environment_type == "base"


def test_legacy_schema_v1_manifest_without_new_container_facts_remains_readable(
    tmp_path: Path,
) -> None:
    # Given a pre-Task19 schema-v1 manifest without optional display facts.
    store = runtime_store(tmp_path)
    payload = json.loads(store.path.read_text(encoding="utf-8"))
    optional = {
        "container.name",
        "container.workdir",
        "container.mount_source",
        "container.mount_destination",
    }
    payload["facts"] = [
        fact for fact in payload["facts"] if fact["name"] not in optional
    ]
    _ = store.path.write_text(json.dumps(payload), encoding="utf-8")

    # When continuation opens the existing schema-v1 manifest.
    manifest = store.read()

    # Then additive Task 19 display fields are not retroactively mandatory.
    assert manifest.schema_version == 1


def test_throwing_runtime_telemetry_cannot_fail_required_existing_hook(
    tmp_path: Path,
) -> None:
    # Given a required post-cleanup stage with optional telemetry that throws.
    store = runtime_store(tmp_path)
    seal_lifecycle(store, cleanup=ContainerCleanupStatus.NOT_APPLICABLE)
    existing_called = False

    class ThrowingTelemetry:
        def set_metadata(self, key, value) -> None:
            del key, value
            raise _TelemetryUnavailable

        def record_event(self, event_type, **details) -> None:
            del event_type, details

    def existing(_outcome) -> RunArtifactUpdate:
        nonlocal existing_called
        existing_called = True
        return RunArtifactUpdate()

    recorder = V3RuntimeReportRecorder(
        RuntimeReportRequest(store, None, RUN_ID, None),
        ThrowingTelemetry(),
        existing,
    )
    request = finalization_request(
        store.path.parent,
        FinalizerScenario(hooks=FinalizationHooks(post_cleanup_manifest=recorder)),
    )
    request = replace(
        request,
        required_stages=frozenset({FinalizationStage.POST_CLEANUP_MANIFEST}),
        runtime_report_source=recorder.read,
    )

    # When finalization executes the composed hook.
    result = finalize_run(request)

    # Then optional reporting cannot skip the existing hook or change PASS.
    assert existing_called is True
    assert result.finalization_failed is False
    assert result.exit_code == 0
