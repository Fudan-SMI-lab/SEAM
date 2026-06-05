"""Central migration route constants and helpers.

This module is the single source of truth for route names, route→framework
mappings, custom-op contract field keys, and route classification utilities.
All other modules import from here instead of maintaining private copies.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import cast


# ── Route name constants ────────────────────────────────────────────────
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

# Internal mutable backing — updated by register_serving_route()
_SERVING_ROUTES: list[str] = [VLLM_SERVING, SGLANG_SERVING]
_SERVING_ENTRY_KINDS: list[str] = ["vllm_serving_validation", "sglang_serving_validation"]

# Exposed tuples for backward-compatible read-only access
SERVING_ROUTES: tuple[str, ...] = tuple(_SERVING_ROUTES)
SERVING_ENTRY_KINDS: tuple[str, ...] = tuple(_SERVING_ENTRY_KINDS)

# ── Route → framework mapping (canonical → import from here) ─────────────
ROUTE_TO_SERVING_FRAMEWORK = {
    VLLM_SERVING: "vllm",
    SGLANG_SERVING: "sglang",
}

SERVING_ENTRY_KIND_TO_ROUTE = {
    "vllm_serving_validation": VLLM_SERVING,
    "sglang_serving_validation": SGLANG_SERVING,
}

# ── Serving route registration API ─────────────────────────────────────────

def register_serving_route(
    route_name: str,
    entry_kind: str,
    framework: str,
) -> None:
    """Register a new serving route dynamically.

    Appends *route_name* to :data:`SERVING_ROUTES`, *entry_kind* to
    :data:`SERVING_ENTRY_KINDS`, and adds corresponding entries to
    :data:`ROUTE_TO_SERVING_FRAMEWORK` and
    :data:`SERVING_ENTRY_KIND_TO_ROUTE`.
    """
    global SERVING_ROUTES, SERVING_ENTRY_KINDS

    _SERVING_ROUTES.append(route_name)
    _SERVING_ENTRY_KINDS.append(entry_kind)
    ROUTE_TO_SERVING_FRAMEWORK[route_name] = framework
    SERVING_ENTRY_KIND_TO_ROUTE[entry_kind] = route_name

    # Rebuild exposed tuples so module-level access sees the new entries
    SERVING_ROUTES = tuple(_SERVING_ROUTES)
    SERVING_ENTRY_KINDS = tuple(_SERVING_ENTRY_KINDS)

# ── Custom-op contract field keys (canonical → import from here) ──────────
# These are the field names that are specific to custom-op routes and should
# be stripped from non-custom-op (serving / ordinary_cuda) contracts.
CUSTOM_OP_CONTRACT_KEYS = frozenset({
    "entry_script_kind",
    "reports_dir",
    "required_report_paths",
    "required_checks",
    "operator_discovery_sources",
    "operator_inventory_schema",
    "performance_report_schema",
    "validation_obligations",
    "phase5_entry_script_revision_allowed",
})

# ── Prompt fallback suffixes (platform-agnostic, used by prompt loader) ───
# These are suffixes tried when a phase's primary prompt template is
# missing.  PlatformPolicy presets extend this list; the list here is the
# framework default (only used when no PlatformPolicy is active).
import os as _os_module

DEFAULT_PROMPT_FALLBACK_SUFFIXES: tuple[str, ...] = tuple(
    s.strip()
    for s in _os_module.environ.get(
        "SEAM_PROMPT_FALLBACK_SUFFIXES", "_npu,_ppu,_musa,_rocm,_mlu"
    ).split(",")
    if s.strip()
)

# ── Framework-level serving env defaults ──────────────────────────────────
# Per-framework env-var defaults that are injected by the serving validation
# wrapper.  "FRAMEWORK" in serving_runtime.py is the key.
FRAMEWORK_SERVING_ENV_DEFAULTS: dict[str, dict[str, str]] = {
    "sglang": {"SGLANG_ENABLE_SPEC_V2": "1"},
    "vllm": {},
}

# ── Framework-level forbidden runtime markers ────────────────────────────
# Substrings that, when found in serving command output, indicate an
# unwanted runtime fallback.  PlatformPolicy can override these per
# platform; these defaults are framework-generic.
FRAMEWORK_FORBIDDEN_RUNTIME_MARKERS: dict[str, tuple[str, ...]] = {
    VLLM_SERVING: ("vllm cuda executor",),
    SGLANG_SERVING: ("deep_gemm_wrapper", "pynccl", "nccl", "cuda_graph"),
}

from core.serving_runtime import write_serving_validation_wrapper
from validators.serving_validator import GENERIC_SERVING_REQUIRED_CHECKS, GENERIC_SERVING_VALIDATION_OBLIGATIONS

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from core.platform_policy import PlatformPolicy

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


CUSTOM_OP_PHASE3_ONLY_FIELDS = (
    "reports_dir",
    "operator_discovery_sources",
    "operator_inventory_schema",
    "validation_obligations",
    "expanded_variant_inventory",
    "variant_axis_coverage",
    "per_variant_performance_report",
)


def normalize_serving_phase1_surface(output: dict[str, object]) -> None:
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
    if "detection_complete" not in surface:
        surface["detection_complete"] = True
    # generic defaults for required serving runtime fields
    if not isinstance(surface.get("runtime_env_setup"), dict) or not surface.get("runtime_env_setup"):
        surface["runtime_env_setup"] = {"accelerator": "generic"}
    if not surface.get("required_import_probes"):
        surface["required_import_probes"] = ["torch"]
    if not surface.get("forbidden_runtime_markers"):
        surface["forbidden_runtime_markers"] = ["cpu fallback", "fallback to cpu"]
    if not surface.get("serving_runtime_checks"):
        surface["serving_runtime_checks"] = ["accelerator_runtime_evidence"]
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
        "runtime_env_setup",
        "required_import_probes",
        "forbidden_runtime_markers",
        "serving_runtime_checks",
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

    required_checks = list(GENERIC_SERVING_REQUIRED_CHECKS)
    contract["required_checks"] = required_checks
    contract["serving_reports_dir"] = str(project_path / "migration_reports" / "serving")
    contract["required_report_paths"] = ["migration_reports/serving/serving_final_gate.json"]
    contract["serving_validation_obligations"] = list(GENERIC_SERVING_VALIDATION_OBLIGATIONS)

    if not contract.get("project_test_files"):
        contract["project_test_files"] = ["project-provided serving demo/test/API path from Phase 1 serving surface"]
    if not contract.get("expected_outputs"):
        contract["expected_outputs"] = ["serving endpoint returns a successful project response"]
    if not isinstance(contract.get("readiness_probe"), Mapping):
        contract["readiness_probe"] = {"type": "http", "success_condition": "serving endpoint becomes ready"}
    if not isinstance(contract.get("request_validation"), Mapping):
        contract["request_validation"] = {"type": "project_demo_or_api_request", "success_condition": "project request succeeds"}
    if not isinstance(contract.get("runtime_env_setup"), dict) or not contract.get("runtime_env_setup"):
        contract["runtime_env_setup"] = surface.get("runtime_env_setup", {"accelerator": "generic"})
    if not contract.get("required_import_probes"):
        contract["required_import_probes"] = surface.get("required_import_probes", ["torch"])
    if not contract.get("forbidden_runtime_markers"):
        contract["forbidden_runtime_markers"] = surface.get("forbidden_runtime_markers", ["cpu fallback", "fallback to cpu"])
    if not contract.get("serving_runtime_checks"):
        contract["serving_runtime_checks"] = surface.get("serving_runtime_checks", ["accelerator_runtime_evidence"])

    script_path = write_serving_validation_wrapper(
        project_dir=project_path,
        route=route,
        launch_command=service_launch_command,
        readiness_probe=contract.get("readiness_probe"),
        request_validation=contract.get("request_validation"),
        project_test_files=contract.get("project_test_files"),
        expected_outputs=contract.get("expected_outputs"),
        required_checks=required_checks,
        platform_policy=platform_policy,
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


# ── Container path normalization (shared between phase_runner and workflow_executor) ──


def rewrite_container_to_host_path(
    path_str: str,
    project_dir: str,
    container_workdir: str,
) -> str:
    """Convert a container-visible path to its host-visible equivalent.

    When a model returns a path under ``container_workdir`` (e.g.
    ``/workspace/validate_fwi.py``), return the corresponding path under
    ``project_dir`` (e.g. ``{project_dir}/validate_fwi.py``).

    Boundary-safe: ``/workspace2/x`` does NOT match container workdir ``/workspace``.
    """
    if not path_str:
        return path_str
    safe = container_workdir.rstrip("/")
    if not safe:
        return path_str
    if not (path_str == safe or path_str.startswith(safe + "/")):
        return path_str
    rel = path_str[len(safe):].lstrip("/")
    if not rel:
        return project_dir
    return str(Path(project_dir) / rel)


def normalize_phase3_container_paths(
    output: dict[str, object],
    prompt_context: dict[str, object],
    *,
    fallback_project_dir: str | None = None,
) -> dict[str, object]:
    """Rewrite host-visible path fields when the model returns container paths.

    Only targets ``entry_script_path`` and ``reports_dir``.  ``run_command``
    is NOT rewritten here — the Phase 5 execution backend already handles
    host-to-container path mapping for command execution.

    When the model returns a path that starts with the container workdir (e.g.
    ``/workspace/...`` or the value of ``{container_project_dir}``), convert it
    to the corresponding host-visible path under ``{project_dir}``.
    """
    project_dir = str(prompt_context.get("project_dir", "")) or fallback_project_dir
    container_workdir = (
        str(prompt_context.get("container_workdir", ""))
        or str(prompt_context.get("container_project_dir", ""))
    )
    if not project_dir or not container_workdir:
        return output

    if not project_dir.startswith("/"):
        try:
            project_dir = str(Path(project_dir).resolve())
        except OSError:
            return output

    normalized = dict(output)

    entry = normalized.get("entry_script_path")
    if isinstance(entry, str) and entry.strip():
        normalized["entry_script_path"] = rewrite_container_to_host_path(
            entry, project_dir, container_workdir,
        )

    reports = normalized.get("reports_dir")
    if isinstance(reports, str) and reports.strip():
        normalized["reports_dir"] = rewrite_container_to_host_path(
            reports, project_dir, container_workdir,
        )

    return normalized
