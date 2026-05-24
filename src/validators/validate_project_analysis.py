"""Validation for Phase 1 project analysis output."""

from dataclasses import dataclass
from itertools import product
from pathlib import Path
import re
from typing import cast

from core.custom_op_variants import source_template_expanded_variants
from core.routes import MIGRATION_ROUTES, is_serving_route, serving_framework_for_route
from core.validator_engine import ValidationDict


CUSTOM_OP_SURFACE_FIELDS = (
    "custom_op_detected",
    "discovery_complete",
    "discovery_sources_checked",
    "searched_source_roots",
    "searched_source_paths",
    "operator_families",
    "fine_grained_operator_units",
    "discovered_operator_names",
    "native_operator_symbols",
    "kernel_launch_sites",
    "source_evidence",
    "negative_evidence",
    "dynamic_loading_checks",
    "build_load_checks",
    "unresolved_source_groups",
    "out_of_scope_source_groups",
    "fine_grained_operator_unit_evidence",
    "variant_axes_detected",
    "variant_axes",
    "expanded_operator_variants",
    "expanded_operator_instances_count",
)

VARIANT_METADATA_FIELDS = (
    "variant_axes_detected",
    "variant_axes",
    "expanded_operator_variants",
    "expanded_operator_instances_count",
)

NATIVE_SOURCE_SUFFIXES = {".c", ".cc", ".cpp", ".cxx", ".cu", ".cuh", ".h", ".hh", ".hpp"}
CUDA_SOURCE_SUFFIXES = {".cu", ".cuh"}
EXCLUDED_SOURCE_DIRS = {
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".sm-artifacts",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "output_projects",
    "site-packages",
    "venv",
}
MAX_DISCOVERY_FILES = 2000
MAX_DISCOVERY_BYTES = 2_000_000
CUDA_NATIVE_SUFFIXES = ("_cuda", "_gpu")
FORBIDDEN_DISCOVERY_SOURCES = {"requirements_doc"}
CUSTOM_OP_INDICATOR_SUFFIXES = NATIVE_SOURCE_SUFFIXES | {".py"}
CUSTOM_OP_SOURCE_INDICATOR_PATTERNS = (
    re.compile(r"\b(?:CUDAExtension|CppExtension)\b"),
    re.compile(r"\btorch\.utils\.cpp_extension\b"),
    re.compile(r"\bPYBIND11_MODULE\s*\("),
    re.compile(r"\bTORCH_LIBRARY(?:_IMPL)?\s*\("),
)
IMPLEMENTATION_DETAIL_AXIS_PATTERNS = (
    re.compile(r"(?:^|[_\-\s])(block|blocksize|block_size|threads?|thread_count|grid|gridsize|grid_size)(?:$|[_\-\s])", re.IGNORECASE),
    re.compile(r"(?:^|[_\-\s])(launch|wrapper|macro|check|runtime|dispatch|coverage|template|specialization|tuning|performance|heuristic)(?:$|[_\-\s])", re.IGNORECASE),
)
IMPLEMENTATION_DETAIL_EVIDENCE_PATTERNS = (
    re.compile(r"\bblock\s*[_-]?\s*size\b", re.IGNORECASE),
    re.compile(r"\b(?:thread|grid)\s*(?:heuristic|count|dim|size|block)\b", re.IGNORECASE),
    re.compile(r"\b(?:launch\s+wrapper|check\s+macro|runtime\s+(?:dtype\s+)?dispatch|dispatch\s+coverage)\b", re.IGNORECASE),
    re.compile(r"\b(?:performance[-_\s]+tuning|template\s+speciali[sz]ation)\b", re.IGNORECASE),
)
IMPLEMENTATION_DETAIL_UNIT_PATTERNS = (
    re.compile(r"(?:^|[_:])__?[a-z0-9_]*update(?:$|[_:])", re.IGNORECASE),
    re.compile(r"(?:^|[_:])(?:opt_)?n_threads(?:$|[_:])", re.IGNORECASE),
    re.compile(r"(?:^|[_:])(?:opt_)?block_config(?:$|[_:])", re.IGNORECASE),
    re.compile(r"(?:^|[_:])(?:cuda_)?check(?:_cuda|_contiguous|_errors?)?(?:$|[_:])", re.IGNORECASE),
    re.compile(r"(?:^|[_:])check_(?:cuda|contiguous|input|errors?)(?:$|[_:])", re.IGNORECASE),
    re.compile(r"(?:^|[_:])cuda_check_errors?(?:$|[_:])", re.IGNORECASE),
    re.compile(r"(?:^|[_:])\w*kernel_wrapper(?:$|[_:])", re.IGNORECASE),
    re.compile(r"(?:^|[_:])\w*_kernel(?::$|$|[_:])", re.IGNORECASE),
    re.compile(r"(?:^|[:_])block(?:_size)?=", re.IGNORECASE),
    re.compile(r"(?:^|[:_])block_size(?:$|[:_])", re.IGNORECASE),
)
COLLAPSED_VARIANT_SYNTAX_PATTERN = re.compile(r"[{}|]")
COMBINED_AXIS_VALUE_PATTERN = re.compile(r"[{}|,]|\w+\s*=")
EXTERNAL_SCOPE_PATTERN = re.compile(r"(?:^|[/:_\-\s])(?:external|benchmark|benchmarks?|third[_\-]?party|out[_\-]?of[_\-]?scope)(?:$|[/:_\-\s])", re.IGNORECASE)
SEMANTIC_VARIANT_AXIS_PATTERNS = {
    "ndim": re.compile(r"\$\{\s*ndim\s*\}|\b(?:enumerates?|generated|builds?|templates?)\b[^\n;]*\b(?:ndim|dimension|dimensionality)\b", re.IGNORECASE),
    "accuracy": re.compile(r"\$\{\s*accuracy\s*\}|\b(?:enumerates?|generated|builds?|templates?)\b[^\n;]*\baccuracy\b", re.IGNORECASE),
    "dtype": re.compile(r"\$\{\s*dtype\s*\}|\b(?:enumerates?|generated|builds?|templates?)\b[^\n;]*\b(?:dtype|float\s+and\s+double|data\s+type)\b", re.IGNORECASE),
    "layout": re.compile(r"\$\{\s*layout\s*\}|\b(?:enumerates?|generated|builds?|templates?)\b[^\n;]*\blayout\b", re.IGNORECASE),
    "mode": re.compile(r"\$\{\s*mode\s*\}|\b(?:enumerates?|generated|builds?|templates?)\b[^\n;]*\bmode\b", re.IGNORECASE),
    "device": re.compile(r"\$\{\s*device\s*\}|\b(?:generated|builds?|templates?)\b[^\n;]*\bdevice\b", re.IGNORECASE),
}
SEMANTIC_AXIS_ALIASES = {
    "ndim": ("ndim", "current_ndim", "dimension", "dimensions", "dimensionality"),
    "accuracy": ("accuracy", "current_accuracy"),
    "dtype": ("dtype", "current_dtype", "dtype_str", "data_type", "data type"),
    "layout": ("layout", "current_layout"),
    "mode": ("mode", "current_mode"),
    "device": ("device", "current_device", "device_str"),
}
TARGET_VARIANT_AXIS_LIKE_NAMES = {
    "device",
    "backend",
    "reference",
    "baseline",
    "comparison",
    "loader",
    "load",
    "symbol",
    "symbols",
}
NON_TARGET_VARIANT_AXIS_VALUES = {
    "cpu",
    "torch_cpu",
    "torch.cpu",
    "python_cpu",
    "reference",
    "baseline",
    "host",
    "ctypes",
    "symbols",
}
SEMANTIC_AXIS_NAMES_BY_ALIAS = {
    alias: axis
    for axis, aliases in SEMANTIC_AXIS_ALIASES.items()
    for alias in aliases
}
SOURCE_REFERENCE_PATTERN = re.compile(r"(?P<path>[^:\s]+\.(?:py|c|cc|cpp|cxx|cu|cuh|h|hh|hpp)):(?P<line>\d+)")
PYTHON_LITERAL_LIST_PATTERN = re.compile(
    r"\b(?P<name>ndim|current_ndim|accuracy|current_accuracy|dtype|current_dtype|dtype_str|layout|current_layout|mode|current_mode|device|current_device|device_str)\b[^\n\[]*\[(?P<values>[^\]]+)\]",
    re.IGNORECASE,
)

RETURN_TYPE_PATTERN = (
    r"(?:extern\s+\"C\"\s+)?"
    r"(?:(?:static|inline|constexpr|__host__|__device__|__global__|__forceinline__)\s+)*"
    r"(?:void|int|long|float|double|bool|size_t|auto|"
    r"[A-Za-z_]\w*(?:::\w+)*(?:\s*<[^;{}()]+>)?(?:\s*[*&])*)"
)
MACRO_EXPORT_PATTERN = re.compile(
    RETURN_TYPE_PATTERN
    + r"\s+(?P<macro>[A-Za-z_]\w*_FUNC|FUNC)\s*\(\s*(?P<name>[A-Za-z_]\w*)\s*\)\s*\("
)
PLAIN_EXPORT_PATTERN = re.compile(
    RETURN_TYPE_PATTERN
    + r"\s+(?P<name>[A-Za-z_]\w*(?:_cuda|_gpu))\s*\("
)


