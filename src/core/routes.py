"""Central migration route constants and helpers."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import cast

from core.ascend_runtime import merge_serving_runtime_contract, write_serving_validation_wrapper
from core.platform_policy import PlatformPolicy


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


SERVING_REQUIRED_CHECKS = (
    "project_demo_or_test_execution",
    "serving_api_request_validation",
    "readiness_probe_passed",
    "npu_execution_evidence",
    "no_cuda_fallback",
    "no_cpu_fallback",
    "fresh_serving_report",
    "route_framework_match",
)

GENERIC_SERVING_REQUIRED_CHECKS = tuple(
    "accelerator_execution_evidence" if check == "npu_execution_evidence"
    else "no_forbidden_runtime_fallback" if check == "no_cuda_fallback"
    else check
    for check in SERVING_REQUIRED_CHECKS
)

SERVING_VALIDATION_OBLIGATIONS = (
    "actual_project_demo_test_or_api_validation",
    "npu_execution_evidence",
    "reject_import_only_or_smoke_only",
    "reject_cuda_or_cpu_fallback",
    "fresh_report_paths",
    "route_framework_match",
)

GENERIC_SERVING_VALIDATION_OBLIGATIONS = tuple(
    "accelerator_execution_evidence" if obligation == "npu_execution_evidence"
    else "reject_forbidden_runtime_or_cpu_fallback" if obligation == "reject_cuda_or_cpu_fallback"
    else obligation
    for obligation in SERVING_VALIDATION_OBLIGATIONS
)

CUSTOM_OP_PHASE3_ONLY_FIELDS = (
    "reports_dir",
    "operator_discovery_sources",
    "operator_inventory_schema",
    "validation_obligations",
    "expanded_variant_inventory",
    "variant_axis_coverage",
    "per_variant_performance_report",
)




def _policy_backend(platform_policy: PlatformPolicy | None) -> str:
    if platform_policy is None:
        return "generic"
    return platform_policy.serving_runtime.backend


def _resolve_serving_backend(
    explicit_backend: object,
    platform_policy: PlatformPolicy | None,
) -> str:
    policy_backend = _policy_backend(platform_policy)
    if isinstance(explicit_backend, str) and explicit_backend.strip():
        backend = explicit_backend.strip()
        if backend in {"vllm", "sglang"} and platform_policy is not None:
            return policy_backend
        return backend
    return policy_backend


def serving_required_checks_for_backend(backend: str) -> tuple[str, ...]:
    if backend == "ascend":
        return SERVING_REQUIRED_CHECKS
    return GENERIC_SERVING_REQUIRED_CHECKS


def serving_validation_obligations_for_backend(backend: str) -> tuple[str, ...]:
    if backend == "ascend":
        return SERVING_VALIDATION_OBLIGATIONS
    return GENERIC_SERVING_VALIDATION_OBLIGATIONS


def normalize_serving_phase1_surface(
    output: dict[str, object],
    *,
    platform_policy: PlatformPolicy | None = None,
) -> None:
    route_value = output.get("migration_route")
    if not isinstance(route_value, str) or not is_serving_route(route_value):
        return
    framework = serving_framework_for_route(route_value)
    if framework is None:
        return
    surface_value = output.get("serving_runtime_surface")
    if isinstance(surface_value, Mapping):
        surface = dict(cast(Mapping[str, object], surface_value))
    else:
        surface = {}
    surface["serving_framework"] = framework
    backend = _resolve_serving_backend(surface.get("serving_backend"), platform_policy)
    surface["serving_backend"] = backend
    if "detection_complete" not in surface:
        surface["detection_complete"] = True
    merge_serving_runtime_contract(surface, route_value, backend)
    output["serving_runtime_surface"] = surface

def normalize_serving_phase3_contract(
    contract: dict[str, object],
    *,
    route: str,
    project_dir: str | Path,
    phase1_output: Mapping[str, object] | None = None,
    platform_policy: PlatformPolicy | None = None,
) -> None:
    entry_kind = serving_entry_kind_for_route(route)
    framework = serving_framework_for_route(route)
    if entry_kind is None or framework is None:
        return

    for field in CUSTOM_OP_PHASE3_ONLY_FIELDS:
        _ = contract.pop(field, None)

    project_path = Path(project_dir).expanduser().resolve(strict=False)
    contract["project_dir"] = str(project_path)
    surface: Mapping[str, object] = {}
    if phase1_output is not None:
        maybe_surface = phase1_output.get("serving_runtime_surface")
        if isinstance(maybe_surface, Mapping):
            surface = cast(Mapping[str, object], maybe_surface)

    for field in (
        "launch_command",
        "readiness_probe",
        "request_validation",
        "project_test_files",
        "expected_outputs",
        "required_runtime_env",
    ):
        value = surface.get(field)
        if value and not contract.get(field):
            contract[field] = value

    launch_command = contract.get("launch_command")
    if not launch_command and phase1_output is not None:
        entry_command = phase1_output.get("entry_command")
        if isinstance(entry_command, str) and entry_command.strip():
            launch_command = entry_command
            contract["launch_command"] = entry_command
    service_launch_command = str(launch_command or contract.get("launch_command") or "")

    explicit_backend = contract.get("serving_backend") or surface.get("serving_backend")
    backend = _resolve_serving_backend(explicit_backend, platform_policy)
    merge_serving_runtime_contract(contract, route, backend)
    required_checks = list(serving_required_checks_for_backend(backend))
    contract["required_checks"] = required_checks
    contract["serving_reports_dir"] = str(project_path / "migration_reports" / "serving")
    contract["required_report_paths"] = ["migration_reports/serving/serving_final_gate.json"]
    contract["serving_validation_obligations"] = list(serving_validation_obligations_for_backend(backend))

    if not contract.get("project_test_files"):
        contract["project_test_files"] = ["project-provided serving demo/test/API path from Phase 1 serving surface"]
    if not contract.get("expected_outputs"):
        contract["expected_outputs"] = ["serving endpoint returns a successful project response"]
    if not isinstance(contract.get("readiness_probe"), Mapping):
        contract["readiness_probe"] = {"type": "http", "success_condition": "serving endpoint becomes ready"}
    if not isinstance(contract.get("request_validation"), Mapping):
        contract["request_validation"] = {"type": "project_demo_or_api_request", "success_condition": "project request succeeds"}

    script_path = write_serving_validation_wrapper(
        project_dir=project_path,
        route=route,
        backend=backend,
        launch_command=service_launch_command,
        readiness_probe=contract.get("readiness_probe"),
        request_validation=contract.get("request_validation"),
        project_test_files=contract.get("project_test_files"),
        expected_outputs=contract.get("expected_outputs"),
        required_checks=required_checks,
    )
    contract["entry_script_path"] = str(script_path)
    venv_python = project_path / ".venv" / "bin" / "python"
    wrapper_command = f"{venv_python} {script_path}"
    contract["run_command"] = wrapper_command
    contract["launch_command"] = wrapper_command
    if service_launch_command:
        contract["service_launch_command"] = service_launch_command

    contract["entry_script_kind"] = entry_kind
    contract["migration_route"] = route
    contract["serving_framework"] = framework
