"""Central migration route constants and helpers."""

from __future__ import annotations

from collections.abc import Mapping


ORDINARY_CUDA = "ordinary_cuda"
CUSTOM_OP = "custom_op"
CUSTOM_OP_WITH_VARIANTS = "custom_op_with_variants"
VLLM_SERVING = "vllm_serving"
SGLANG_SERVING = "sglang_serving"

MIGRATION_ROUTES = (
    ORDINARY_CUDA,
    CUSTOM_OP,
    CUSTOM_OP_WITH_VARIANTS,
    VLLM_SERVING,
    SGLANG_SERVING,
)

SERVING_ROUTES = (VLLM_SERVING, SGLANG_SERVING)
SERVING_ENTRY_KINDS = ("vllm_serving_validation", "sglang_serving_validation")

ROUTE_TO_SERVING_FRAMEWORK = {
    VLLM_SERVING: "vllm",
    SGLANG_SERVING: "sglang",
}

SERVING_ENTRY_KIND_TO_ROUTE = {
    "vllm_serving_validation": VLLM_SERVING,
    "sglang_serving_validation": SGLANG_SERVING,
}


def is_serving_route(route: object) -> bool:
    return isinstance(route, str) and route in SERVING_ROUTES


def serving_framework_for_route(route: object) -> str | None:
    if not isinstance(route, str):
        return None
    return ROUTE_TO_SERVING_FRAMEWORK.get(route)


def serving_route_for_entry_kind(entry_script_kind: object) -> str | None:
    if not isinstance(entry_script_kind, str):
        return None
    return SERVING_ENTRY_KIND_TO_ROUTE.get(entry_script_kind)


def serving_route_from_contract(contract: Mapping[str, object]) -> str | None:
    route = serving_route_for_entry_kind(contract.get("entry_script_kind"))
    if route is not None:
        return route
    route_value = contract.get("migration_route")
    if isinstance(route_value, str) and is_serving_route(route_value):
        return route_value
    return None


def serving_entry_kind_for_route(route: object) -> str | None:
    for entry_kind, candidate_route in SERVING_ENTRY_KIND_TO_ROUTE.items():
        if route == candidate_route:
            return entry_kind
    return None