@dataclass(frozen=True)
class NativeUnit:
    identity: str
    family: str
    symbol: str
    source_path: str
    line_number: int


@dataclass(frozen=True)
class CustomOpSourceIndicator:
    source_path: str
    line_number: int
    marker: str

REQUIRED_DISCOVERY_SOURCES = (
    "source",
    "bindings",
    "wrappers",
    "autograd",
    "aliases",
    "launch",
    "setup",
    "tests",
)


def validate(data: dict[str, object]) -> ValidationDict:
    errors: list[str] = []

    project_dir = data.get("project_dir")
    if not isinstance(project_dir, str) or not project_dir.strip():
        errors.append("project_dir must be a non-empty string")

    dependencies = data.get("dependencies")
    if not isinstance(dependencies, list):
        errors.append("dependencies must be a list")
    else:
        dependency_list = cast(list[object], dependencies)
        if not all(isinstance(dependency, str) for dependency in dependency_list):
            errors.append("dependencies must contain only strings")

    if not isinstance(data.get("cuda_detected"), bool):
        errors.append("cuda_detected must be a boolean")

    entry_script = data.get("entry_script")
    if not isinstance(entry_script, str) or not entry_script.strip():
        errors.append("entry_script must be a non-empty string")

    migration_route = data.get("migration_route")
    if migration_route is not None:
        if not isinstance(migration_route, str) or migration_route not in MIGRATION_ROUTES:
            errors.append("migration_route must be one of: " + ", ".join(MIGRATION_ROUTES))

    _validate_serving_runtime_surface(data, errors)

    source_discovered_units = _discover_required_cuda_native_units_from_project(project_dir)
    source_custom_op_indicators = _discover_custom_op_source_indicators_from_project(project_dir)
    custom_op_surface = data.get("custom_op_surface")
    if custom_op_surface is None:
        if source_discovered_units:
            errors.append(
                "custom_op_surface must be present and custom_op_detected must be true when CUDA/native custom-op units are discovered from source: "
                + _format_native_units(source_discovered_units)
            )
        elif source_custom_op_indicators:
            errors.append(
                "custom_op_surface must be present and custom_op_detected must be true when project-local custom-op build/binding evidence is discovered from source: "
                + _format_custom_op_indicators(source_custom_op_indicators)
            )
    else:
        if not isinstance(custom_op_surface, dict):
            errors.append("custom_op_surface must be an object when present")
        else:
            surface = cast(dict[str, object], custom_op_surface)
            custom_op_detected = surface.get("custom_op_detected")
            if not isinstance(custom_op_detected, bool):
                errors.append("custom_op_surface.custom_op_detected must be a boolean")
            if not isinstance(surface.get("discovery_complete"), bool):
                errors.append("custom_op_surface.discovery_complete must be a boolean")
            _validate_string_list(
                surface,
                "discovery_sources_checked",
                errors,
                non_empty_message="custom_op_surface.discovery_sources_checked must contain the full source discovery category set when custom_op_detected is true",
                require_non_empty=custom_op_detected is True,
            )
            _validate_string_list(
                surface,
                "searched_source_roots",
                errors,
                non_empty_message="custom_op_surface.searched_source_roots must contain at least one source root when custom_op_detected is true",
                require_non_empty=custom_op_detected is True,
            )
            _validate_string_list(
                surface,
                "searched_source_paths",
                errors,
                non_empty_message="custom_op_surface.searched_source_paths must contain at least one source path when custom_op_detected is true",
                require_non_empty=custom_op_detected is True,
            )
            _validate_string_list(
                surface,
                "operator_families",
                errors,
                non_empty_message="custom_op_surface.operator_families must contain at least one family when custom_op_detected is true",
                require_non_empty=custom_op_detected is True,
            )
            _validate_string_list(
                surface,
                "fine_grained_operator_units",
                errors,
                non_empty_message="custom_op_surface.fine_grained_operator_units must contain at least one source-discovered fine-grained operator unit when custom_op_detected is true",
                require_non_empty=custom_op_detected is True,
            )
            _validate_string_list(
                surface,
                "discovered_operator_names",
                errors,
                non_empty_message="custom_op_surface.discovered_operator_names must contain at least one source-discovered operator name when custom_op_detected is true",
                require_non_empty=custom_op_detected is True,
            )
            _validate_string_list(
                surface,
                "native_operator_symbols",
                errors,
                non_empty_message="custom_op_surface.native_operator_symbols must contain native CUDA/GPU/helper symbols when custom_op_detected is true",
                require_non_empty=custom_op_detected is True,
            )
            _validate_string_list(
                surface,
                "kernel_launch_sites",
                errors,
                non_empty_message="custom_op_surface.kernel_launch_sites must contain kernel launch or CUDA/helper call sites when custom_op_detected is true",
                require_non_empty=custom_op_detected is True,
            )
            _validate_string_list(
                surface,
                "source_evidence",
                errors,
                non_empty_message="custom_op_surface.source_evidence must contain at least one source proof when custom_op_detected is true",
                require_non_empty=custom_op_detected is True,
            )
            _validate_string_list(
                surface,
                "negative_evidence",
                errors,
                non_empty_message="custom_op_surface.negative_evidence must contain at least one negative probe when custom_op_detected is true",
                require_non_empty=custom_op_detected is True,
            )
            _validate_string_list(
                surface,
                "dynamic_loading_checks",
                errors,
                non_empty_message="custom_op_surface.dynamic_loading_checks must contain at least one dynamic loading check when custom_op_detected is true",
                require_non_empty=custom_op_detected is True,
            )
            _validate_string_list(
                surface,
                "build_load_checks",
                errors,
                non_empty_message="custom_op_surface.build_load_checks must contain at least one build/load check when custom_op_detected is true",
                require_non_empty=custom_op_detected is True,
            )
            _validate_string_list(surface, "unresolved_source_groups", errors)
            _validate_string_list(surface, "out_of_scope_source_groups", errors)
            sources = surface.get("discovery_sources_checked")
            if isinstance(sources, list):
                source_values = {str(source).strip().lower().replace("-", "_") for source in cast(list[object], sources)}
                _validate_forbidden_discovery_sources(source_values, errors)
            if source_discovered_units and custom_op_detected is False:
                errors.append(
                    "custom_op_surface.custom_op_detected must be true when CUDA/native custom-op units are discovered from source: "
                    + _format_native_units(source_discovered_units)
                )
            if custom_op_detected is False and _has_active_expanded_variant_metadata(surface):
                errors.append("custom_op_surface.custom_op_detected must be true when active expanded variant metadata is present")
            if custom_op_detected is True:
                if surface.get("discovery_complete") is not True:
                    errors.append("custom_op_surface.discovery_complete must be true when custom_op_detected is true")
                elif cast(list[object], surface.get("unresolved_source_groups", [])):
                    errors.append("custom_op_surface.unresolved_source_groups must be empty when discovery_complete is true")
                _validate_required_sources(surface, errors)
                _validate_fine_grained_unit_evidence(surface, errors)
                _validate_no_implementation_detail_units(surface, errors)
                _validate_no_external_scope_units(surface, errors)
                source_enumerated_axis_values = _source_enumerated_semantic_axis_values(surface, project_dir)
                _validate_source_required_semantic_variants(surface, errors, source_enumerated_axis_values)
                _validate_expanded_variant_metadata(surface, errors, source_enumerated_axis_values)
                _validate_source_discovered_cuda_units(source_discovered_units, surface, errors)

    return {"passed": not errors, "errors": errors, "warnings": []}


