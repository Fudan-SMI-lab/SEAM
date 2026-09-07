from __future__ import annotations

from core.compat import assert_never

from core.continuation_models import SummaryStatus
from core.run_outcome import TerminalOutcome

from .models import RunArtifacts, RunFinalizationRequest, RunSummary
from .trace_lifecycle_models import TRACE_NOT_REQUESTED, TraceLifecycleStatus


def read_trace_status(request: RunFinalizationRequest) -> TraceLifecycleStatus:
    source = request.trace_status_source
    return source() if source is not None else TRACE_NOT_REQUESTED


def build_summary(
    request: RunFinalizationRequest,
    artifacts: RunArtifacts,
    outcome: TerminalOutcome,
    trace: TraceLifecycleStatus,
) -> RunSummary:
    identity = request.identity
    execution = request.execution
    total_duration_seconds = (
        execution.total_duration_seconds
        if execution.duration_source is None
        else execution.duration_source()
    )
    if outcome is TerminalOutcome.FAILED:
        overall_status = SummaryStatus.FAIL
    elif (
        outcome is TerminalOutcome.PASSED
        or outcome is TerminalOutcome.PASSED_WITH_REVIEWS
    ):
        overall_status = SummaryStatus.PASS
    else:
        assert_never(outcome)
    return RunSummary(
        run_id=str(identity.run_id),
        base_url=identity.base_url,
        workflow_path=identity.workflow_path,
        output_dir=identity.output_dir,
        temp_dir=identity.temp_dir,
        keep_temp_dir=execution.keep_temp_dir,
        requested_max_phase5_iter=execution.requested_max_phase5_iter,
        effective_max_phase5_iter=execution.effective_max_phase5_iter,
        phases=execution.phases,
        session_count=execution.session_count,
        command_count=execution.command_count,
        overall_status=overall_status,
        total_duration_seconds=round(total_duration_seconds, 3),
        artifact_dir=artifacts.artifact_dir,
        telemetry_paths=dict(artifacts.telemetry_paths),
        before_snapshot_path=artifacts.before_snapshot_path,
        after_snapshot_path=artifacts.after_snapshot_path,
        entry_script=artifacts.entry_script,
        errors=execution.errors,
        trace=trace,
        review_timeout_observability=request.observability,
    )


__all__ = ("build_summary", "read_trace_status")
