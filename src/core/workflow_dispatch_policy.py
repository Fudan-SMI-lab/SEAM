from __future__ import annotations

from collections.abc import Mapping
from typing import NamedTuple

from core.compat import TypeAlias


RouteScalar: TypeAlias = "str | int | float | bool | None"


class DispatchDecision(NamedTuple):
    route_key: str
    target: str | None
    available_routes: tuple[str, ...]


def select_dispatch_route(
    resolved_route: RouteScalar | Mapping[str, RouteScalar],
    parameter_routes: Mapping[str, str],
    transition_routes: Mapping[str, str],
) -> DispatchDecision:
    if isinstance(resolved_route, Mapping):
        route_key = str(resolved_route.get("value", resolved_route.get("role", "")))
    else:
        route_key = str(resolved_route)
    routes = parameter_routes or transition_routes
    return DispatchDecision(route_key, routes.get(route_key), tuple(routes))