def _validate_serving_runtime_surface(data: dict[str, object], errors: list[str]) -> None:
    migration_route = data.get("migration_route")
    surface_value = data.get("serving_runtime_surface")
    if not is_serving_route(migration_route):
        if surface_value is not None and not isinstance(surface_value, dict):
            errors.append("serving_runtime_surface must be an object when present")
        return

    if not isinstance(surface_value, dict):
        errors.append("serving_runtime_surface must be present for vLLM/SGLang serving routes")
        return

    surface = cast(dict[str, object], surface_value)
    expected_framework = serving_framework_for_route(migration_route)
    framework = surface.get("serving_framework")
    if framework != expected_framework:
        errors.append(
            f"serving_runtime_surface.serving_framework must be '{expected_framework}' for migration_route={migration_route}"
        )

    detection_complete = surface.get("detection_complete")
    if not isinstance(detection_complete, bool):
        errors.append("serving_runtime_surface.detection_complete must be a boolean")

    launch_command = surface.get("launch_command")
    if not isinstance(launch_command, str) or not launch_command.strip():
        errors.append("serving_runtime_surface.launch_command must be a non-empty project launch command")

    _validate_string_list(
        surface,
        "launch_evidence",
        errors,
        non_empty_message="serving_runtime_surface.launch_evidence must contain project-local serving launch evidence",
        require_non_empty=True,
    )
    _validate_string_list(
        surface,
        "project_demo_or_test_evidence",
        errors,
        non_empty_message="serving_runtime_surface.project_demo_or_test_evidence must contain project demo/test/API evidence",
        require_non_empty=True,
    )
    _validate_string_list(
        surface,
        "project_test_files",
        errors,
        non_empty_message="serving_runtime_surface.project_test_files must list project-provided demo/test/API files",
        require_non_empty=True,
    )
    _validate_string_list(
        surface,
        "expected_outputs",
        errors,
        non_empty_message="serving_runtime_surface.expected_outputs must list project output evidence expected from the serving demo/API request",
        require_non_empty=True,
    )
    _validate_string_list(
        surface,
        "required_runtime_env",
        errors,
        non_empty_message="serving_runtime_surface.required_runtime_env must list required NPU/serving runtime environment evidence",
        require_non_empty=True,
    )
    for field_name in ("readiness_probe", "request_validation"):
        value = surface.get(field_name)
        if not isinstance(value, dict) or not value:
            errors.append(f"serving_runtime_surface.{field_name} must be a non-empty object")
    _validate_string_list(surface, "unresolved_source_groups", errors, require_non_empty=False)

    if detection_complete is True:
        unresolved = _string_list_values(surface, "unresolved_source_groups")
        if unresolved:
            errors.append(
                "serving_runtime_surface.unresolved_source_groups must be empty when detection_complete=true"
            )


def _validate_string_list(
    surface: dict[str, object],
    field_name: str,
    errors: list[str],
    *,
    require_non_empty: bool = False,
    non_empty_message: str | None = None,
) -> None:
    value = surface.get(field_name)
    if not isinstance(value, list):
        errors.append(f"custom_op_surface.{field_name} must be a list")
        return
    items = cast(list[object], value)
    if not all(isinstance(item, str) and str(item).strip() for item in items):
        errors.append(f"custom_op_surface.{field_name} must contain only non-empty strings")
    if require_non_empty and not items:
        errors.append(non_empty_message or f"custom_op_surface.{field_name} must contain at least one item when custom_op_detected is true")


def _validate_required_sources(surface: dict[str, object], errors: list[str]) -> None:
    sources = surface.get("discovery_sources_checked")
    if not isinstance(sources, list):
        return
    source_values = {str(source).strip().lower().replace("-", "_") for source in cast(list[object], sources)}
    _validate_forbidden_discovery_sources(source_values, errors)
    missing_sources = sorted(set(REQUIRED_DISCOVERY_SOURCES) - source_values)
    if missing_sources:
        errors.append("custom_op_surface.discovery_sources_checked missing required sources: " + ", ".join(missing_sources))


def _validate_forbidden_discovery_sources(source_values: set[str], errors: list[str]) -> None:
    forbidden_sources = sorted(FORBIDDEN_DISCOVERY_SOURCES & source_values)
    if forbidden_sources:
        errors.append(
            "custom_op_surface.discovery_sources_checked must not use non-source completion markers: "
            + ", ".join(forbidden_sources)
        )


def _validate_fine_grained_unit_evidence(surface: dict[str, object], errors: list[str]) -> None:
    unit_values = surface.get("fine_grained_operator_units")
    if not isinstance(unit_values, list):
        return
    unit_identities = [unit for unit in cast(list[object], unit_values) if isinstance(unit, str) and unit.strip()]

    evidence_values = surface.get("fine_grained_operator_unit_evidence")
    if not isinstance(evidence_values, list):
        errors.append("custom_op_surface.fine_grained_operator_unit_evidence must be a list")
        return
    evidence_items = cast(list[object], evidence_values)
    if not evidence_items:
        errors.append("custom_op_surface.fine_grained_operator_unit_evidence must contain at least one source-linked entry when custom_op_detected is true")
        return

    evidence_unit_identities: list[str] = []
    for index, item in enumerate(evidence_items):
        if not isinstance(item, dict):
            errors.append(f"custom_op_surface.fine_grained_operator_unit_evidence[{index}] must be an object")
            continue
        evidence = cast(dict[str, object], item)
        unit_identity = evidence.get("unit_identity")
        if not isinstance(unit_identity, str) or not unit_identity.strip():
            errors.append(f"custom_op_surface.fine_grained_operator_unit_evidence[{index}].unit_identity must be a non-empty string")
        else:
            evidence_unit_identities.append(unit_identity.strip())

        source_evidence = evidence.get("source_evidence")
        if not isinstance(source_evidence, list):
            errors.append(f"custom_op_surface.fine_grained_operator_unit_evidence[{index}].source_evidence must be a list")
            continue
        source_items = cast(list[object], source_evidence)
        if not source_items or not all(isinstance(source, str) and source.strip() for source in source_items):
            errors.append(f"custom_op_surface.fine_grained_operator_unit_evidence[{index}].source_evidence must contain only non-empty strings")

        public_routes = evidence.get("candidate_public_api_routes")
        framework_routes = evidence.get("candidate_framework_integration_routes")
        if not _has_non_empty_string_list(public_routes) and not _has_non_empty_string_list(framework_routes):
            errors.append(
                f"custom_op_surface.fine_grained_operator_unit_evidence[{index}] must include candidate_public_api_routes or candidate_framework_integration_routes"
            )

    source_linked_variant_identities = set(_source_linked_expanded_variant_unit_identities(surface))
    evidence_unit_identities.extend(sorted(source_linked_variant_identities))

    if unit_identities:
        expected = set(unit_identities)
        observed = set(evidence_unit_identities)
        missing = sorted(expected - observed)
        extra = sorted((observed - expected) - source_linked_variant_identities)
        if missing or extra:
            details: list[str] = []
            if missing:
                details.append("missing unit evidence: " + ", ".join(missing))
            if extra:
                details.append("evidence without matching units: " + ", ".join(extra))
            errors.append(
                "custom_op_surface.fine_grained_operator_unit_evidence must provide one source-linked entry for every fine_grained_operator_unit"
                + (" (" + "; ".join(details) + ")" if details else "")
            )


def _source_linked_expanded_variant_unit_identities(surface: dict[str, object]) -> list[str]:
    variants = surface.get("expanded_operator_variants")
    if not isinstance(variants, list):
        return []
    unit_identities: list[str] = []
    for item in cast(list[object], variants):
        if not isinstance(item, dict):
            continue
        variant = cast(dict[object, object], item)
        unit_identity = variant.get("unit_identity")
        if not isinstance(unit_identity, str) or not unit_identity.strip():
            continue
        if not _has_non_empty_string_list(variant.get("source_evidence")):
            continue
        if not _has_non_empty_string_list(variant.get("candidate_public_api_routes")) and not _has_non_empty_string_list(variant.get("candidate_framework_integration_routes")):
            continue
        unit_identities.append(unit_identity.strip())
    return unit_identities


def _validate_no_implementation_detail_units(surface: dict[str, object], errors: list[str]) -> None:
    rejected_units = _implementation_detail_units_from_string_list(
        surface.get("fine_grained_operator_units")
    )
    rejected_evidence_units: list[str] = []
    evidence = surface.get("fine_grained_operator_unit_evidence")
    if isinstance(evidence, list):
        for item in cast(list[object], evidence):
            if not isinstance(item, dict):
                continue
            unit_identity = cast(dict[object, object], item).get("unit_identity")
            if isinstance(unit_identity, str) and _is_implementation_detail_unit_identity(unit_identity):
                rejected_evidence_units.append(unit_identity.strip())

    if rejected_units:
        errors.append(
            "custom_op_surface.fine_grained_operator_units must use public/native boundary operator identities, not raw kernels, launch wrappers, check macros, block/thread helpers, or performance-tuning specializations: "
            + ", ".join(sorted(set(rejected_units))[:20])
        )
    if rejected_evidence_units:
        errors.append(
            "custom_op_surface.fine_grained_operator_unit_evidence unit_identity must not be an implementation-detail row: "
            + ", ".join(sorted(set(rejected_evidence_units))[:20])
        )


def _implementation_detail_units_from_string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [
        item.strip()
        for item in cast(list[object], value)
        if isinstance(item, str)
        and item.strip()
        and _is_implementation_detail_unit_identity(item)
    ]


def _is_implementation_detail_unit_identity(identity: str) -> bool:
    normalized = identity.strip().lower().replace("-", "_").replace(" ", "_")
    if not normalized:
        return False
    if normalized.startswith("__"):
        return True
    return any(pattern.search(normalized) for pattern in IMPLEMENTATION_DETAIL_UNIT_PATTERNS)


