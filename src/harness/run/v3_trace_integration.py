from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn, Protocol

from harness.session.trace_export_models import TraceGraphClient
from harness.session.trace_seeds import TraceSeed
from harness.run.trace_correlation import (
    V3TraceCorrelationInputs,
    build_v3_trace_correlation,
)

from .trace_lifecycle import (
    TraceCapturePolicy,
    TraceLifecycle,
    TraceLifecycleRequest,
    TraceTelemetrySink,
)


class TraceSourceInvariantError(RuntimeError):
    pass


class TraceSessionSource(Protocol):
    @property
    def trace_client(self) -> TraceGraphClient: ...

    @property
    def trace_seeds(self) -> tuple[TraceSeed, ...]: ...


@dataclass(frozen=True, slots=True)
class V3TraceIntegrationRequest:
    cli_value: bool | None
    destination: Path
    session: TraceSessionSource | None
    overflow_roots: tuple[Path, ...]
    telemetry: TraceTelemetrySink | None
    correlation_inputs: V3TraceCorrelationInputs | None = None


def create_v3_trace_lifecycle(
    request: V3TraceIntegrationRequest,
) -> TraceLifecycle:
    session = request.session
    if session is None:

        def unavailable_client() -> NoReturn:
            raise TraceSourceInvariantError("empty trace seeds must bypass the client")

        def trace_seeds() -> tuple[TraceSeed, ...]:
            return ()

        client_source = unavailable_client
        seeds_source = trace_seeds
    else:

        def trace_client() -> TraceGraphClient:
            return session.trace_client

        def trace_seeds() -> tuple[TraceSeed, ...]:
            return session.trace_seeds

        client_source = trace_client
        seeds_source = trace_seeds
    correlation_inputs = request.correlation_inputs
    return TraceLifecycle(
        TraceLifecycleRequest(
            policy=TraceCapturePolicy.from_cli(request.cli_value),
            destination=request.destination,
            client_source=client_source,
            seeds_source=seeds_source,
            overflow_roots=request.overflow_roots,
            telemetry=request.telemetry,
            correlation_source=(
                (lambda: build_v3_trace_correlation(correlation_inputs))
                if correlation_inputs is not None
                else None
            ),
        )
    )


__all__ = (
    "V3TraceCorrelationInputs",
    "V3TraceIntegrationRequest",
    "create_v3_trace_lifecycle",
)
