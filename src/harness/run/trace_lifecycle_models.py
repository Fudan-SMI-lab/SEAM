from __future__ import annotations

import typing


class TraceLifecycleStatus(typing.NamedTuple):
    requested: bool
    enabled: bool
    complete: bool
    path: typing.Optional[str]
    errors: typing.Tuple[str, ...]


TRACE_NOT_REQUESTED = TraceLifecycleStatus(
    requested=False,
    enabled=False,
    complete=False,
    path=None,
    errors=(),
)

TraceStatusSource = typing.Callable[[], TraceLifecycleStatus]


__all__ = ("TRACE_NOT_REQUESTED", "TraceLifecycleStatus", "TraceStatusSource")