def _validate_no_external_scope_units(surface: dict[str, object], errors: list[str]) -> None:
    rejected_units: list[str] = []
    units = _string_list_values(surface, "fine_grained_operator_units")
    evidence_by_unit = _evidence_by_unit_identity(surface)
    for unit in units:
        evidence = evidence_by_unit.get(unit, [])
        if _is_external_scope_unit(unit, evidence):
            rejected_units.append(unit)
    if rejected_units:
        errors.append(
            "custom_op_surface.fine_grained_operator_units must not include external/out-of-scope benchmark units unless project-local source evidence proves they are source-required target units: "
            + ", ".join(sorted(set(rejected_units))[:20])
        )


def _string_list_values(surface: dict[str, object], field_name: str) -> list[str]:
    value = surface.get(field_name)
    if not isinstance(value, list):
        return []
    return [item.strip() for item in cast(list[object], value) if isinstance(item, str) and item.strip()]


def _evidence_by_unit_identity(surface: dict[str, object]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    evidence = surface.get("fine_grained_operator_unit_evidence")
    if not isinstance(evidence, list):
        return result
    for item in cast(list[object], evidence):
        if not isinstance(item, dict):
            continue
        item_map = cast(dict[object, object], item)
        unit_identity = item_map.get("unit_identity")
        if not isinstance(unit_identity, str) or not unit_identity.strip():
            continue
        source_evidence = item_map.get("source_evidence")
        if isinstance(source_evidence, list):
            result[unit_identity.strip()] = [
                source.strip()
                for source in cast(list[object], source_evidence)
                if isinstance(source, str) and source.strip()
            ]
    return result


def _is_external_scope_unit(unit_identity: str, source_evidence: list[str]) -> bool:
    family = unit_identity.split(":", 1)[0]
    family_external = family.lower().startswith("external_")
    evidence_text = " ".join(source_evidence)
    evidence_external = bool(EXTERNAL_SCOPE_PATTERN.search(evidence_text))
    if not family_external and not evidence_external:
        return False
    return not _has_project_local_source_evidence(source_evidence)


def _has_project_local_source_evidence(source_evidence: list[str]) -> bool:
    for evidence in source_evidence:
        normalized = evidence.lower()
        if "project-local" in normalized or "project_local" in normalized:
            return True
        if EXTERNAL_SCOPE_PATTERN.search(normalized):
            continue
        if re.search(r"\.(?:c|cc|cpp|cxx|cu|cuh|h|hh|hpp)(?::|$)", normalized):
            return True
    return False


def _validate_source_required_semantic_variants(
    surface: dict[str, object],
    errors: list[str],
    source_enumerated_axis_values: dict[str, set[str]] | None = None,
) -> None:
    source_axis_values = _filter_source_enumerated_axis_values_for_declared_scope(
        source_enumerated_axis_values or {},
        None,
        None,
        surface,
    )
    axes = sorted(
        set(_detected_source_required_semantic_axes(surface))
        | {axis_name for axis_name, values in source_axis_values.items() if len(values) > 1}
    )
    if not axes:
        return
    if _has_active_expanded_variant_metadata(surface):
        return
    errors.append(
        "custom_op_surface describes source-required semantic generated axes ("
        + ", ".join(axes)
        + ") but does not provide expanded variant metadata; set variant_axes_detected=true and enumerate concrete per-axis expanded_operator_variants"
    )


def _detected_source_required_semantic_axes(surface: dict[str, object]) -> list[str]:
    text_parts: list[str] = []
    for field_name in ("source_evidence", "native_operator_symbols", "discovered_operator_names"):
        text_parts.extend(_string_list_values(surface, field_name))
    evidence = surface.get("fine_grained_operator_unit_evidence")
    if isinstance(evidence, list):
        for item in cast(list[object], evidence):
            if not isinstance(item, dict):
                continue
            item_map = cast(dict[object, object], item)
            unit_identity = item_map.get("unit_identity")
            if isinstance(unit_identity, str):
                text_parts.append(unit_identity)
            source_evidence = item_map.get("source_evidence")
            if isinstance(source_evidence, list):
                text_parts.extend(
                    source
                    for source in cast(list[object], source_evidence)
                    if isinstance(source, str)
                )
    semantic_text = "\n".join(
        part for part in text_parts if part and not _mentions_implementation_detail(part)
    )
    return [axis for axis, pattern in SEMANTIC_VARIANT_AXIS_PATTERNS.items() if pattern.search(semantic_text)]


def _source_enumerated_semantic_axis_values(surface: dict[str, object], project_dir: object) -> dict[str, set[str]]:
    text_parts = _semantic_evidence_text_parts(surface)
    text_parts.extend(_source_context_for_evidence_references(text_parts, project_dir))
    text_parts.extend(_project_semantic_source_text_parts(surface, project_dir))
    result: dict[str, set[str]] = {}
    for text in text_parts:
        if not text or _mentions_implementation_detail(text):
            continue
        for axis_name, values in _extract_explicit_axis_values(text).items():
            if len(values) > 1:
                result.setdefault(axis_name, set()).update(values)
    return result


def _semantic_evidence_text_parts(surface: dict[str, object]) -> list[str]:
    text_parts: list[str] = []
    for field_name in ("source_evidence", "native_operator_symbols", "discovered_operator_names"):
        text_parts.extend(_string_list_values(surface, field_name))
    evidence = surface.get("fine_grained_operator_unit_evidence")
    if isinstance(evidence, list):
        for item in cast(list[object], evidence):
            if not isinstance(item, dict):
                continue
            item_map = cast(dict[object, object], item)
            unit_identity = item_map.get("unit_identity")
            if isinstance(unit_identity, str):
                text_parts.append(unit_identity)
            source_evidence = item_map.get("source_evidence")
            if isinstance(source_evidence, list):
                text_parts.extend(
                    source
                    for source in cast(list[object], source_evidence)
                    if isinstance(source, str)
                )
    return text_parts


def _project_semantic_source_text_parts(surface: dict[str, object], project_dir: object) -> list[str]:
    _ = surface
    if not isinstance(project_dir, str) or not project_dir.strip():
        return []
    root = Path(project_dir)
    if not root.is_dir():
        return []
    resolved_root = root.resolve()
    paths: list[Path] = []
    seen: set[Path] = set()

    def add_path(path: Path) -> None:
        try:
            resolved = path.resolve()
            _ = resolved.relative_to(resolved_root)
        except (OSError, ValueError):
            return
        if resolved in seen or not resolved.is_file():
            return
        seen.add(resolved)
        paths.append(resolved)

    for pattern in ("ADAPTATION_REQUIREMENTS*", "*ADAPTATION_REQUIREMENTS*"):
        for path in root.glob(pattern):
            add_path(path)

    contexts: list[str] = []
    for path in paths[:MAX_DISCOVERY_FILES]:
        try:
            if path.stat().st_size > MAX_DISCOVERY_BYTES:
                continue
            contexts.append(path.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            continue
    return contexts


def _source_context_for_evidence_references(text_parts: list[str], project_dir: object) -> list[str]:
    if not isinstance(project_dir, str) or not project_dir.strip():
        return []
    root = Path(project_dir)
    if not root.is_dir():
        return []
    resolved_root = root.resolve()
    contexts: list[str] = []
    seen: set[tuple[str, int]] = set()
    for text in text_parts:
        for match in SOURCE_REFERENCE_PATTERN.finditer(text):
            relative_path = match.group("path")
            line_number = int(match.group("line"))
            key = (relative_path, line_number)
            if key in seen:
                continue
            seen.add(key)
            source_path = (root / relative_path).resolve()
            try:
                _ = source_path.relative_to(resolved_root)
            except ValueError:
                continue
            if not source_path.is_file():
                continue
            try:
                lines = source_path.read_text(encoding="utf-8", errors="ignore").splitlines()
            except OSError:
                continue
            start = max(0, line_number - 25)
            end = min(len(lines), line_number + 120)
            contexts.append("\n".join(lines[start:end]))
    return contexts


def _extract_explicit_axis_values(text: str) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for axis_name, aliases in SEMANTIC_AXIS_ALIASES.items():
        alias_pattern = "|".join(re.escape(alias) for alias in aliases)
        for match in re.finditer(
            rf"\b(?:enumerates?|values?|options?|choices?|defined|iterate(?:s|d)?|loop(?:s|ed)?(?:\s+through)?|range)\b[^\n.;:]*\b(?:{alias_pattern})\b\s*(?:are|as|in|=|:)?\s*(?P<values>[^\n.;]+)",
            text,
            flags=re.IGNORECASE,
        ):
            values = _normalize_source_axis_values(axis_name, _truncate_axis_value_span(axis_name, match.group("values")))
            if len(values) > 1:
                result.setdefault(axis_name, set()).update(values)
    for match in PYTHON_LITERAL_LIST_PATTERN.finditer(text):
        alias = match.group("name").lower()
        axis_name = SEMANTIC_AXIS_NAMES_BY_ALIAS.get(alias)
        if not axis_name:
            continue
        values = _normalize_source_axis_values(axis_name, match.group("values"))
        if len(values) > 1:
            result.setdefault(axis_name, set()).update(values)
    return result


def _truncate_axis_value_span(axis_name: str, raw_values: str) -> str:
    other_aliases = [alias for other_axis, aliases in SEMANTIC_AXIS_ALIASES.items() if other_axis != axis_name for alias in aliases]
    if not other_aliases:
        return raw_values
    alias_pattern = re.compile(
        r"(?<![A-Za-z0-9_])(?:" + "|".join(re.escape(alias) for alias in other_aliases) + r")(?![A-Za-z0-9_])",
        re.IGNORECASE,
    )
    match = alias_pattern.search(raw_values)
    if match is None:
        return raw_values
    return raw_values[: match.start()]


def _normalize_source_axis_values(axis_name: str, raw_values: str) -> set[str]:
    values: set[str] = set()
    cleaned = raw_values.replace("'", " ").replace('"', " ")
    skipped_tokens = {"and", "or", "the", "values", "value", "for", "current"} | set(SEMANTIC_AXIS_NAMES_BY_ALIAS)
    for token in cast(list[str], re.findall(r"[A-Za-z0-9_+-]+", cleaned)):
        normalized = token.strip().lower()
        if not normalized or normalized in skipped_tokens:
            continue
        if axis_name == "ndim":
            if re.fullmatch(r"\d+", normalized):
                normalized = f"{normalized}d"
            elif not re.fullmatch(r"\d+d", normalized):
                continue
        elif axis_name == "accuracy" and not re.fullmatch(r"\d+", normalized):
            continue
        elif axis_name == "dtype" and not _is_source_dtype_value(normalized):
            continue
        elif axis_name == "device" and not _is_source_device_value(normalized):
            continue
        values.add(normalized)
    return values


def _is_source_dtype_value(value: str) -> bool:
    if value in {"float", "double", "half", "bfloat16", "bf16"}:
        return True
    return bool(re.fullmatch(r"(?:float|fp|int|uint|complex)(?:8|16|32|64|128)", value))


def _is_source_device_value(value: str) -> bool:
    if value in {"cpu", "cuda", "gpu", "npu", "xpu", "mlu", "mps", "hip", "rocm", "ascend", "ascend_opp"}:
        return True
    return bool(re.fullmatch(r"(?:cuda|gpu|npu|xpu|mlu|ascend|rocm)[_+-]?\d*", value))


def _validate_expanded_variant_metadata(
    surface: dict[str, object],
    errors: list[str],
    source_enumerated_axis_values: dict[str, set[str]] | None = None,
) -> None:
    metadata_present = any(field in surface for field in VARIANT_METADATA_FIELDS)
    axes_detected = surface.get("variant_axes_detected")
    if axes_detected is not None and not isinstance(axes_detected, bool):
        errors.append("custom_op_surface.variant_axes_detected must be a boolean when present")
        return
    if not metadata_present or axes_detected is False:
        return
    if axes_detected is not True:
        errors.append("custom_op_surface.variant_axes_detected must be true when expanded variant metadata is present")
        return

    _validate_expanded_variant_target_units(surface, errors)

    axes = _validate_variant_axes(surface.get("variant_axes"), "custom_op_surface.variant_axes", errors)
    variants = _validate_expanded_variant_items(
        surface.get("expanded_operator_variants"),
        "custom_op_surface.expanded_operator_variants",
        errors,
        require_base=True,
    )
    source_enumerated_axis_values = _filter_source_enumerated_axis_values_for_declared_scope(
        source_enumerated_axis_values or {},
        axes,
        variants,
        surface,
    )
    source_enumerated_axis_values = _merge_template_declared_axis_values(
        source_enumerated_axis_values,
        axes,
        surface,
    )
    _validate_variant_axes_are_source_semantic(axes, variants, errors)
    _validate_expanded_variant_target_closure_values(axes, variants, errors)
    expected_variants = source_template_expanded_variants(surface)
    _validate_expanded_variant_inventory_matches_source_template(expected_variants, variants, errors)
    count = surface.get("expanded_operator_instances_count")
    if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
        errors.append("custom_op_surface.expanded_operator_instances_count must be a positive integer when variant_axes_detected is true")
    elif variants is not None and count != len(variants):
        errors.append("custom_op_surface.expanded_operator_instances_count must equal expanded_operator_variants length")

    if variants is not None:
        identities = [cast(str, variant["unit_identity"]) for variant in variants]
        if len(set(identities)) != len(identities):
            errors.append("custom_op_surface.expanded_operator_variants unit_identity values must be unique")
        if axes and variants:
            _validate_source_enumerated_axis_coverage(source_enumerated_axis_values, axes, variants, errors)
            _validate_variant_inventory_is_fully_expanded(axes, variants, errors)
            _validate_source_required_per_base_variant_combinations(
                source_enumerated_axis_values,
                variants,
                errors,
            )
            axis_names = set(axes)
            for index, variant in enumerate(variants):
                variant_axes = cast(dict[str, str], variant["axis_values"])
                variant_axis_names = set(variant_axes)
                extra_axes = sorted(variant_axis_names - axis_names)
                if extra_axes:
                    errors.append(
                        f"custom_op_surface.expanded_operator_variants[{index}].axis_values uses axes not declared in variant_axes: "
                        + ", ".join(extra_axes)
                    )
                for axis_name, axis_value in variant_axes.items():
                    allowed_values = axes.get(axis_name)
                    if allowed_values is not None and axis_value not in allowed_values:
                        errors.append(
                            f"custom_op_surface.expanded_operator_variants[{index}].axis_values.{axis_name} must be one of variant_axes.{axis_name}"
                        )

            observed_values: dict[str, set[str]] = {axis_name: set() for axis_name in axes}
            for variant in variants:
                for axis_name, axis_value in cast(dict[str, str], variant["axis_values"]).items():
                    if axis_name in observed_values:
                        observed_values[axis_name].add(axis_value)
            for axis_name, allowed_values in axes.items():
                missing_values = sorted(allowed_values - observed_values.get(axis_name, set()))
                if missing_values:
                    errors.append(
                        f"custom_op_surface.expanded_operator_variants missing variant_axes.{axis_name} values: "
                        + ", ".join(missing_values)
                        )


def _validate_source_enumerated_axis_coverage(
    source_axis_values: dict[str, set[str]],
    axes: dict[str, set[str]],
    variants: list[dict[str, object]],
    errors: list[str],
) -> None:
    if not source_axis_values:
        return
    observed_values: dict[str, set[str]] = {axis_name: set() for axis_name in axes}
    for variant in variants:
        for axis_name, axis_value in cast(dict[str, str], variant["axis_values"]).items():
            if axis_name in observed_values:
                observed_values[axis_name].add(axis_value)
    for axis_name, required_values in source_axis_values.items():
        declared_values = axes.get(axis_name, set())
        missing_declared = sorted(required_values - declared_values)
        if missing_declared:
            errors.append(
                f"custom_op_surface.variant_axes.{axis_name} missing source-enumerated axis values: "
                + ", ".join(missing_declared)
            )
        missing_observed = sorted(required_values - observed_values.get(axis_name, set()))
        if missing_observed:
            errors.append(
                f"custom_op_surface.expanded_operator_variants axis_values.{axis_name} missing source-enumerated axis values: "
                + ", ".join(missing_observed)
            )


def _merge_template_declared_axis_values(
    source_axis_values: dict[str, set[str]],
    axes: dict[str, set[str]] | None,
    surface: dict[str, object],
) -> dict[str, set[str]]:
    if not axes:
        return source_axis_values
    merged = {axis_name: set(values) for axis_name, values in source_axis_values.items()}
    template_text = "\n".join(_semantic_evidence_text_parts(surface))
    for axis_name, declared_values in axes.items():
        if axis_name == "device" or not declared_values:
            continue
        if _is_implementation_detail_axis(axis_name):
            continue
        if _descriptor_mentions_declared_axis(template_text, axis_name):
            merged.setdefault(axis_name, set()).update(declared_values)
    return merged


def _descriptor_mentions_declared_axis(text: str, axis_name: str) -> bool:
    axis = axis_name.strip()
    if not axis:
        return False
    escaped = re.escape(axis)
    compact = re.escape(axis.replace("_", ""))
    patterns = [
        rf"<\s*{escaped}\s*>",
        rf"\$\{{\s*{escaped}\s*\}}",
    ]
    if compact != escaped:
        patterns.extend([rf"<\s*{compact}\s*>", rf"\$\{{\s*{compact}\s*\}}"])
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def _filter_source_enumerated_axis_values_for_declared_scope(
    source_axis_values: dict[str, set[str]],
    axes: dict[str, set[str]] | None,
    variants: list[dict[str, object]] | None,
    surface: dict[str, object] | None = None,
) -> dict[str, set[str]]:
    if not source_axis_values:
        return {}
    filtered = {
        axis_name: _target_source_axis_values(axis_name, values)
        for axis_name, values in source_axis_values.items()
    }
    filtered = {axis_name: values for axis_name, values in filtered.items() if values}
    if not axes or not variants:
        return filtered
    declared_devices = axes.get("device", set())
    observed_devices = {
        axis_value
        for variant in variants
        for axis_name, axis_value in cast(dict[str, str], variant["axis_values"]).items()
        if axis_name == "device"
    }
    identity_text = "\n".join(str(variant.get("unit_identity", "")) for variant in variants).lower()
    device_values = filtered.get("device")
    if (
        device_values is not None
        and "cpu" in device_values
        and "cpu" not in declared_devices
        and "cpu" not in observed_devices
        and not re.search(r"(?<![A-Za-z0-9])cpu(?![A-Za-z0-9])", identity_text)
        and _custom_op_structural_scope_is_fixed_cuda_or_gpu(surface)
    ):
        device_values.discard("cpu")
        if not device_values:
            _ = filtered.pop("device", None)
    return filtered


def _target_source_axis_values(axis_name: str, values: set[str]) -> set[str]:
    if _is_target_variant_axis_like(axis_name):
        return {value for value in values if not _is_non_target_variant_axis_value(value)}
    return set(values)


def _validate_expanded_variant_target_closure_values(
    axes: dict[str, set[str]] | None,
    variants: list[dict[str, object]] | None,
    errors: list[str],
) -> None:
    if axes:
        for axis_name, values in axes.items():
            invalid_values = sorted(value for value in values if _is_non_target_axis_value(axis_name, value))
            if invalid_values:
                errors.append(
                    f"custom_op_surface.variant_axes.{axis_name} must describe target Ascend OPP/custom-op variants, not source/baseline/reference/loader values: "
                    + ", ".join(invalid_values)
                )
    if not variants:
        return
    for index, variant in enumerate(variants):
        axis_values = cast(dict[str, str], variant.get("axis_values", {}))
        invalid_assignments = sorted(
            f"{axis_name}={axis_value}"
            for axis_name, axis_value in axis_values.items()
            if _is_non_target_axis_value(axis_name, axis_value)
        )
        if invalid_assignments:
            errors.append(
                f"custom_op_surface.expanded_operator_variants[{index}].axis_values must describe target Ascend OPP/custom-op variants, not source/baseline/reference/loader values: "
                + ", ".join(invalid_assignments)
            )


def _is_non_target_axis_value(axis_name: str, value: str) -> bool:
    return _is_target_variant_axis_like(axis_name) and _is_non_target_variant_axis_value(value)


def _is_target_variant_axis_like(axis_name: str) -> bool:
    normalized = axis_name.strip().lower().replace("-", "_").replace(" ", "_")
    return normalized in TARGET_VARIANT_AXIS_LIKE_NAMES or normalized.endswith("_device") or normalized.endswith("_backend")


def _is_non_target_variant_axis_value(value: str) -> bool:
    normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
    return normalized in NON_TARGET_VARIANT_AXIS_VALUES


def _custom_op_structural_scope_is_fixed_cuda_or_gpu(surface: dict[str, object] | None) -> bool:
    if surface is None:
        return False
    structured_parts: list[str] = []
    for field_name in ("fine_grained_operator_units", "discovered_operator_names", "native_operator_symbols"):
        structured_parts.extend(_string_list_values(surface, field_name))
    if not structured_parts:
        return False
    structured_text = "\n".join(structured_parts).lower()
    if re.search(r"\$\{\s*device\s*\}|(?<![A-Za-z0-9])(?:current_device|device_str)(?![A-Za-z0-9])", structured_text):
        return False
    if re.search(r"(?<![A-Za-z0-9])cpu(?![A-Za-z0-9])", structured_text):
        return False
    return bool(re.search(r"(?<![A-Za-z0-9])(?:cuda|gpu)(?![A-Za-z0-9])", structured_text))


def _validate_expanded_variant_target_units(surface: dict[str, object], errors: list[str]) -> None:
    collapsed_units = [
        unit
        for unit in _string_list_values(surface, "fine_grained_operator_units")
        if _is_collapsed_variant_token(unit)
    ]
    if collapsed_units:
        errors.append(
            "custom_op_surface.fine_grained_operator_units must contain concrete per-axis expanded variant identities, not brace/pipe collapsed alternatives: "
            + ", ".join(sorted(collapsed_units[:20]))
        )


def _validate_variant_inventory_is_fully_expanded(
    axes: dict[str, set[str]],
    variants: list[dict[str, object]],
    errors: list[str],
) -> None:
    if not any(len(values) > 1 for values in axes.values()):
        return
    base_identities = {
        str(variant.get("base_unit_identity") or _base_identity_from_unit_identity(str(variant.get("unit_identity", ""))))
        for variant in variants
    }
    if len(variants) <= len(base_identities):
        errors.append(
            "custom_op_surface.expanded_operator_variants must enumerate concrete per-axis combinations; variant count must expand beyond distinct base unit count when any axis has multiple values"
        )


def _validate_source_required_per_base_variant_combinations(
    source_axis_values: dict[str, set[str]],
    variants: list[dict[str, object]],
    errors: list[str],
) -> None:
    if not source_axis_values:
        return
    source_axes = {
        axis_name: values
        for axis_name, values in source_axis_values.items()
        if axis_name != "device" and len(values) > 1
    }
    if not source_axes:
        return

    variants_by_base: dict[str, list[dict[str, object]]] = {}
    for variant in variants:
        base_identity = str(variant.get("base_unit_identity") or "").strip()
        if not base_identity:
            continue
        variants_by_base.setdefault(base_identity, []).append(variant)

    for base_identity, base_variants in variants_by_base.items():
        observed_axis_names = {
            axis_name
            for variant in base_variants
            for axis_name in cast(dict[str, str], variant.get("axis_values", {}))
        }
        required_axes = sorted(axis_name for axis_name in source_axes if axis_name in observed_axis_names)
        if len(required_axes) < 2:
            continue
        expected = _expected_axis_combinations(required_axes, source_axes)
        observed = {
            tuple((axis_name, cast(dict[str, str], variant.get("axis_values", {})).get(axis_name, "")) for axis_name in required_axes)
            for variant in base_variants
            if all(axis_name in cast(dict[str, str], variant.get("axis_values", {})) for axis_name in required_axes)
        }
        missing = sorted(expected - observed)
        if missing:
            errors.append(
                "custom_op_surface.expanded_operator_variants missing source-required per-base axis combinations for "
                + base_identity
                + ": "
                + _format_axis_combinations(missing)
            )


def _validate_expanded_variant_inventory_matches_source_template(
    expected_variants: list[dict[str, object]],
    variants: list[dict[str, object]] | None,
    errors: list[str],
) -> None:
    if not expected_variants:
        return
    if not variants:
        return

    expected_signatures = _expanded_variant_signatures(expected_variants)
    observed_signatures = _expanded_variant_signatures(variants)
    observed_signature_set = set(observed_signatures)
    missing = [signature for signature in expected_signatures if signature not in observed_signature_set]
    if missing:
        errors.append(
            "custom_op_surface.expanded_operator_variants is sampled or incomplete relative to source-backed axes/templates/evidence; missing combinations: "
            + _format_variant_signatures(missing)
        )


def _expanded_variant_signatures(variants: list[dict[str, object]]) -> list[tuple[str, tuple[tuple[str, str], ...]]]:
    signatures: list[tuple[str, tuple[tuple[str, str], ...]]] = []
    for variant in variants:
        base_identity = str(variant.get("base_unit_identity") or variant.get("source_unit_identity") or "").strip()
        if not base_identity:
            unit_identity = str(variant.get("unit_identity", "")).strip()
            if not unit_identity:
                continue
            base_identity = _base_identity_from_unit_identity(unit_identity)
        axis_values = variant.get("axis_values")
        if not isinstance(axis_values, dict):
            axis_values = variant.get("variant_axes")
        ordered_axis_values: tuple[tuple[str, str], ...] = ()
        if isinstance(axis_values, dict):
            ordered_axis_values = tuple(
                sorted(
                    (
                        str(axis_name).strip(),
                        _normalize_declared_axis_value(str(axis_name).strip(), str(axis_value).strip()),
                    )
                    for axis_name, axis_value in cast(dict[object, object], axis_values).items()
                    if isinstance(axis_name, str)
                    and axis_name.strip()
                    and isinstance(axis_value, (str, int, float))
                    and not isinstance(axis_value, bool)
                    and str(axis_value).strip()
                )
            )
        signatures.append((base_identity, ordered_axis_values))
    return signatures


def _format_variant_signatures(signatures: list[tuple[str, tuple[tuple[str, str], ...]]]) -> str:
    rendered = [
        unit_identity + (" : " + ", ".join(f"{axis}={value}" for axis, value in axis_values) if axis_values else "")
        for unit_identity, axis_values in signatures[:12]
    ]
    if len(signatures) > len(rendered):
        rendered.append(f"... +{len(signatures) - len(rendered)} more")
    return "; ".join(rendered)


def _expected_axis_combinations(
    axis_names: list[str],
    axis_values: dict[str, set[str]],
) -> set[tuple[tuple[str, str], ...]]:
    value_lists = [sorted(axis_values[axis_name]) for axis_name in axis_names]
    return {
        tuple(zip(axis_names, values, strict=True))
        for values in product(*value_lists)
    }


def _format_axis_combinations(combinations: list[tuple[tuple[str, str], ...]]) -> str:
    rendered = [", ".join(f"{axis}={value}" for axis, value in combination) for combination in combinations[:12]]
    if len(combinations) > len(rendered):
        rendered.append(f"... +{len(combinations) - len(rendered)} more")
    return "; ".join(rendered)



def _validate_variant_axes_are_source_semantic(
    axes: dict[str, set[str]] | None,
    variants: list[dict[str, object]] | None,
    errors: list[str],
) -> None:
    if not axes:
        return
    rejected_axes = [axis for axis in axes if _is_implementation_detail_axis(axis)]
    if rejected_axes:
        errors.append(
            "custom_op_surface.variant_axes must describe source-required semantic axes, not implementation details: "
            + ", ".join(sorted(rejected_axes))
        )
    if not variants:
        return
    rejected_variants: list[str] = []
    for variant in variants:
        unit_identity = cast(str, variant.get("unit_identity", ""))
        axis_values = variant.get("axis_values", {})
        evidence_text = " ".join([unit_identity, str(axis_values)])
        if _mentions_implementation_detail(evidence_text):
            rejected_variants.append(unit_identity or evidence_text[:80])
    if rejected_variants:
        errors.append(
            "custom_op_surface.expanded_operator_variants must not activate variants for block-size, thread/grid, launch-wrapper, runtime-dispatch, check-macro, or performance-template implementation details: "
            + ", ".join(sorted(rejected_variants[:20]))
        )


def _is_implementation_detail_axis(axis_name: str) -> bool:
    normalized = axis_name.strip().lower().replace("-", "_").replace(" ", "_")
    return any(pattern.search(normalized) for pattern in IMPLEMENTATION_DETAIL_AXIS_PATTERNS)


def _mentions_implementation_detail(text: str) -> bool:
    return any(pattern.search(text) for pattern in IMPLEMENTATION_DETAIL_EVIDENCE_PATTERNS)


def _has_active_expanded_variant_metadata(surface: dict[str, object]) -> bool:
    if surface.get("variant_axes_detected") is True:
        return True
    axes = surface.get("variant_axes")
    if isinstance(axes, dict):
        axes_map = cast(dict[object, object], axes)
        if len(axes_map) > 0:
            return True
    variants = surface.get("expanded_operator_variants")
    if isinstance(variants, list):
        variant_items = cast(list[object], variants)
        if len(variant_items) > 0:
            return True
    count = surface.get("expanded_operator_instances_count")
    return isinstance(count, int) and not isinstance(count, bool) and count > 0


def _validate_variant_axes(value: object, label: str, errors: list[str]) -> dict[str, set[str]] | None:
    if not isinstance(value, dict) or not value:
        errors.append(f"{label} must be a non-empty object when variant_axes_detected is true")
        return None
    axes: dict[str, set[str]] = {}
    for raw_axis, raw_values in cast(dict[object, object], value).items():
        if not isinstance(raw_axis, str) or not raw_axis.strip():
            errors.append(f"{label} axis names must be non-empty strings")
            continue
        axis = raw_axis.strip()
        if _is_collapsed_variant_token(axis):
            errors.append(f"{label} axis names must be atomic and must not use brace/pipe collapsed syntax")
        if not isinstance(raw_values, list) or not raw_values:
            errors.append(f"{label}.{axis} must be a non-empty list")
            continue
        raw_axis_values = cast(list[object], raw_values)
        values = {
            _normalize_declared_axis_value(axis, str(item).strip())
            for item in raw_axis_values
            if isinstance(item, (str, int, float)) and not isinstance(item, bool) and str(item).strip()
        }
        if len(values) != len(raw_axis_values):
            errors.append(f"{label}.{axis} must contain only non-empty scalar values")
            continue
        collapsed_values = sorted(value for value in values if _is_combined_axis_value(value))
        if collapsed_values:
            errors.append(
                f"{label}.{axis} values must be atomic scalar values, not combined assignments or alternatives: "
                + ", ".join(collapsed_values[:20])
            )
            continue
        axes[axis] = values
    return axes if axes else None


def _validate_expanded_variant_items(value: object, label: str, errors: list[str], *, require_base: bool) -> list[dict[str, object]] | None:
    if not isinstance(value, list) or not value:
        errors.append(f"{label} must be a non-empty list when variant_axes_detected is true")
        return None
    variants: list[dict[str, object]] = []
    for index, item in enumerate(cast(list[object], value)):
        if not isinstance(item, dict):
            errors.append(f"{label}[{index}] must be an object")
            continue
        variant = cast(dict[str, object], item)
        unit_identity = variant.get("unit_identity")
        if not isinstance(unit_identity, str) or not unit_identity.strip():
            errors.append(f"{label}[{index}].unit_identity must be a non-empty string")
        elif _is_collapsed_variant_token(unit_identity):
            errors.append(f"{label}[{index}].unit_identity must be concrete and must not use brace/pipe collapsed alternatives")
        base_identity = variant.get("base_unit_identity") or variant.get("source_unit_identity")
        if require_base and (not isinstance(base_identity, str) or not base_identity.strip()):
            errors.append(f"{label}[{index}] must include base_unit_identity or source_unit_identity")
        axis_values = variant.get("axis_values") or variant.get("variant_axes")
        if not isinstance(axis_values, dict) or not axis_values:
            errors.append(f"{label}[{index}] must include non-empty axis_values or variant_axes")
            axis_values = {}
        else:
            normalized_axis_values: dict[str, str] = {}
            for raw_axis, raw_axis_value in cast(dict[object, object], axis_values).items():
                if not isinstance(raw_axis, str) or not raw_axis.strip() or not isinstance(raw_axis_value, (str, int, float)) or isinstance(raw_axis_value, bool) or not str(raw_axis_value).strip():
                    errors.append(f"{label}[{index}].axis_values must contain non-empty scalar axis values")
                    break
                normalized_value = _normalize_declared_axis_value(raw_axis.strip(), str(raw_axis_value).strip())
                if _is_combined_axis_value(raw_axis.strip()) or _is_combined_axis_value(normalized_value):
                    errors.append(f"{label}[{index}].axis_values must contain atomic scalar axis values, not combined assignments or alternatives")
                    break
                normalized_axis_values[raw_axis.strip()] = normalized_value
            axis_values = normalized_axis_values
        source_evidence = variant.get("source_evidence")
        if not _has_non_empty_string_list(source_evidence):
            errors.append(f"{label}[{index}].source_evidence must contain source proof")
        public_routes = variant.get("candidate_public_api_routes")
        framework_routes = variant.get("candidate_framework_integration_routes")
        if not _has_non_empty_string_list(public_routes) and not _has_non_empty_string_list(framework_routes):
            errors.append(f"{label}[{index}] must include candidate_public_api_routes or candidate_framework_integration_routes")
        if isinstance(unit_identity, str) and unit_identity.strip():
            variants.append({
                "unit_identity": unit_identity.strip(),
                "axis_values": axis_values,
                "base_unit_identity": base_identity.strip() if isinstance(base_identity, str) and base_identity.strip() else _base_identity_from_unit_identity(unit_identity.strip()),
            })
    return variants


def _is_collapsed_variant_token(value: str) -> bool:
    return bool(COLLAPSED_VARIANT_SYNTAX_PATTERN.search(value))


def _normalize_declared_axis_value(axis_name: str, value: str) -> str:
    normalized = value.strip().lower()
    axis = axis_name.strip().lower()
    if axis == "ndim" and re.fullmatch(r"\d+", normalized):
        return f"{normalized}d"
    return normalized


def _is_combined_axis_value(value: str) -> bool:
    return bool(COMBINED_AXIS_VALUE_PATTERN.search(value.strip()))


def _base_identity_from_unit_identity(unit_identity: str) -> str:
    parts = [part for part in unit_identity.split(":") if "=" not in part]
    return ":".join(parts) or unit_identity


def _has_non_empty_string_list(value: object) -> bool:
    if not isinstance(value, list):
        return False
    return any(isinstance(item, str) and item.strip() for item in cast(list[object], value))


def _validate_source_discovered_cuda_units(
    required_units: list[NativeUnit],
    surface: dict[str, object],
    errors: list[str],
) -> None:
    if not required_units:
        return

    unit_tokens = _string_list_tokens(surface, "fine_grained_operator_units")
    evidence_tokens = _fine_grained_evidence_unit_tokens(surface)
    missing = [unit for unit in required_units if not _unit_reported(unit, unit_tokens)]
    missing_evidence = [unit for unit in required_units if not _unit_reported(unit, evidence_tokens)]
    if missing:
        errors.append(
            "custom_op_surface.fine_grained_operator_units missing CUDA/native helper units discovered from source: "
            + _format_native_units(missing)
        )
    if missing_evidence:
        errors.append(
            "custom_op_surface.fine_grained_operator_unit_evidence missing CUDA/native helper units discovered from source: "
            + _format_native_units(missing_evidence)
        )


def _discover_required_cuda_native_units_from_project(project_dir: object) -> list[NativeUnit]:
    if not isinstance(project_dir, str) or not project_dir.strip():
        return []
    root = Path(project_dir)
    if not root.is_dir():
        return []
    return _discover_required_cuda_native_units(root)


def _format_native_units(units: list[NativeUnit]) -> str:
    details = ", ".join(
        f"{unit.identity} ({unit.source_path}:{unit.line_number})"
        for unit in units[:20]
    )
    if len(units) > 20:
        details += f", ... +{len(units) - 20} more"
    return details


def _format_custom_op_indicators(indicators: list[CustomOpSourceIndicator]) -> str:
    details = ", ".join(
        f"{indicator.marker} ({indicator.source_path}:{indicator.line_number})"
        for indicator in indicators[:20]
    )
    if len(indicators) > 20:
        details += f", ... +{len(indicators) - 20} more"
    return details


def _discover_custom_op_source_indicators_from_project(project_dir: object) -> list[CustomOpSourceIndicator]:
    if not isinstance(project_dir, str) or not project_dir.strip():
        return []
    root = Path(project_dir)
    if not root.is_dir():
        return []
    indicators: dict[tuple[str, int, str], CustomOpSourceIndicator] = {}
    for path in _custom_op_indicator_files(root):
        try:
            if path.stat().st_size > MAX_DISCOVERY_BYTES:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        relative = path.relative_to(root).as_posix()
        for pattern in CUSTOM_OP_SOURCE_INDICATOR_PATTERNS:
            for match in pattern.finditer(text):
                marker = match.group(0).split("(", 1)[0].strip()
                line_number = _line_number(text, match.start())
                indicators[(relative, line_number, marker)] = CustomOpSourceIndicator(relative, line_number, marker)
    return sorted(indicators.values(), key=lambda indicator: (indicator.source_path, indicator.line_number, indicator.marker))


def _custom_op_indicator_files(project_dir: Path) -> list[Path]:
    files: list[Path] = []
    for path in project_dir.rglob("*"):
        if len(files) >= MAX_DISCOVERY_FILES:
            break
        if not path.is_file() or path.suffix.lower() not in CUSTOM_OP_INDICATOR_SUFFIXES:
            continue
        relative_parts = path.relative_to(project_dir).parts
        if any(part in EXCLUDED_SOURCE_DIRS for part in relative_parts):
            continue
        files.append(path)
    return files


def _string_list_tokens(surface: dict[str, object], field_name: str) -> set[str]:
    tokens: set[str] = set()
    value = surface.get(field_name)
    if isinstance(value, list):
        for item in cast(list[object], value):
            _add_reported_tokens(tokens, item)
    return tokens


def _fine_grained_evidence_unit_tokens(surface: dict[str, object]) -> set[str]:
    tokens: set[str] = set()
    evidence = surface.get("fine_grained_operator_unit_evidence")
    if not isinstance(evidence, list):
        return tokens
    for item in cast(list[object], evidence):
        if not isinstance(item, dict):
            continue
        unit_identity = cast(dict[object, object], item).get("unit_identity")
        _add_reported_tokens(tokens, unit_identity)
    return tokens


def _add_reported_tokens(tokens: set[str], value: object) -> None:
    if isinstance(value, str):
        normalized = _normalize_token(value)
        if normalized:
            tokens.add(normalized)
        for part in re.split(r"[^A-Za-z0-9_]+", value):
            part_normalized = _normalize_token(part)
            if part_normalized:
                tokens.add(part_normalized)
        return
    if isinstance(value, dict):
        for item in cast(dict[object, object], value).values():
            _add_reported_tokens(tokens, item)
    elif isinstance(value, list):
        for item in cast(list[object], value):
            _add_reported_tokens(tokens, item)


def _unit_reported(unit: NativeUnit, reported_tokens: set[str]) -> bool:
    identity = _normalize_token(unit.identity)
    symbol = _normalize_token(unit.symbol)
    family = _normalize_token(unit.family)
    return (
        identity in reported_tokens
        or symbol in reported_tokens
        or f"{family}:{symbol}" in reported_tokens
        or f"{family}_{symbol}" in reported_tokens
    )


def _discover_required_cuda_native_units(project_dir: Path) -> list[NativeUnit]:
    files = _native_source_files(project_dir)
    if not files:
        return []

    contents: dict[Path, str] = {}
    for path in files:
        try:
            if path.stat().st_size > MAX_DISCOVERY_BYTES:
                continue
            contents[path] = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
    if not contents:
        return []

    cuda_text = "\n".join(text for path, text in contents.items() if path.suffix.lower() in CUDA_SOURCE_SUFFIXES)
    units: dict[str, NativeUnit] = {}
    for path, text in contents.items():
        relative = path.relative_to(project_dir).as_posix()
        for unit in _extract_native_units_from_text(relative, text, path.suffix.lower(), cuda_text):
            _ = units.setdefault(unit.identity, unit)
    return sorted(units.values(), key=lambda unit: unit.identity)


def _native_source_files(project_dir: Path) -> list[Path]:
    files: list[Path] = []
    for path in project_dir.rglob("*"):
        if len(files) >= MAX_DISCOVERY_FILES:
            break
        if not path.is_file() or path.suffix.lower() not in NATIVE_SOURCE_SUFFIXES:
            continue
        relative_parts = path.relative_to(project_dir).parts
        if any(part in EXCLUDED_SOURCE_DIRS for part in relative_parts):
            continue
        files.append(path)
    return files


def _extract_native_units_from_text(
    relative_path: str,
    text: str,
    suffix: str,
    cuda_text: str,
) -> list[NativeUnit]:
    units: list[NativeUnit] = []
    for match in MACRO_EXPORT_PATTERN.finditer(text):
        macro = match.group("macro")
        name = match.group("name")
        if not _macro_unit_is_cuda_related(macro, name, text, suffix, cuda_text):
            continue
        family = _family_from_path(relative_path)
        if macro != "FUNC":
            family = _family_from_macro(macro, family)
        symbol = _cuda_symbol_name(macro, name, suffix)
        units.append(
            NativeUnit(
                identity=f"{family}:{symbol}",
                family=family,
                symbol=symbol,
                source_path=relative_path,
                line_number=_line_number(text, match.start()),
            )
        )

    for match in PLAIN_EXPORT_PATTERN.finditer(text):
        name = match.group("name")
        family = _family_from_path(relative_path)
        units.append(
            NativeUnit(
                identity=f"{family}:{name}",
                family=family,
                symbol=name,
                source_path=relative_path,
                line_number=_line_number(text, match.start()),
            )
        )
    return units


def _macro_unit_is_cuda_related(macro: str, name: str, text: str, suffix: str, cuda_text: str) -> bool:
    name_lower = name.lower()
    if name_lower.endswith(CUDA_NATIVE_SUFFIXES):
        return True
    if macro == "FUNC":
        return suffix in CUDA_SOURCE_SUFFIXES and bool(
            re.search(r"#define\s+CAT_I\b[^\n]*(?:cuda|gpu|device|dw_device)", text, flags=re.IGNORECASE)
        )
    invocation = f"{macro}({name})"
    return invocation in cuda_text


def _cuda_symbol_name(macro: str, name: str, suffix: str) -> str:
    if name.lower().endswith(CUDA_NATIVE_SUFFIXES):
        return name
    if macro == "FUNC" and suffix in CUDA_SOURCE_SUFFIXES:
        return f"{name}_cuda"
    return name


def _family_from_macro(macro: str, fallback: str) -> str:
    prefix = macro[:-5].lower() if macro.endswith("_FUNC") else macro.lower()
    if prefix == "sc":
        return "simple_compress"
    if prefix == "storage":
        return "storage"
    return prefix or fallback


def _family_from_path(relative_path: str) -> str:
    stem = Path(relative_path).stem
    if stem.endswith("_utils"):
        stem = stem[:-6]
    return stem


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _normalize_token(value: str) -> str:
    return value.strip().lower()
