from __future__ import annotations

import sys

if sys.version_info >= (3, 10):
    from core.run_outcome import RunOutcome, TerminalOutcome
    from .cleanup import CleanupContext, ResourceCleanup
    from .finalizer import (
        allocate_report_directory,
        build_run_summary,
        finalize_run,
    )
    from .models import (
        ContinuationRunSummary,
        EMPTY_ARTIFACT_UPDATE,
        FinalizationDiagnostic,
        FinalizationHook,
        FinalizationHookError,
        FinalizationHooks,
        FinalizationResult,
        FinalizationStage,
        PhaseStatus,
        ReportAllocationError,
        ReportAllocationErrorKind,
        RunArtifacts,
        RunArtifactUpdate,
        RunExecution,
        RunFinalizationRequest,
        RunIdentity,
        RunSummary,
        SidecarWriteError,
    )
    from .resource_manifest_hook import resource_manifest_finalization_hook
    from .sidecars import copy_run_artifacts, write_json_text
    from .trace_lifecycle_models import TraceLifecycleStatus
    from .trace_lifecycle import (
        TraceCapturePolicy,
        TraceLifecycle,
        TraceLifecycleRequest,
        compose_trace_hooks,
    )
    from .v3_lifecycle import (
        BridgeSidecar,
        EvidenceContext,
        EvidencePersister,
        ObserverSidecar,
        RunCounts,
        SnapshotResult,
        TelemetrySidecars,
        V3RunLifecycle,
        V3TelemetrySources,
        build_telemetry_sidecars,
        persist_python_snapshot,
    )

    __all__ = (
        "EMPTY_ARTIFACT_UPDATE",
        "BridgeSidecar",
        "CleanupContext",
        "ContinuationRunSummary",
        "EvidenceContext",
        "EvidencePersister",
        "FinalizationDiagnostic",
        "FinalizationHook",
        "FinalizationHookError",
        "FinalizationHooks",
        "FinalizationResult",
        "FinalizationStage",
        "ObserverSidecar",
        "PhaseStatus",
        "ReportAllocationError",
        "ReportAllocationErrorKind",
        "ResourceCleanup",
        "RunArtifactUpdate",
        "RunArtifacts",
        "RunCounts",
        "RunExecution",
        "RunFinalizationRequest",
        "RunIdentity",
        "RunOutcome",
        "RunSummary",
        "SidecarWriteError",
        "SnapshotResult",
        "TelemetrySidecars",
        "TerminalOutcome",
        "TraceCapturePolicy",
        "TraceLifecycle",
        "TraceLifecycleRequest",
        "TraceLifecycleStatus",
        "V3RunLifecycle",
        "V3TelemetrySources",
        "allocate_report_directory",
        "build_run_summary",
        "build_telemetry_sidecars",
        "copy_run_artifacts",
        "compose_trace_hooks",
        "finalize_run",
        "persist_python_snapshot",
        "resource_manifest_finalization_hook",
        "write_json_text",
    )
else:
    from .finalization_contract import (
        ContinuationRunSummary,
        FinalizationHooks,
        FinalizationResult,
        FinalizationStage,
        PhaseStatus,
        RunArtifacts,
        RunArtifactUpdate,
        RunExecution,
        RunFinalizationRequest,
        RunIdentity,
        RunOutcome,
        RunSummary,
        TerminalOutcome,
    )
    from .finalizer_py38 import finalize_run
    from .resource_manifest_hook import resource_manifest_finalization_hook
    from .trace_lifecycle_models import TraceLifecycleStatus

    __all__ = (
        "ContinuationRunSummary",
        "FinalizationHooks",
        "FinalizationResult",
        "FinalizationStage",
        "PhaseStatus",
        "RunArtifacts",
        "RunArtifactUpdate",
        "RunExecution",
        "RunFinalizationRequest",
        "RunIdentity",
        "RunOutcome",
        "RunSummary",
        "TerminalOutcome",
        "TraceLifecycleStatus",
        "finalize_run",
        "resource_manifest_finalization_hook",
    )
