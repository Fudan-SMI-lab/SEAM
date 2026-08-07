from __future__ import annotations

import typing


class TraceCorrelationSummary(typing.NamedTuple):
    schema_version: int
    complete: bool
    run_id: str
    parent_run_id: typing.Optional[str]
    lineage_root_run_id: str
    diagnostics: typing.Tuple[str, ...]


class TraceLifecycleStatus(typing.NamedTuple):
    requested: bool
    enabled: bool
    complete: bool
    path: typing.Optional[str]
    errors: typing.Tuple[str, ...]
    correlation: typing.Optional[TraceCorrelationSummary] = None


TRACE_NOT_REQUESTED = TraceLifecycleStatus(
    requested=False,
    enabled=False,
    complete=False,
    path=None,
    errors=(),
)

TraceStatusSource = typing.Callable[[], TraceLifecycleStatus]


__all__ = (
    "TRACE_NOT_REQUESTED",
    "TraceCorrelationSummary",
    "TraceLifecycleStatus",
    "TraceStatusSource",
)
