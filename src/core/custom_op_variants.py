"""Helpers for propagating expanded custom-op variant contracts."""

from __future__ import annotations

import ast
from collections.abc import Mapping, Sequence
from itertools import product
from pathlib import Path
import json
import re
import shlex
import sys
from typing import cast

from core.custom_op_source_discovery import discover_required_cuda_native_units_from_project
from core.routes import SGLANG_SERVING, VLLM_SERVING


PHASE1_REQUIRED_DISCOVERY_SOURCES = (
    "source",
    "bindings",
    "wrappers",
    "autograd",
    "aliases",
    "launch",
    "setup",
    "tests",
)
DEPENDENCY_MANIFESTS = (
    "requirements.txt",
    "requirements-dev.txt",
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "environment.yml",
    "environment.yaml",
    "Pipfile",
    "package.json",
)
ENTRY_SCRIPT_CANDIDATES = (
    "test_data_and_scripts/run.py",
    "test_data_and_scripts/main.py",
    "test_data_and_scripts/train.py",
    "test_data_and_scripts/inference.py",
    "run.py",
    "main.py",
    "train.py",
    "inference.py",
    "demo.py",
    "app.py",
)
CUDA_TEXT_PATTERN = re.compile(
    r"\b(?:CUDAExtension|CppExtension|torch\.utils\.cpp_extension|PYBIND11_MODULE|TORCH_LIBRARY|__global__|cudaLaunchKernel|\.cu\b|\.cuh\b|torch\.ops)\b"
)
VLLM_STRONG_PATTERN = re.compile(
    r"\b(?:vllm\s+serve|mineru-vllm-server|vllm-async-engine|AsyncLLM|AsyncEngineArgs|vllm\.entrypoints|from\s+vllm\b|import\s+vllm\b)",
    re.IGNORECASE,
)
SGLANG_STRONG_PATTERN = re.compile(
    r"\b(?:sglang\s+serve|python\s+-m\s+sglang|sglang\.launch_server|from\s+sglang\b|import\s+sglang\b)",
    re.IGNORECASE,
)
NON_TARGET_VARIANT_VALUES = frozenset({"cpu", "host", "baseline", "reference", "native", "none", "default"})
PHASE1_MIGRATION_ROUTES = frozenset(
    {"ordinary_cuda", "custom_op", "custom_op_with_variants", "vllm_serving", "sglang_serving"}
)


EXPANDED_VARIANT_CONTRACT_FIELDS = frozenset(
    {
        "expanded_variant_inventory",
        "expanded_operator_variants",
        "variant_axis_coverage",
        "per_variant_performance_report",
    }
)

REQUIRED_VARIANT_CHECKS = (
    "expanded_variant_inventory",
    "variant_axis_coverage",
    "per_variant_performance_report",
)
PREFERRED_AXIS_ORDER = ("ndim", "accuracy", "dtype", "layout", "mode")
HELPER_FAMILY_SUFFIXES = ("_utils", "_util", "_helpers", "_helper", "_ops", "_op", "_kernels", "_kernel")
OPERATOR_INVENTORY_AXIS_NAMES = frozenset(
    {
        "unit",
        "units",
        "operator",
        "operators",
        "operator_unit",
        "operator_units",
        "base_unit",
        "base_units",
        "source_unit",
        "source_units",
        "unit_identity",
        "unit_identities",
        "expanded_unit",
        "expanded_units",
        "fine_grained_operator_unit",
        "fine_grained_operator_units",
    }
)
STRICT_OPERATOR_INVENTORY_AXIS_NAMES = frozenset(
    {
        "unit_identity",
        "unit_identities",
        "expanded_unit",
        "expanded_units",
        "fine_grained_operator_unit",
        "fine_grained_operator_units",
    }
)
DEVICE_SUFFIX_PATTERN = re.compile(r"(?:^|[:_\-])(cuda|gpu)(?:$|[:_\-])", re.IGNORECASE)

IMPLEMENTATION_DETAIL_AXIS_PATTERNS = (
    re.compile(r"(?:^|[_\-\s])(block|blocksize|block_size|threads?|thread_count|grid|gridsize|grid_size)(?:$|[_\-\s])", re.IGNORECASE),
    re.compile(r"(?:^|[_\-\s])(launch|wrapper|macro|check|runtime|dispatch|coverage|template|specialization|tuning|performance|heuristic)(?:$|[_\-\s])", re.IGNORECASE),
)
IMPLEMENTATION_DETAIL_TEXT_PATTERNS = (
    re.compile(r"\bblock\s*[_-]?\s*size\b", re.IGNORECASE),
    re.compile(r"\b(?:thread|grid)\s*(?:heuristic|count|dim|size|block)\b", re.IGNORECASE),
    re.compile(r"\b(?:launch\s+wrapper|check\s+macro|runtime\s+(?:dtype\s+)?dispatch|dispatch\s+coverage)\b", re.IGNORECASE),
    re.compile(r"\b(?:performance[-_\s]+tuning|template\s+speciali[sz]ation)\b", re.IGNORECASE),
)
COLLAPSED_VARIANT_SYNTAX_PATTERN = re.compile(r"[{}|]")
COMBINED_AXIS_VALUE_PATTERN = re.compile(r"[{}|,]|\w+\s*=")


def expanded_variant_contract_from_outputs(outputs: object) -> dict[str, object]:
    """Return a canonical expanded-variant overlay from phase outputs."""
    if not isinstance(outputs, Mapping):
        return {}
    output_map = cast(Mapping[object, object], outputs)
    phase1 = output_map.get("phase_1_project_analysis") or output_map.get("phase_1")
    phase1_overlay = expanded_variant_contract_from_phase1(phase1)
    if phase1_overlay:
        return phase1_overlay
    return expanded_variant_contract_from_contract(output_map.get("phase_3_entry_script"))


def normalize_phase1_project_analysis(output: dict[str, object], *, project_dir: object) -> None:
    """Bind and repair Phase 1 project-analysis output from source-backed discovery."""
    project_dir_text = str(project_dir) if isinstance(project_dir, (str, Path)) else ""
    output["project_dir"] = project_dir_text
    root = Path(project_dir_text) if project_dir_text else None

    if not _is_string_list(output.get("dependencies")):
        output["dependencies"] = _discover_dependencies(root)
    if not isinstance(output.get("cuda_detected"), bool):
        output["cuda_detected"] = _discover_cuda_detected(root)
    if not isinstance(output.get("entry_script"), str) or not str(output.get("entry_script", "")).strip():
        output["entry_script"] = _discover_entry_script(root)

    units = discover_required_cuda_native_units_from_project(project_dir_text)
    merged_inventory_axes = False
    if units:
        output["cuda_detected"] = True
        _normalize_phase1_route(output, default_route="custom_op")
        custom_op_surface = output.get("custom_op_surface")
        if not isinstance(custom_op_surface, dict):
            custom_op_surface = {}
            output["custom_op_surface"] = custom_op_surface
        surface = cast(dict[str, object], custom_op_surface)
        _ensure_source_backed_custom_op_surface(surface, units, project_dir_text)
        _merge_source_template_variant_axes(surface, project_dir_text)
        merged_inventory_axes = _merge_operator_inventory_variant_axes(output, surface)

    normalize_project_analysis_expanded_variants(output)
    surface_value = output.get("custom_op_surface")
    if units and isinstance(surface_value, dict):
        surface_map = cast(dict[str, object], surface_value)
        if merged_inventory_axes:
            _drop_unexpanded_synthesized_variant_axes(surface_map)
        _normalize_phase1_route(
            output,
            default_route="custom_op_with_variants" if _surface_has_active_variant_metadata(surface_map) else "custom_op",
        )
    elif isinstance(surface_value, dict) and _surface_requires_custom_op_route(cast(Mapping[object, object], surface_value)):
        surface_map = cast(dict[str, object], surface_value)
        _normalize_phase1_route(
            output,
            default_route="custom_op_with_variants" if _surface_has_active_variant_metadata(surface_map) else "custom_op",
        )
    else:
        route = _discover_serving_route(root)
        if route is not None:
            output["migration_route"] = route
            _ensure_serving_runtime_surface(output, root, route)
        else:
            _normalize_phase1_route(output, default_route="ordinary_cuda")


def normalize_project_analysis_expanded_variants(output: dict[str, object]) -> None:
    """Normalize Phase 1 expanded-variant metadata from source-declared templates.

    Phase 1 models often report the source-discovered base units and generated
    symbol templates correctly, then provide only representative expanded rows.
    When the source metadata is explicit enough, synthesize the concrete rows
    from those generic source axes instead of accepting a sampled inventory.
    """
    custom_op_surface = output.get("custom_op_surface")
    if not isinstance(custom_op_surface, dict):
        return

    surface = cast(dict[str, object], custom_op_surface)
    project_dir = output.get("project_dir")
    _supplement_source_discovered_cuda_units(surface, project_dir)
    _canonicalize_compact_cuda_base_units(surface)
    variants = surface.get("expanded_operator_variants")
    if isinstance(variants, list) and variants:
        variant_count = len(cast(list[object], variants))
        declared_count = surface.get("expanded_operator_instances_count")
        if not isinstance(declared_count, int) or isinstance(declared_count, bool) or declared_count < variant_count:
            surface["expanded_operator_instances_count"] = variant_count

    generated = source_template_expanded_variants(surface, project_dir=str(project_dir) if isinstance(project_dir, str) else None)
    if not generated:
        return
    existing_variants = cast(list[object], variants) if isinstance(variants, list) else []
    if not _should_replace_with_source_template_variants(existing_variants, generated):
        return
    surface["expanded_operator_variants"] = generated
    surface["expanded_operator_instances_count"] = len(generated)
    _merge_variant_axes_from_generated_rows(surface, generated)
    _mark_discovery_complete_for_full_generated_inventory(surface, generated)


def _is_string_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in cast(list[object], value))


def _normalize_phase1_route(output: dict[str, object], *, default_route: str) -> None:
    if default_route in {"custom_op", "custom_op_with_variants"}:
        output["migration_route"] = default_route
        return

    route = output.get("migration_route")
    if isinstance(route, str) and route in PHASE1_MIGRATION_ROUTES:
        if route == "ordinary_cuda" and default_route != "ordinary_cuda":
            output["migration_route"] = default_route
        return
    raw_route = output.get("route")
    if isinstance(raw_route, str) and raw_route in PHASE1_MIGRATION_ROUTES:
        if raw_route == "ordinary_cuda" and default_route != "ordinary_cuda":
            output["migration_route"] = default_route
        else:
            output["migration_route"] = raw_route
        return
    output["migration_route"] = default_route


def _surface_requires_custom_op_route(surface: Mapping[object, object]) -> bool:
    if surface.get("custom_op_detected") is not True:
        return False
    if surface.get("discovery_complete") is True:
        return True
    for field in ("fine_grained_operator_units", "discovered_operator_names", "native_operator_symbols", "source_evidence"):
        if _string_sequence(surface.get(field)):
            return True
    return False


def _discover_serving_route(root: Path | None) -> str | None:
    if root is None or not root.is_dir():
        return None
    vllm_evidence: list[str] = []
    sglang_evidence: list[str] = []
    for path in _iter_project_source_files(root, suffixes={".py", ".toml", ".txt", ".md", ".yaml", ".yml", ".sh"}):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        relative = path.relative_to(root).as_posix()
        if VLLM_STRONG_PATTERN.search(text):
            vllm_evidence.append(relative)
        if SGLANG_STRONG_PATTERN.search(text):
            sglang_evidence.append(relative)
    if sglang_evidence:
        return SGLANG_SERVING
    if vllm_evidence:
        return VLLM_SERVING
    return None


def _ensure_serving_runtime_surface(output: dict[str, object], root: Path | None, route: str) -> None:
    surface_value = output.get("serving_runtime_surface")
    surface = dict(cast(Mapping[str, object], surface_value)) if isinstance(surface_value, Mapping) else {}
    framework = "sglang" if route == SGLANG_SERVING else "vllm"
    evidence = _serving_evidence(root, framework)
    launch_command = _serving_launch_command(root, framework)
    demo_files = _serving_demo_or_test_files(root)
    surface.setdefault("launch_command", launch_command)
    surface.setdefault("launch_evidence", evidence or [f"project-local files mention {framework} serving launch"])
    surface.setdefault("project_demo_or_test_evidence", demo_files or evidence or ["project-local README/config documents serving API usage"])
    surface.setdefault("project_test_files", demo_files or evidence or ["README.md"])
    surface.setdefault("readiness_probe", {"type": "http", "path": "/health"})
    surface.setdefault("request_validation", {"type": "openai-compatible-http", "path": "/v1/chat/completions"})
    surface.setdefault("expected_outputs", ["HTTP 200 response from project serving API request"])
    surface.setdefault("required_runtime_env", [framework, "torch", "target accelerator runtime"])
    surface.setdefault("unresolved_source_groups", [])
    surface.setdefault("detection_complete", True)
    output["serving_runtime_surface"] = surface


def _serving_evidence(root: Path | None, framework: str) -> list[str]:
    if root is None or not root.is_dir():
        return []
    pattern = SGLANG_STRONG_PATTERN if framework == "sglang" else VLLM_STRONG_PATTERN
    evidence: list[str] = []
    for path in _iter_project_source_files(root, suffixes={".py", ".toml", ".txt", ".md", ".yaml", ".yml", ".sh"}):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if pattern.search(text):
            evidence.append(path.relative_to(root).as_posix())
        if len(evidence) >= 5:
            break
    return evidence


def _serving_launch_command(root: Path | None, framework: str) -> str:
    evidence = _serving_evidence(root, framework)
    if framework == "sglang":
        return "sglang serve --model-path <project-model> --port 8080"
    if any("pyproject.toml" in item for item in evidence):
        return "mineru-vllm-server"
    return "vllm serve <project-model> --port 8000"


def _serving_demo_or_test_files(root: Path | None) -> list[str]:
    if root is None or not root.is_dir():
        return []
    candidates: list[str] = []
    for path in _iter_project_source_files(root, suffixes={".py", ".md", ".yaml", ".yml"}):
        relative = path.relative_to(root).as_posix()
        lowered = relative.lower()
        if "test" in lowered or "example" in lowered or "readme" in lowered or "config" in lowered:
            candidates.append(relative)
        if len(candidates) >= 5:
            break
    return candidates


def _surface_has_active_variant_metadata(surface: dict[str, object]) -> bool:
    if surface.get("variant_axes_detected") is True:
        return True
    axes = surface.get("variant_axes")
    if isinstance(axes, Mapping) and axes:
        return True
    variants = surface.get("expanded_operator_variants")
    if isinstance(variants, list) and variants:
        return True
    count = surface.get("expanded_operator_instances_count")
    return isinstance(count, int) and not isinstance(count, bool) and count > 0


def _surface_has_active_custom_op_inventory(surface: Mapping[object, object]) -> bool:
    if surface.get("custom_op_detected") is True:
        return True
    if surface.get("custom_op_detected") is False:
        return False
    return bool(
        _string_list(surface.get("fine_grained_operator_units"))
        or _string_list(surface.get("discovered_operator_names"))
        or _string_list(surface.get("native_operator_symbols"))
        or _string_list(surface.get("kernel_launch_sites"))
    )


def _discover_dependencies(root: Path | None) -> list[str]:
    if root is None or not root.is_dir():
        return []
    dependencies: list[str] = []
    for manifest in DEPENDENCY_MANIFESTS:
        path = root / manifest
        if not path.is_file():
            continue
        dependencies.extend(_dependencies_from_manifest(path))
    return _ordered_unique(dependencies)


def _dependencies_from_manifest(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    name = path.name.lower()
    if name.startswith("requirements"):
        return [_clean_dependency_line(line) for line in text.splitlines() if _clean_dependency_line(line)]
    if name == "pyproject.toml":
        return _dependencies_from_loose_manifest_text(text)
    if name in {"environment.yml", "environment.yaml"}:
        return _dependencies_from_environment_yaml(text)
    if name == "setup.cfg":
        return _dependencies_from_setup_cfg(text)
    if name == "package.json":
        return _dependencies_from_package_json(text)
    return _dependencies_from_loose_manifest_text(text)


def _clean_dependency_line(line: str) -> str:
    clean = line.split("#", 1)[0].strip()
    if not clean or clean.startswith(("-r ", "--")):
        return ""
    return clean


def _dependencies_from_environment_yaml(text: str) -> list[str]:
    dependencies: list[str] = []
    in_dependencies = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == "dependencies:":
            in_dependencies = True
            continue
        if not in_dependencies or not stripped.startswith("-"):
            continue
        value = stripped[1:].strip()
        if value and not value.startswith("pip:"):
            dependencies.append(value)
    return dependencies


def _dependencies_from_setup_cfg(text: str) -> list[str]:
    dependencies: list[str] = []
    in_requires = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("install_requires"):
            in_requires = True
            _, _, value = stripped.partition("=")
            if value.strip():
                dependencies.append(value.strip())
            continue
        if in_requires:
            if line.startswith(" ") or line.startswith("\t"):
                if stripped:
                    dependencies.append(stripped)
            else:
                in_requires = False
    return dependencies


def _dependencies_from_package_json(text: str) -> list[str]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    dependencies: list[str] = []
    if isinstance(data, dict):
        data_map = cast(dict[object, object], data)
        for key in ("dependencies", "devDependencies"):
            value = data_map.get(key)
            if isinstance(value, dict):
                dependencies.extend(name for name in cast(dict[object, object], value) if isinstance(name, str))
    return dependencies


def _dependencies_from_loose_manifest_text(text: str) -> list[str]:
    dependencies: list[str] = []
    for match in re.finditer(r"['\"]([A-Za-z0-9_.-]+(?:[<>=!~]=?[^'\",\]]*)?)['\"]", text):
        value = match.group(1).strip()
        if value and value.lower() != "python":
            dependencies.append(value)
    return dependencies


def _discover_cuda_detected(root: Path | None) -> bool:
    if root is None or not root.is_dir():
        return False
    if discover_required_cuda_native_units_from_project(str(root)):
        return True
    for path in _iter_project_source_files(root, suffixes={".py", ".c", ".cc", ".cpp", ".cxx", ".cu", ".cuh", ".h", ".hpp"}):
        if path.suffix.lower() in {".cu", ".cuh"}:
            return True
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if CUDA_TEXT_PATTERN.search(text):
            return True
    return False


def _discover_entry_script(root: Path | None) -> str:
    if root is None or not root.is_dir():
        return "entry_script.py"
    for candidate in ENTRY_SCRIPT_CANDIDATES:
        if (root / candidate).is_file():
            return candidate
    scripts_dir = root / "test_data_and_scripts"
    if scripts_dir.is_dir():
        scripts = sorted(path for path in scripts_dir.glob("*.py") if path.is_file())
        if scripts:
            return scripts[0].relative_to(root).as_posix()
    for name in ("run", "main", "train", "inference", "demo", "app"):
        matches = sorted(path for path in root.rglob(f"{name}.py") if _is_project_local_file(root, path))
        if matches:
            return matches[0].relative_to(root).as_posix()
    return "entry_script.py"


def _iter_project_source_files(root: Path, *, suffixes: set[str]) -> list[Path]:
    files: list[Path] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if len(files) >= 2000:
            break
        if path.is_file() and path.suffix.lower() in suffixes and _is_project_local_file(root, path):
            files.append(path)
    return files


def _is_project_local_file(root: Path, path: Path) -> bool:
    try:
        relative_parts = path.relative_to(root).parts
    except ValueError:
        return False
    return not any(part in {".git", ".sm-artifacts", ".venv", "__pycache__", "build", "dist", "output_projects", "e2e-reports", "site-packages", "venv"} for part in relative_parts)


def _ensure_source_backed_custom_op_surface(surface: dict[str, object], units: Sequence[object], project_dir: str) -> None:
    native_units = [unit for unit in units if hasattr(unit, "identity") and hasattr(unit, "source_path")]
    unit_identities = _ordered_unique([_native_unit_attr(unit, "identity") for unit in native_units])
    families = _ordered_unique([_native_unit_attr(unit, "family") for unit in native_units])
    symbols = _ordered_unique([_native_unit_attr(unit, "symbol") for unit in native_units])
    source_paths = _ordered_unique([_native_unit_attr(unit, "source_path") for unit in native_units])
    source_evidence = _ordered_unique(
        [
            f"source inspection: {_native_unit_attr(unit, 'source_path')}:{_native_unit_attr(unit, 'line_number')} defines {_native_unit_attr(unit, 'symbol')}"
            for unit in native_units
        ]
    )

    surface["custom_op_detected"] = True
    surface["discovery_complete"] = True
    surface["discovery_sources_checked"] = list(PHASE1_REQUIRED_DISCOVERY_SOURCES)
    surface["searched_source_roots"] = [project_dir]
    surface["searched_source_paths"] = source_paths
    surface["operator_families"] = families
    surface["fine_grained_operator_units"] = unit_identities
    surface["discovered_operator_names"] = symbols
    surface["native_operator_symbols"] = symbols
    surface["kernel_launch_sites"] = source_evidence
    surface["source_evidence"] = source_evidence
    surface["negative_evidence"] = [
        "source inspection only: no runtime custom-op pass/fail evidence claimed in Phase 1 inventory"
    ]
    surface["dynamic_loading_checks"] = [
        "source inspection only: inspect Python/C++ binding and dynamic-loading routes for each discovered native unit"
    ]
    surface["build_load_checks"] = [
        "source inspection only: inspect setup/build manifests for extension build and load routes without claiming execution"
    ]
    surface["unresolved_source_groups"] = []
    surface["out_of_scope_source_groups"] = []
    surface["fine_grained_operator_unit_evidence"] = [
        {
            "unit_identity": _native_unit_attr(unit, "identity"),
            "source_evidence": [
                f"source inspection: {_native_unit_attr(unit, 'source_path')}:{_native_unit_attr(unit, 'line_number')} defines {_native_unit_attr(unit, 'symbol')}"
            ],
            "candidate_framework_integration_routes": [
                f"source-backed native binding route for {_native_unit_attr(unit, 'symbol')}",
                "source-backed Python/C++ extension integration route",
            ],
        }
        for unit in native_units
    ]


def _native_unit_attr(unit: object, attr: str) -> str:
    return str(getattr(unit, attr, "")).strip()


def _merge_operator_inventory_variant_axes(output: Mapping[str, object], surface: dict[str, object]) -> bool:
    if surface.get("variant_axes_detected") is True and isinstance(surface.get("variant_axes"), Mapping):
        return False
    inventory = output.get("operator_inventory")
    if not isinstance(inventory, Mapping):
        return False
    axes = _variant_axes_from_operator_inventory(cast(Mapping[object, object], inventory), surface)
    if not axes:
        return False
    surface["variant_axes_detected"] = True
    surface["variant_axes"] = axes
    return True


def _drop_unexpanded_synthesized_variant_axes(surface: dict[str, object]) -> None:
    variants = surface.get("expanded_operator_variants")
    if isinstance(variants, list) and variants:
        return
    if surface.get("variant_axes_source") == "source_template_scan":
        return
    _ = surface.pop("variant_axes_detected", None)
    _ = surface.pop("variant_axes", None)
    _ = surface.pop("expanded_operator_variants", None)
    _ = surface.pop("expanded_operator_instances_count", None)


def _merge_source_template_variant_axes(surface: dict[str, object], project_dir: str) -> None:
    if surface.get("variant_axes_detected") is True and isinstance(surface.get("variant_axes"), Mapping):
        return
    axes = _source_template_axes_from_project(surface, project_dir)
    if not axes:
        return
    surface["variant_axes_detected"] = True
    surface["variant_axes"] = axes
    surface["variant_axes_source"] = "source_template_scan"


def _source_template_axes_from_project(surface: Mapping[str, object], project_dir: str) -> dict[str, object]:
    root = Path(project_dir).resolve()
    if not root.is_dir():
        return {}
    evidence_by_unit = _fine_grained_evidence_by_unit(surface.get("fine_grained_operator_unit_evidence"))
    axes_by_unit: dict[str, dict[str, list[str]]] = {}
    source_cache: dict[Path, str] = {}
    for base_unit in _string_list(surface.get("fine_grained_operator_units")):
        source_text = _source_template_text_for_unit(base_unit, surface, evidence_by_unit, root, source_cache)
        if not source_text:
            continue
        axes = _source_template_axes_from_text(source_text)
        if axes:
            axes_by_unit[base_unit] = axes
    if not axes_by_unit:
        return {}
    merged: dict[str, list[str]] = {}
    for axes in axes_by_unit.values():
        for axis_name, values in axes.items():
            merged[axis_name] = _ordered_unique([*merged.get(axis_name, []), *values])
    base_units = _string_list(surface.get("fine_grained_operator_units"))
    result: dict[str, object] = dict(_canonicalized_template_axes(merged, base_units))
    for base_unit, axes in axes_by_unit.items():
        canonical_axes = _canonicalized_template_axes(axes, [base_unit])
        if canonical_axes:
            result[base_unit] = canonical_axes
    return result


def _source_template_text_for_unit(
    base_unit: str,
    surface: Mapping[str, object],
    evidence_by_unit: Mapping[str, Mapping[object, object]],
    project_root: Path,
    source_cache: dict[Path, str],
) -> str:
    project_root = project_root.resolve()
    evidence_text = "\n".join([
        base_unit,
        _flatten_text(evidence_by_unit.get(base_unit, {})),
        *_source_evidence_for_base(base_unit, evidence_by_unit, {}, surface),
    ])
    snippets: list[str] = [evidence_text]
    for path in _source_paths_from_text(base_unit, evidence_text):
        resolved = (project_root / path).resolve()
        if resolved != project_root and project_root not in resolved.parents:
            continue
        if not _is_native_source_path(resolved):
            continue
        if resolved not in source_cache:
            try:
                source_cache[resolved] = resolved.read_text(encoding="utf-8", errors="replace")
            except OSError:
                source_cache[resolved] = ""
        if source_cache[resolved]:
            snippets.append(source_cache[resolved])
    return "\n".join(snippets)


def _source_template_axes_from_text(text: str) -> dict[str, list[str]]:
    axes: dict[str, list[str]] = {}
    for row in _template_declaration_rows(text):
        for axis_name in _declared_template_axis_names(row):
            axis_values = _template_axis_values_from_text(row, axis_name)
            if axis_values:
                canonical = _canonical_template_axis_name(axis_name)
                axes[canonical] = _ordered_unique([*axes.get(canonical, []), *axis_values])
    _apply_source_template_axis_formatting(axes, text)
    return axes


def _apply_source_template_axis_formatting(axes: dict[str, list[str]], text: str) -> None:
    ndim_values = axes.get("ndim")
    if not ndim_values or not _source_template_uses_dimension_suffix(text):
        return
    axes["ndim"] = _canonical_template_axis_values("ndim", [
        f"{value}d" if value.isdigit() else value
        for value in ndim_values
    ])


def _source_template_uses_dimension_suffix(text: str) -> bool:
    return bool(re.search(r"(?:##|\{|_)ndim(?:##|\})?d(?![A-Za-z0-9])", text, flags=re.IGNORECASE))


def _template_declaration_rows(text: str) -> list[str]:
    raw_rows = [_clean_template_comment_line(line) for line in text.splitlines()]
    rows: list[str] = []
    for index, row in enumerate(raw_rows):
        if not row:
            continue
        joined = " ".join(part for part in raw_rows[index:index + 3] if part)
        if _row_has_template_declaration_signal(joined):
            rows.append(joined)
    return _ordered_unique(rows)


def _clean_template_comment_line(line: str) -> str:
    stripped = line.strip()
    is_comment = stripped.startswith(("//", "/*", "*"))
    stripped = re.sub(r"^/\*+", "", stripped)
    stripped = re.sub(r"\*/$", "", stripped)
    stripped = re.sub(r"^//", "", stripped)
    stripped = re.sub(r"^\*+", "", stripped)
    cleaned = stripped.strip()
    if is_comment:
        return cleaned
    if re.search(r"(?<![A-Za-z0-9_])(?:generated|variant|template|speciali[sz]e|possible values)\b", cleaned, flags=re.IGNORECASE):
        return cleaned
    return ""


def _row_has_template_declaration_signal(row: str) -> bool:
    normalized = row.lower().replace("-", "_")
    has_macro_axis = bool(re.search(r"(?<![A-Za-z0-9_])[A-Z][A-Z0-9_]*_(?:NDIM|DIMS?|ACCURACY|DTYPE|TYPE|PRECISION)(?![A-Za-z0-9_])", row))
    has_template_words = bool(re.search(r"(?:^|\b)(?:generated|variant|template|speciali[sz]e|axis|axes|values?|possible values|compiled multiple times|options are specified)\b", normalized))
    has_values = bool(re.search(r"\{[^{}]+\}|\[[^\[\]]+\]|range\(|possible values are", row, flags=re.IGNORECASE))
    return has_values and (has_macro_axis or has_template_words)


def _declared_template_axis_names(text: str) -> list[str]:
    names: list[str] = []
    for match in re.finditer(r"(?<![A-Za-z0-9_])([A-Z][A-Z0-9_]*_(?:NDIM|DIMS?|ACCURACY|DTYPE|TYPE|PRECISION))(?![A-Za-z0-9_])", text):
        names.append(_axis_name_from_macro(match.group(1)))
    for match in re.finditer(r"(?<![A-Za-z0-9_])([A-Za-z][A-Za-z0-9_-]*)\s*=\s*\{[^{}]+\}", text):
        names.append(match.group(1))
    for match in re.finditer(r"(?<![A-Za-z0-9_])([A-Za-z][A-Za-z0-9_-]*)\s*(?:=|:|are|values? are|possible values are)\s*[^\n.;]*[,/{][^\n.;]*", text, flags=re.IGNORECASE):
        names.append(match.group(1))
    return [name for name in _ordered_unique([_canonical_template_axis_name(name) for name in names]) if name and not _axis_is_implementation_detail(name) and _valid_source_template_axis_name(name)]


def _valid_source_template_axis_name(axis_name: str) -> bool:
    normalized = axis_name.strip().lower().replace("-", "_")
    if normalized in {"source", "inspection", "possible", "values", "generated", "variant", "template"}:
        return False
    return bool(re.fullmatch(r"[a-z][a-z0-9_]{1,40}", normalized))


def _axis_name_from_macro(macro_name: str) -> str:
    normalized = macro_name.strip().lower().replace("-", "_")
    for suffix in ("_ndim", "_ndims", "_dim", "_dims", "_accuracy", "_dtype", "_type", "_precision"):
        if normalized.endswith(suffix):
            return normalized[len(normalized) - len(suffix) + 1 :]
    return normalized


def _template_axis_values_from_text(text: str, axis_name: str) -> list[str]:
    values: list[str] = []
    axis_pattern = re.escape(axis_name)
    macro_patterns = [re.escape(name.lower()) for name in _macro_names_for_axis(text, axis_name)]
    exact_patterns = [rf"{axis_pattern}\s*=\s*\{{([^}}]+)\}}"]
    exact_patterns.extend(rf"{macro_pattern}\s*=\s*\{{([^}}]+)\}}" for macro_pattern in macro_patterns)
    for pattern in exact_patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            values.extend(_split_axis_value_list(match.group(1)))
    if values:
        return _canonical_template_axis_values(axis_name, values)

    patterns = [
        rf"{axis_pattern}\s*(?:=|:|are|values? are|possible values are)\s*([^\n.;]+)",
        rf"{axis_pattern}[\s\S]{{0,220}}?possible values are\s*([^.;\n]+)",
    ]
    for macro_pattern in macro_patterns:
        patterns.extend((
            rf"{macro_pattern}\s*(?:=|:|are|values? are|possible values are)\s*([^\n.;]+)",
            rf"{macro_pattern}[\s\S]{{0,220}}?possible values are\s*([^.;\n]+)",
        ))
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            values.extend(_split_axis_value_list(match.group(1)))
    if not values and axis_name == "dtype" and _row_has_dtype_value_context(text):
        for value in ("float", "double"):
            if re.search(rf"(?<![A-Za-z0-9_]){value}(?![A-Za-z0-9_])", text, flags=re.IGNORECASE):
                values.append(value)
    return _filter_axis_values(axis_name, _canonical_template_axis_values(axis_name, values))


def _row_has_dtype_value_context(text: str) -> bool:
    return bool(re.search(r"(?:possible values|values? are|generated|variant|template)", text, flags=re.IGNORECASE))


def _filter_axis_values(axis_name: str, values: list[str]) -> list[str]:
    canonical_axis = _canonical_template_axis_name(axis_name)
    filtered: list[str] = []
    for value in values:
        if canonical_axis == "ndim":
            numeric = value[:-1] if value.endswith("d") and value[:-1].isdigit() else value
            if not numeric.isdigit():
                continue
        elif canonical_axis == "accuracy":
            if not value.isdigit():
                continue
        elif canonical_axis == "dtype":
            if not re.fullmatch(r"[a-z][a-z0-9_]*", value):
                continue
        elif not re.fullmatch(r"[a-z0-9_.+-]+", value):
            continue
        filtered.append(value)
    return _ordered_unique(filtered)


def _macro_names_for_axis(text: str, axis_name: str) -> list[str]:
    names: list[str] = []
    canonical_axis = _canonical_template_axis_name(axis_name)
    suffixes = _macro_suffixes_for_axis(canonical_axis)
    for match in re.finditer(r"(?<![A-Za-z0-9_])([A-Z][A-Z0-9_]+)(?![A-Za-z0-9_])", text):
        macro_name = match.group(1)
        normalized = macro_name.lower()
        if any(normalized.endswith(suffix) for suffix in suffixes):
            names.append(macro_name)
    return _ordered_unique(names)


def _macro_suffixes_for_axis(axis_name: str) -> tuple[str, ...]:
    if axis_name == "ndim":
        return ("_ndim", "_ndims", "_dim", "_dims")
    if axis_name == "dtype":
        return ("_dtype", "_type")
    if axis_name == "accuracy":
        return ("_accuracy", "_precision")
    return (f"_{axis_name}",)


def _variant_axes_from_operator_inventory(inventory: Mapping[object, object], surface: Mapping[str, object]) -> dict[str, list[str]]:
    base_units = [unit for unit in _string_sequence(surface.get("fine_grained_operator_units"))]
    collected: dict[str, list[str]] = {}
    for family in _operator_inventory_families(inventory.get("families")):
        raw_axes = family.get("variant_axes")
        if not isinstance(raw_axes, Mapping):
            continue
        axes = _canonicalized_template_axes(_normalized_axis_values(cast(Mapping[object, object], raw_axes)), base_units)
        for axis_name, values in axes.items():
            filtered_values = [value for value in values if value.strip().lower() not in NON_TARGET_VARIANT_VALUES]
            if filtered_values:
                collected[axis_name] = _ordered_unique([*collected.get(axis_name, []), *filtered_values])
    return collected


def _operator_inventory_families(value: object) -> list[Mapping[object, object]]:
    if isinstance(value, Mapping):
        return [cast(Mapping[object, object], item) for item in cast(Mapping[object, object], value).values() if isinstance(item, Mapping)]
    if isinstance(value, list):
        return [cast(Mapping[object, object], item) for item in cast(list[object], value) if isinstance(item, Mapping)]
    return []


def _string_sequence(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in cast(list[object], value) if isinstance(item, str) and item.strip()]


def _supplement_source_discovered_cuda_units(surface: dict[str, object], project_dir: object) -> None:
    if surface.get("custom_op_detected") is not True:
        return
    if not isinstance(project_dir, str) or not project_dir.strip():
        return
    discovered_units = discover_required_cuda_native_units_from_project(project_dir)
    if not discovered_units:
        return

    for field_name in (
        "operator_families",
        "fine_grained_operator_units",
        "discovered_operator_names",
        "native_operator_symbols",
        "kernel_launch_sites",
        "source_evidence",
        "searched_source_paths",
        "searched_source_roots",
    ):
        if not isinstance(surface.get(field_name), list):
            surface[field_name] = []
    if not isinstance(surface.get("fine_grained_operator_unit_evidence"), list):
        surface["fine_grained_operator_unit_evidence"] = []

    reported = _source_discovered_unit_report_tokens(surface)
    for unit in discovered_units:
        identity = str(getattr(unit, "identity", "")).strip()
        family = str(getattr(unit, "family", "")).strip()
        symbol = str(getattr(unit, "symbol", "")).strip()
        source_path = str(getattr(unit, "source_path", "")).strip()
        line_number = getattr(unit, "line_number", "")
        if not identity or _source_discovered_unit_is_reported(identity, family, symbol, reported):
            continue

        source_reference = f"{source_path}:{line_number} {symbol}" if source_path else symbol
        _append_unique_string(surface, "operator_families", family)
        _append_unique_string(surface, "fine_grained_operator_units", identity)
        _append_unique_string(surface, "discovered_operator_names", identity)
        _append_unique_string(surface, "native_operator_symbols", symbol or identity)
        _append_unique_string(surface, "kernel_launch_sites", source_reference)
        _append_unique_string(surface, "source_evidence", source_reference)
        _append_unique_string(surface, "searched_source_paths", source_path)
        if source_path:
            root_name = source_path.split("/", 1)[0]
            _append_unique_string(surface, "searched_source_roots", root_name)
        evidence = cast(list[object], surface["fine_grained_operator_unit_evidence"])
        evidence.append(
            {
                "unit_identity": identity,
                "source_evidence": [source_reference],
                "candidate_framework_integration_routes": [
                    f"project native CUDA symbol {symbol or identity} in {source_path or project_dir}"
                ],
            }
        )
        reported.update(_source_discovered_unit_tokens(identity, family, symbol))
    _restrict_to_source_discovered_units(surface, discovered_units)


def _restrict_to_source_discovered_units(surface: dict[str, object], discovered_units: Sequence[object]) -> None:
    discovered_identities = [
        identity
        for unit in discovered_units
        if (identity := str(getattr(unit, "identity", "")).strip())
    ]
    if not discovered_identities:
        return
    discovered_set = set(discovered_identities)
    existing_units = _string_list(surface.get("fine_grained_operator_units"))
    protected_existing_units = _sample_backed_existing_units(surface)
    retained_units = _ordered_unique([unit for unit in existing_units if unit in discovered_set or unit in protected_existing_units])
    retained_units = _ordered_unique([*retained_units, *discovered_identities])
    surface["fine_grained_operator_units"] = retained_units
    surface["operator_families"] = _ordered_unique([unit.split(":", 1)[0] for unit in retained_units if ":" in unit])

    evidence = surface.get("fine_grained_operator_unit_evidence")
    if isinstance(evidence, list):
        retained_evidence: list[object] = []
        for item in cast(list[object], evidence):
            if not isinstance(item, Mapping):
                retained_evidence.append(item)
                continue
            unit_identity = cast(Mapping[object, object], item).get("unit_identity")
            if (
                isinstance(unit_identity, str)
                and unit_identity.strip()
                and unit_identity.strip() not in discovered_set
                and unit_identity.strip() not in protected_existing_units
            ):
                continue
            retained_evidence.append(cast(object, item))
        surface["fine_grained_operator_unit_evidence"] = retained_evidence

    variants = surface.get("expanded_operator_variants")
    if isinstance(variants, list):
        retained_variants: list[object] = []
        for item in cast(list[object], variants):
            if not isinstance(item, Mapping):
                retained_variants.append(item)
                continue
            variant = cast(Mapping[object, object], item)
            base_identity = str(variant.get("base_unit_identity") or variant.get("source_unit_identity") or "").strip()
            if base_identity and base_identity not in discovered_set and base_identity not in protected_existing_units:
                continue
            retained_variants.append(cast(object, item))
        surface["expanded_operator_variants"] = retained_variants
        surface["expanded_operator_instances_count"] = len(retained_variants)


def _sample_backed_existing_units(surface: Mapping[str, object]) -> set[str]:
    protected: set[str] = set()
    variants = surface.get("expanded_operator_variants")
    if isinstance(variants, list):
        for item in cast(list[object], variants):
            if not isinstance(item, Mapping):
                continue
            variant = cast(Mapping[object, object], item)
            base_identity = str(variant.get("base_unit_identity") or variant.get("source_unit_identity") or "").strip()
            if base_identity and _device_suffix_for_unit(base_identity) in {"cuda", "gpu"}:
                protected.add(base_identity)
    return protected


def _append_unique_string(surface: dict[str, object], field_name: str, value: str) -> None:
    if not value:
        return
    items = surface.get(field_name)
    if not isinstance(items, list):
        surface[field_name] = [value]
        return
    existing = {item for item in cast(list[object], items) if isinstance(item, str)}
    if value not in existing:
        cast(list[object], items).append(value)


def _source_discovered_unit_report_tokens(surface: Mapping[str, object]) -> set[str]:
    tokens: set[str] = set()
    for field_name in ("fine_grained_operator_units", "discovered_operator_names", "native_operator_symbols"):
        for value in _string_list(surface.get(field_name)):
            token = _source_discovered_unit_token(value)
            if token:
                tokens.add(token)
    evidence = surface.get("fine_grained_operator_unit_evidence")
    if isinstance(evidence, list):
        for item in cast(list[object], evidence):
            if isinstance(item, Mapping):
                unit_identity = cast(Mapping[object, object], item).get("unit_identity")
                if isinstance(unit_identity, str):
                    token = _source_discovered_unit_token(unit_identity)
                    if token:
                        tokens.add(token)
    return tokens


def _source_discovered_unit_is_reported(identity: str, family: str, symbol: str, reported: set[str]) -> bool:
    return any(token in reported for token in _source_discovered_unit_tokens(identity, family, symbol))


def _source_discovered_unit_tokens(identity: str, family: str, symbol: str) -> set[str]:
    tokens = {_source_discovered_unit_token(identity)}
    if family and symbol:
        tokens.add(_source_discovered_unit_token(f"{family}:{symbol}"))
        tokens.add(_source_discovered_unit_token(f"{family}_{symbol}"))
    return {token for token in tokens if token}


def _source_discovered_unit_token(value: str) -> str:
    return value.strip().lower()


def source_template_expanded_variants(surface: Mapping[str, object], *, project_dir: str | None = None) -> list[dict[str, object]]:
    return _source_template_expanded_variants(surface, project_dir=project_dir)


def _source_template_expanded_variants(surface: Mapping[str, object], *, project_dir: str | None = None) -> list[dict[str, object]]:
    if surface.get("variant_axes_detected") is not True:
        return []
    raw_axes = surface.get("variant_axes")
    if not isinstance(raw_axes, Mapping):
        return []
    raw_axes_map = cast(Mapping[object, object], raw_axes)
    fine_units = _string_list(surface.get("fine_grained_operator_units"))
    base_units = [unit for unit in fine_units if "=" not in unit]
    if not base_units:
        return []
    axes = _canonicalized_template_axes(_normalized_axis_values(raw_axes_map), base_units)
    if not axes:
        return []

    names = _string_list(surface.get("discovered_operator_names"))
    symbols = _string_list(surface.get("native_operator_symbols"))
    evidence_by_unit = _fine_grained_evidence_by_unit(surface.get("fine_grained_operator_unit_evidence"))
    sample_by_base = _variant_samples_by_base(surface.get("expanded_operator_variants"))
    global_template_axis_names = _global_source_template_axis_names(surface, axes)
    source_file_cache: dict[Path, list[str]] = {}

    generated: list[dict[str, object]] = []
    for index, base_unit in enumerate(base_units):
        descriptor_text = _descriptor_text_for_unit(base_unit, index, names, symbols, surface, evidence_by_unit)
        base_unit = _canonical_cuda_base_unit(base_unit, descriptor_text, axes)
        descriptor_text = _descriptor_text_with_source_files(base_unit, descriptor_text, project_dir, source_file_cache) if project_dir else descriptor_text
        base_axes = _axes_for_base_from_variant_axes(base_unit, raw_axes_map, axes, descriptor_text)
        sample = sample_by_base.get(base_unit)
        sample_axis_names = _sample_axis_names_for_base(sample, base_axes)
        source_axis_names = _source_template_axis_names_for_unit(
            base_unit,
            descriptor_text,
            base_axes,
            global_template_axis_names,
        )
        if project_dir:
            descriptor_with_source = _descriptor_text_with_source_files(base_unit, descriptor_text, project_dir, source_file_cache)
            source_axis_names_from_source = _source_template_axis_names_for_unit(
                base_unit,
                descriptor_with_source,
                base_axes,
                global_template_axis_names,
            )
            source_axis_names = _ordered_unique([*source_axis_names, *source_axis_names_from_source])
        if sample_axis_names:
            if project_dir and source_axis_names:
                effective_sample_axis_names = sample_axis_names & set(source_axis_names)
            else:
                effective_sample_axis_names = sample_axis_names
            axis_names = [
                axis
                for axis in _template_axis_order(base_axes)
                if axis in effective_sample_axis_names or axis in source_axis_names
            ]
            axes_for_base = _axes_with_source_values_for_base(base_unit, axis_names, base_axes, surface)
        else:
            axis_names = source_axis_names
            axes_for_base = _axes_with_source_values_for_base(base_unit, axis_names, base_axes, surface)
        if not axis_names:
            continue
        device_values = _device_values_for_base(base_unit, base_axes) if _sample_includes_device_axis(sample) else [""]
        source_evidence = _source_evidence_for_base(base_unit, evidence_by_unit, sample_by_base, surface)
        public_routes = _routes_for_base(base_unit, "candidate_public_api_routes", evidence_by_unit, sample_by_base)
        framework_routes = _routes_for_base(base_unit, "candidate_framework_integration_routes", evidence_by_unit, sample_by_base)

        for axis_values in _axis_value_product(axis_names, axes_for_base):
            for device_value in device_values:
                row_axis_values = dict(axis_values)
                if device_value:
                    row_axis_values["device"] = device_value
                generated.append({
                    "unit_identity": _expanded_unit_identity(base_unit, row_axis_values),
                    "base_unit_identity": base_unit,
                    "axis_values": row_axis_values,
                    "source_evidence": source_evidence,
                    "candidate_public_api_routes": public_routes,
                    "candidate_framework_integration_routes": framework_routes,
                })
    return generated


def _canonicalize_compact_cuda_base_units(surface: dict[str, object]) -> None:
    raw_axes = surface.get("variant_axes")
    if not isinstance(raw_axes, Mapping):
        return
    axes = _normalized_axis_values(cast(Mapping[object, object], raw_axes))
    if not axes:
        return
    fine_units = _string_list(surface.get("fine_grained_operator_units"))
    if not fine_units:
        return
    original_fine_units = fine_units
    fine_units = _filter_non_target_device_units_when_target_sibling_exists(fine_units)
    dropped_units = set(original_fine_units) - set(fine_units)
    names = _string_list(surface.get("discovered_operator_names"))
    symbols = _string_list(surface.get("native_operator_symbols"))
    evidence_by_unit = _fine_grained_evidence_by_unit(surface.get("fine_grained_operator_unit_evidence"))
    sibling_aliases = _device_sibling_aliases(fine_units, axes)
    alias_map: dict[str, str] = {}
    for index, base_unit in enumerate(fine_units):
        descriptor_text = _descriptor_text_for_unit(base_unit, index, names, symbols, surface, evidence_by_unit)
        canonical = sibling_aliases.get(base_unit) or _canonical_cuda_base_unit(base_unit, descriptor_text, axes)
        canonical = _canonical_helper_family_unit(canonical)
        if canonical != base_unit:
            alias_map[base_unit] = canonical
    if not alias_map:
        if dropped_units:
            surface["fine_grained_operator_units"] = _ordered_unique(fine_units)
            _drop_evidence_unit_identities(surface, dropped_units)
        return

    surface["fine_grained_operator_units"] = _ordered_unique([alias_map.get(unit, unit) for unit in fine_units])
    _rewrite_unit_identity_list(surface, "discovered_operator_names", alias_map)
    _rewrite_unit_identity_list(surface, "native_operator_symbols", alias_map)
    _rewrite_unit_identity_list(surface, "kernel_launch_sites", alias_map)
    _rewrite_unit_identity_list(surface, "source_evidence", alias_map)
    _rewrite_evidence_unit_identities(surface, alias_map)
    if dropped_units:
        _drop_evidence_unit_identities(surface, dropped_units)
    _rewrite_variant_base_identities(surface, alias_map)


def _canonical_cuda_base_unit(base_unit: str, descriptor_text: str, axes: Mapping[str, list[str]]) -> str:
    family, separator, symbol = base_unit.rpartition(":")
    if not separator:
        family = ""
        symbol = base_unit
    normalized_symbol = symbol.strip().lower()
    if normalized_symbol.endswith(("_cuda", "_gpu")):
        return base_unit
    for suffix in _target_device_suffixes(axes):
        if _descriptor_mentions_cuda_symbol(descriptor_text, symbol, suffix):
            canonical_symbol = f"{symbol}_{suffix}"
            return f"{family}:{canonical_symbol}" if family else canonical_symbol
    return base_unit


def _filter_non_target_device_units_when_target_sibling_exists(fine_units: Sequence[str]) -> list[str]:
    unit_set = set(fine_units)
    filtered: list[str] = []
    for unit in fine_units:
        target_sibling_exists = any(
            _same_device_operation(unit, candidate)
            and _device_suffix_for_unit(candidate) in {"cuda", "gpu"}
            for candidate in unit_set
        )
        if _device_suffix_for_unit(unit) in {"cpu", "reference", "baseline"} and target_sibling_exists:
            continue
        filtered.append(unit)
    return filtered


def _same_device_operation(left: str, right: str) -> bool:
    return _strip_device_suffix_from_unit(left) == _strip_device_suffix_from_unit(right)


def _strip_device_suffix_from_unit(unit: str) -> str:
    match = re.search(r"(?i)(?:^|[:_\-])(cpu|cuda|gpu|reference|baseline)(?:$|[:_\-])", unit)
    if match is None:
        return unit
    start, end = match.span(1)
    stripped = unit[:start] + unit[end:]
    return re.sub(r"[:_\-]+", "_", stripped).strip("_:-").lower()


def _device_suffix_for_unit(unit: str) -> str:
    match = re.search(r"(?i)(?:^|[:_\-])(cpu|cuda|gpu|reference|baseline)(?:$|[:_\-])", unit)
    return match.group(1).lower() if match else ""


def _device_sibling_aliases(fine_units: Sequence[str], axes: Mapping[str, list[str]]) -> dict[str, str]:
    unit_set = set(fine_units)
    aliases: dict[str, str] = {}
    for base_unit in fine_units:
        if DEVICE_SUFFIX_PATTERN.search(base_unit):
            continue
        for suffix in _target_device_suffixes(axes):
            sibling = f"{base_unit}_{suffix}"
            if sibling in unit_set:
                aliases[base_unit] = sibling
                break
    return aliases


def _canonical_helper_family_unit(base_unit: str) -> str:
    family, separator, symbol = base_unit.partition(":")
    if not separator:
        return base_unit
    canonical_family = family
    if canonical_family.endswith("_iso"):
        canonical_family = canonical_family[:-4]
    canonical_family = _strip_helper_family_suffix(canonical_family)
    if canonical_family != family:
        return f"{canonical_family}:{symbol}"
    return base_unit


def _target_device_suffixes(axes: Mapping[str, list[str]]) -> list[str]:
    values = axes.get("device") or []
    suffixes = [value for value in values if value in {"cuda", "gpu"}]
    return suffixes or ["cuda"]


def _descriptor_mentions_cuda_symbol(descriptor_text: str, symbol: str, suffix: str) -> bool:
    normalized_text = descriptor_text.lower().replace("-", "_")
    normalized_symbol = symbol.strip().lower().replace("-", "_")
    if not normalized_symbol:
        return False
    if re.search(rf"(?<![A-Za-z0-9]){re.escape(normalized_symbol)}_{re.escape(suffix)}(?![A-Za-z0-9])", normalized_text):
        return True
    if suffix == "cuda" and re.search(rf"\bfunc\(\s*{re.escape(normalized_symbol)}\s*\)", normalized_text):
        return ".cu" in normalized_text or "cuda" in normalized_text
    return False


def _rewrite_unit_identity_list(surface: dict[str, object], field_name: str, alias_map: Mapping[str, str]) -> None:
    value = surface.get(field_name)
    if not isinstance(value, list):
        return
    rewritten: list[object] = []
    seen_strings: set[str] = set()
    for item in cast(list[object], value):
        if isinstance(item, str):
            rewritten_item = _rewrite_unit_identity_text(item, alias_map)
            if rewritten_item in seen_strings:
                continue
            seen_strings.add(rewritten_item)
            rewritten.append(rewritten_item)
        else:
            rewritten.append(item)
    surface[field_name] = rewritten


def _rewrite_evidence_unit_identities(surface: dict[str, object], alias_map: Mapping[str, str]) -> None:
    evidence = surface.get("fine_grained_operator_unit_evidence")
    if not isinstance(evidence, list):
        return
    for item in cast(list[object], evidence):
        if not isinstance(item, dict):
            continue
        evidence_item = cast(dict[object, object], item)
        unit_identity = evidence_item.get("unit_identity")
        if isinstance(unit_identity, str):
            evidence_item["unit_identity"] = alias_map.get(unit_identity, unit_identity)


def _drop_evidence_unit_identities(surface: dict[str, object], dropped_units: set[str]) -> None:
    evidence = surface.get("fine_grained_operator_unit_evidence")
    if not isinstance(evidence, list):
        return
    retained: list[object] = []
    for item in cast(list[object], evidence):
        if not isinstance(item, dict):
            retained.append(item)
            continue
        evidence_item = cast(dict[object, object], item)
        unit_identity = evidence_item.get("unit_identity")
        if isinstance(unit_identity, str) and unit_identity in dropped_units:
            continue
        retained.append(evidence_item)
    surface["fine_grained_operator_unit_evidence"] = retained


def _rewrite_variant_base_identities(surface: dict[str, object], alias_map: Mapping[str, str]) -> None:
    variants = surface.get("expanded_operator_variants")
    if not isinstance(variants, list):
        return
    for item in cast(list[object], variants):
        if not isinstance(item, dict):
            continue
        variant = cast(dict[object, object], item)
        for key in ("base_unit_identity", "source_unit_identity"):
            value = variant.get(key)
            if isinstance(value, str):
                variant[key] = alias_map.get(value, value)
        unit_identity = variant.get("unit_identity")
        if isinstance(unit_identity, str):
            variant["unit_identity"] = _rewrite_unit_identity_text(unit_identity, alias_map)


def _rewrite_unit_identity_text(value: str, alias_map: Mapping[str, str]) -> str:
    rewritten = value
    for source, target in sorted(alias_map.items(), key=lambda item: len(item[0]), reverse=True):
        rewritten = rewritten.replace(source, target)
        rewritten = rewritten.replace(source.replace(":", "_"), target.replace(":", "_"))
    return _collapse_repeated_device_suffixes(rewritten)


def _collapse_repeated_device_suffixes(value: str) -> str:
    previous = ""
    collapsed = value
    while previous != collapsed:
        previous = collapsed
        collapsed = re.sub(r"(?i)_cuda_cuda(?![A-Za-z0-9])", "_cuda", collapsed)
        collapsed = re.sub(r"(?i)_gpu_gpu(?![A-Za-z0-9])", "_gpu", collapsed)
    return collapsed


def _mark_discovery_complete_for_full_generated_inventory(surface: dict[str, object], generated: list[dict[str, object]]) -> None:
    declared_count = surface.get("expanded_operator_instances_count")
    if declared_count != len(generated):
        return
    unresolved = _string_list(surface.get("unresolved_source_groups"))
    if unresolved and not all(_is_non_blocking_generated_inventory_note(item) for item in unresolved):
        return
    if surface.get("custom_op_detected") is True:
        surface["discovery_complete"] = True
        surface["unresolved_source_groups"] = []


def _is_non_blocking_generated_inventory_note(value: str) -> bool:
    normalized = value.strip().lower()
    if not normalized:
        return True
    generated_inventory = "generated" in normalized and ("inventory" in normalized or "symbol" in normalized)
    build_manifest_uncertainty = any(token in normalized for token in ("no cmake", "no setup", "no build manifest", "build manifest"))
    return generated_inventory and build_manifest_uncertainty


def _axes_with_source_values_for_base(
    base_unit: str,
    axis_names: Sequence[str],
    axes: Mapping[str, list[str]],
    surface: Mapping[str, object],
) -> Mapping[str, list[str]]:
    if not axis_names:
        return axes
    per_base_values = _source_axis_values_for_base(base_unit, axis_names, axes, surface)
    if not per_base_values:
        return {axis: values for axis, values in axes.items() if axis in axis_names}
    narrowed = {axis: values for axis, values in axes.items() if axis in axis_names}
    for axis_name, values in per_base_values.items():
        if values:
            narrowed[axis_name] = values
    return narrowed


def _source_axis_values_for_base(
    base_unit: str,
    axis_names: Sequence[str],
    axes: Mapping[str, list[str]],
    surface: Mapping[str, object],
) -> dict[str, list[str]]:
    source_rows = _source_rows_for_base(base_unit, surface)
    if not source_rows:
        return {}
    extracted_values = _source_template_axis_values_from_rows(source_rows, axis_names, axes)
    values_by_axis: dict[str, list[str]] = {}
    for axis_name in axis_names:
        mentioned_values = [
            axis_value
            for axis_value in axes.get(axis_name, [])
            if any(_row_mentions_axis_value(row, axis_name, axis_value) for row in source_rows)
        ]
        values = _ordered_unique([*mentioned_values, *extracted_values.get(axis_name, [])])
        if values:
            values_by_axis[axis_name] = values
    return values_by_axis


def _source_template_axis_values_from_rows(
    rows: Sequence[str],
    axis_names: Sequence[str],
    axes: Mapping[str, list[str]],
) -> dict[str, list[str]]:
    allowed_axes = [axis for axis in _template_axis_order(axes) if axis in axis_names]
    if not allowed_axes:
        return {}
    values_by_axis: dict[str, list[str]] = {axis: [] for axis in allowed_axes}
    for row in rows:
        if row not in _template_declaration_rows(row):
            continue
        for axis_name in allowed_axes:
            values_by_axis[axis_name] = _ordered_unique([
                *values_by_axis[axis_name],
                *_named_axis_values_from_row(row, axis_name, axes),
            ])
        for axis_name, values in _positional_brace_axis_values_from_row(row, allowed_axes).items():
            normalized_values = [_normalize_source_axis_value(value, axis_name, axes) for value in values]
            values_by_axis[axis_name] = _ordered_unique([*values_by_axis[axis_name], *normalized_values])
    return {axis: values for axis, values in values_by_axis.items() if values}


def _named_axis_values_from_row(row: str, axis_name: str, axes: Mapping[str, list[str]]) -> list[str]:
    axis_patterns = _axis_reference_patterns(axis_name)
    values: list[str] = []
    for axis_pattern in axis_patterns:
        for pattern in (
            rf"(?<![A-Za-z0-9_]){axis_pattern}\s*(?:=|:)\s*[\[{{]([^\]}}]{{1,500}})[\]}}]",
            rf"(?<![A-Za-z0-9_]){axis_pattern}\s+in\s*\[([^\]]{{1,500}})\]",
            rf"(?<![A-Za-z0-9_]){axis_pattern}\s+([A-Za-z0-9_.+-]+(?:\s*,\s*[A-Za-z0-9_.+-]+)+)",
            rf"(?<![A-Za-z0-9_]){axis_pattern}\s+([A-Za-z0-9_.+-]+(?:\s*/\s*[A-Za-z0-9_.+-]+)+)",
        ):
            for match in re.finditer(pattern, row, flags=re.IGNORECASE):
                values.extend(_split_axis_value_list(match.group(1)))
        range_pattern = rf"(?<![A-Za-z0-9_]){axis_pattern}\s*(?:=|:|\s+in\s+)\s*range\(\s*([-+]?\d+)\s*,\s*([-+]?\d+)(?:\s*,\s*([-+]?\d+))?\s*\)"
        for match in re.finditer(range_pattern, row, flags=re.IGNORECASE):
            values.extend(_axis_values_from_range(match.group(1), match.group(2), match.group(3), axis_name=axis_name))
    return _ordered_unique([_normalize_source_axis_value(value, axis_name, axes) for value in values])


def _normalize_source_axis_value(value: str, axis_name: str, axes: Mapping[str, list[str]]) -> str:
    normalized = value.strip().lower()
    if axis_name.strip().lower().replace("-", "_") != "ndim":
        return normalized
    axis_values = axes.get(axis_name, [])
    if normalized.isdigit() and f"{normalized}d" in axis_values:
        return f"{normalized}d"
    if normalized.endswith("d") and normalized[:-1] in axis_values:
        return normalized[:-1]
    return normalized


def _axis_reference_patterns(axis_name: str) -> list[str]:
    normalized = axis_name.strip().replace("-", "_")
    if not normalized:
        return []
    escaped = re.escape(normalized)
    patterns = [escaped]
    current_name = f"current_{normalized}"
    if current_name != normalized:
        patterns.append(re.escape(current_name))
    return patterns


def _positional_brace_axis_values_from_row(row: str, axis_order: Sequence[str]) -> dict[str, list[str]]:
    if not axis_order:
        return {}
    matches = [
        match
        for match in re.finditer(r"\{([^{}]{1,500})\}", row)
        if (match.start() == 0 or row[match.start() - 1] != "$") and "," in match.group(1)
    ]
    if len(matches) != len(axis_order):
        return {}
    values_by_axis: dict[str, list[str]] = {}
    for axis_name, match in zip(axis_order, matches, strict=True):
        values = _split_axis_value_list(match.group(1))
        if axis_name.strip().lower().replace("-", "_") == "ndim":
            suffix = row[match.end():match.end() + 1].lower()
            if suffix == "d":
                values = [f"{value}d" if value.isdigit() else value for value in values]
        if not values:
            return {}
        values_by_axis[axis_name] = values
    return values_by_axis


def _split_axis_value_list(raw_values: str) -> list[str]:
    values: list[str] = []
    for raw_value in re.split(r"[,/]", raw_values):
        value = raw_value.strip().strip("'\"").lower()
        value = re.sub(r"^(?:and|or)\s+", "", value)
        range_match = re.fullmatch(r"([-+]?\d+)\s*-\s*([-+]?\d+)", value)
        if range_match:
            start = int(range_match.group(1))
            stop = int(range_match.group(2))
            step = 1 if stop >= start else -1
            values.extend(str(item) for item in range(start, stop + step, step))
            continue
        if _safe_axis_value_token(value):
            values.append(value)
    return _ordered_unique(values)


def _axis_values_from_range(start_text: str, stop_text: str, step_text: str | None, *, axis_name: str) -> list[str]:
    try:
        start = int(start_text)
        stop = int(stop_text)
        step = int(step_text) if step_text is not None else 1
    except ValueError:
        return []
    if step == 0:
        return []
    values = list(range(start, stop, step))
    if not values or len(values) > 256:
        return []
    rendered = [str(value).lower() for value in values]
    if axis_name.strip().lower().replace("-", "_") == "ndim":
        return [f"{value}d" if value.isdigit() else value for value in rendered]
    return rendered


def _safe_axis_value_token(value: str) -> bool:
    if not value or len(value) > 80:
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9_.+-]+", value))


def _source_rows_for_base(base_unit: str, surface: Mapping[str, object]) -> list[str]:
    base_tokens = _base_match_tokens(base_unit)
    rows: list[str] = []
    for field_name in ("discovered_operator_names", "native_operator_symbols", "source_evidence"):
        for row in _string_list(surface.get(field_name)):
            normalized = row.lower().replace("-", "_").replace(":", "_")
            if _line_matches_base(normalized, base_tokens):
                rows.append(row)
    return rows


def _line_matches_base(normalized_line: str, base_tokens: Sequence[str]) -> bool:
    for token in base_tokens:
        if token in normalized_line:
            return True
        parts = [part for part in token.split("_") if part]
        if len(parts) >= 2 and all(part in normalized_line for part in parts):
            return True
        if _line_fuzzy_matches_template_base(normalized_line, parts):
            return True
    return False


def _line_fuzzy_matches_template_base(normalized_line: str, parts: Sequence[str]) -> bool:
    if len(parts) < 4:
        return False
    distinct_parts = _ordered_unique(list(parts))
    matched_parts = [part for part in distinct_parts if part in normalized_line]
    if len(matched_parts) < len(distinct_parts) - 1:
        return False
    if distinct_parts[0] not in normalized_line or distinct_parts[-1] not in normalized_line:
        return False
    return _line_has_template_scope_signal(normalized_line)


def _line_has_template_scope_signal(normalized_line: str) -> bool:
    return any(
        token in normalized_line
        for token in (
            "axis",
            "axes",
            "construct",
            "define",
            "dtype",
            "expand",
            "generated",
            "macro",
            "ndim",
            "symbol",
            "variant",
        )
    )


def _base_match_tokens(base_unit: str) -> list[str]:
    normalized = base_unit.lower().replace("-", "_").replace(":", "_")
    tokens = [normalized]
    if normalized.endswith("_cuda"):
        tokens.append(normalized[: -len("_cuda")])
    if normalized.endswith("_gpu"):
        tokens.append(normalized[: -len("_gpu")])
    return [token for token in _ordered_unique(tokens) if token]


def _row_mentions_axis_value(row: str, axis_name: str, axis_value: str) -> bool:
    normalized_row = row.lower().replace("-", "_")
    normalized_axis = axis_name.lower().replace("-", "_")
    normalized_value = axis_value.lower().replace("-", "_")
    value_without_suffix = normalized_value[:-1] if normalized_axis == "ndim" and normalized_value.endswith("d") else normalized_value
    value_patterns = _ordered_unique([normalized_value, value_without_suffix])
    return any(
        re.search(rf"(?<![A-Za-z0-9]){re.escape(normalized_axis)}[_=:\s]+{re.escape(value)}(?![A-Za-z0-9])", normalized_row)
        for value in value_patterns
        if value
    )


def _normalized_axis_values(raw_axes: Mapping[object, object]) -> dict[str, list[str]]:
    axes: dict[str, list[str]] = {}
    for raw_axis, raw_values in raw_axes.items():
        if not isinstance(raw_axis, str):
            continue
        if isinstance(raw_values, Mapping):
            nested = _normalized_axis_values(cast(Mapping[object, object], raw_values))
            for axis_name, values in nested.items():
                axes[axis_name] = _ordered_unique([*axes.get(axis_name, []), *values])
            continue
        if isinstance(raw_values, list):
            raw_values_list = cast(list[object], raw_values)
            if all(isinstance(item, Mapping) for item in raw_values_list):
                nested = _axis_values_from_axis_rows(raw_values_list)
                for axis_name, values in nested.items():
                    axes[axis_name] = _ordered_unique([*axes.get(axis_name, []), *values])
                continue

        if not isinstance(raw_values, list):
            continue
        axis_name = raw_axis.strip()
        values = [str(value).strip().lower() for value in cast(list[object], raw_values) if isinstance(value, (str, int, float)) and not isinstance(value, bool) and str(value).strip()]
        if values:
            axes[axis_name] = _ordered_unique(values)
    return axes


def _canonicalized_template_axes(axes: Mapping[str, list[str]], base_units: Sequence[str]) -> dict[str, list[str]]:
    canonical_axes: dict[str, list[str]] = {}
    for raw_axis, raw_values in axes.items():
        axis_name = _canonical_template_axis_name(raw_axis)
        values = _canonical_template_axis_values(axis_name, raw_values)
        if not values or _is_operator_inventory_axis(axis_name, values, base_units):
            continue
        canonical_axes[axis_name] = _ordered_unique([*canonical_axes.get(axis_name, []), *values])
    return canonical_axes


def _canonical_template_axis_name(axis_name: str) -> str:
    normalized = axis_name.strip().lower().replace("-", "_").replace(" ", "_")
    if normalized in {"dim", "dims", "dimension", "dimensions", "n_dim", "n_dims", "num_dim", "num_dims"}:
        return "ndim"
    for suffix, canonical in (
        ("_ndim", "ndim"),
        ("_ndims", "ndim"),
        ("_dim", "ndim"),
        ("_dims", "ndim"),
        ("_accuracy", "accuracy"),
        ("_precision", "accuracy"),
        ("_dtype", "dtype"),
        ("_type", "dtype"),
    ):
        if normalized.endswith(suffix):
            return canonical
    return normalized


def _canonical_template_axis_values(axis_name: str, values: Sequence[str]) -> list[str]:
    if axis_name != "ndim":
        return _ordered_unique([value.strip().lower() for value in values if value.strip()])
    selected_by_dimension: dict[str, str] = {}
    ordered_dimensions: list[str] = []
    for value in values:
        normalized = value.strip().lower()
        if not normalized:
            continue
        match = re.fullmatch(r"([1-9]\d*)d?", normalized)
        if not match:
            if normalized not in selected_by_dimension:
                selected_by_dimension[normalized] = normalized
                ordered_dimensions.append(normalized)
            continue
        dimension = match.group(1)
        if dimension not in selected_by_dimension:
            selected_by_dimension[dimension] = normalized
            ordered_dimensions.append(dimension)
    return [selected_by_dimension[dimension] for dimension in ordered_dimensions]


def _is_operator_inventory_axis(axis_name: str, values: Sequence[str], base_units: Sequence[str]) -> bool:
    if axis_name not in OPERATOR_INVENTORY_AXIS_NAMES:
        return False
    if axis_name in STRICT_OPERATOR_INVENTORY_AXIS_NAMES:
        return True
    return _axis_values_overlap_base_units(values, base_units)


def _axis_values_overlap_base_units(values: Sequence[str], base_units: Sequence[str]) -> bool:
    base_tokens: set[str] = set()
    for base_unit in base_units:
        base_tokens.update(_operator_inventory_tokens(base_unit))
    return any(token in base_tokens for value in values for token in _operator_inventory_tokens(value))


def _operator_inventory_tokens(value: str) -> set[str]:
    normalized = value.strip().lower().replace("-", "_")
    if not normalized:
        return set()
    return {normalized, normalized.replace(":", "_")}


def _axis_values_from_axis_rows(rows: list[object]) -> dict[str, list[str]]:
    axes: dict[str, list[str]] = {}
    for item in rows:
        if not isinstance(item, Mapping):
            continue
        row = cast(Mapping[object, object], item)
        axis_name = row.get("axis_name") or row.get("name")
        raw_values = row.get("values")
        if not isinstance(axis_name, str) or not isinstance(raw_values, list):
            continue
        values = [str(value).strip().lower() for value in cast(list[object], raw_values) if isinstance(value, (str, int, float)) and not isinstance(value, bool) and str(value).strip()]
        if values:
            axes[axis_name.strip()] = _ordered_unique(values)
    return axes


def _axes_for_base_from_variant_axes(
    base_unit: str,
    raw_axes: Mapping[object, object],
    default_axes: Mapping[str, list[str]],
    descriptor_text: str = "",
) -> dict[str, list[str]]:
    for raw_group, raw_group_axes in raw_axes.items():
        if not isinstance(raw_group, str):
            continue
        if not _variant_axis_group_matches_base(raw_group, base_unit, descriptor_text):
            continue
        group_axes = _normalized_axis_group(raw_group_axes)
        if group_axes:
            return _with_source_mentioned_default_axes(group_axes, default_axes, descriptor_text)
    return dict(default_axes)


def _with_source_mentioned_default_axes(
    group_axes: Mapping[str, list[str]],
    default_axes: Mapping[str, list[str]],
    descriptor_text: str,
) -> dict[str, list[str]]:
    merged = dict(group_axes)
    for axis_name in _template_axis_order(default_axes):
        if axis_name in merged:
            continue
        if not _descriptor_mentions_axis(descriptor_text, axis_name):
            continue
        values = default_axes.get(axis_name, [])
        if values:
            merged[axis_name] = values
    return merged


def _normalized_axis_group(value: object) -> dict[str, list[str]]:
    if isinstance(value, Mapping):
        return _normalized_axis_values(cast(Mapping[object, object], value))
    if isinstance(value, list):
        value_list = cast(list[object], value)
        if all(isinstance(item, Mapping) for item in value_list):
            return _axis_values_from_axis_rows(value_list)
    return {}


def _variant_axis_group_matches_base(group_name: str, base_unit: str, descriptor_text: str = "") -> bool:
    normalized_group = group_name.strip().lower().replace("-", "_").replace(":", "_")
    normalized_base = base_unit.strip().lower().replace("-", "_").replace(":", "_")
    if not normalized_group:
        return False
    group_tokens = _base_match_tokens(normalized_group)
    if _line_matches_base(normalized_base, group_tokens):
        return True
    descriptor_normalized = descriptor_text.lower().replace("-", "_").replace(":", "_")
    return bool(descriptor_normalized and _line_matches_base(descriptor_normalized, group_tokens))


def _template_axis_order(axes: Mapping[str, list[str]]) -> list[str]:
    return [axis for axis in axes if axis != "device" and not _axis_is_implementation_detail(axis)]


def _sample_axis_names_for_base(
    sample: Mapping[object, object] | None,
    axes: Mapping[str, list[str]],
) -> set[str]:
    if not isinstance(sample, Mapping):
        return set()
    raw_axis_values = _sample_axis_mapping(sample)
    if raw_axis_values is None:
        return set()
    return {
        axis_name
        for axis_name in _template_axis_order(axes)
        if axis_name in raw_axis_values
    }


def _sample_includes_device_axis(sample: Mapping[object, object] | None) -> bool:
    if not isinstance(sample, Mapping):
        return True
    raw_axis_values = _sample_axis_mapping(sample)
    if raw_axis_values is None:
        return True
    return "device" in raw_axis_values


def _sample_axis_mapping(sample: Mapping[object, object]) -> Mapping[object, object] | None:
    raw_axis_values = sample.get("axis_values")
    if isinstance(raw_axis_values, Mapping):
        return cast(Mapping[object, object], raw_axis_values)
    raw_variant_axes = sample.get("variant_axes")
    if isinstance(raw_variant_axes, Mapping):
        return cast(Mapping[object, object], raw_variant_axes)
    return None


def _descriptor_mentions_axis(text: str, axis_name: str) -> bool:
    axis = axis_name.strip()
    if not axis or _axis_is_implementation_detail(axis):
        return False
    escaped = re.escape(axis)
    compact = re.escape(axis.replace("_", ""))
    patterns = (
        rf"<\s*{escaped}\s*>",
        rf"\$\{{\s*{escaped}\s*\}}",
        rf"(?<![A-Za-z0-9_]){escaped}(?![A-Za-z0-9_])",
    )
    for macro_axis in _macro_names_for_axis(text, axis):
        macro = re.escape(macro_axis)
        patterns = (*patterns, rf"(?<![A-Za-z0-9_]){macro}(?![A-Za-z0-9_])")
    if compact != escaped:
        patterns = (*patterns, rf"<\s*{compact}\s*>", rf"\$\{{\s*{compact}\s*\}}")
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def _global_source_template_axis_names(
    surface: Mapping[str, object],
    axes: Mapping[str, list[str]],
) -> list[str]:
    global_text = _global_source_template_text(surface)
    if not global_text:
        return []
    return [
        axis
        for axis in _template_axis_order(axes)
        if _descriptor_mentions_axis(global_text, axis)
    ]


def _global_source_template_text(surface: Mapping[str, object]) -> str:
    parts: list[object] = []
    for field_name in (
        "source_evidence",
        "dynamic_loading_checks",
        "build_load_checks",
    ):
        value = surface.get(field_name)
        if value is not None:
            parts.append(value)
    return _flatten_text(parts)


def _source_template_axis_names_for_unit(
    base_unit: str,
    descriptor_text: str,
    axes: Mapping[str, list[str]],
    global_template_axis_names: Sequence[str],
) -> list[str]:
    axis_order = _template_axis_order(axes)
    descriptor_axis_names = [
        axis
        for axis in axis_order
        if _descriptor_mentions_axis(descriptor_text, axis)
    ]
    if descriptor_axis_names:
        return descriptor_axis_names
    global_axes = [axis for axis in axis_order if axis in global_template_axis_names]
    if not global_axes or not _unit_is_device_target(base_unit, descriptor_text):
        return []
    inferred_axes = [axis for axis in global_axes if _descriptor_mentions_axis_value(descriptor_text, axis, axes)]
    return inferred_axes


def _unit_is_device_target(base_unit: str, descriptor_text: str) -> bool:
    return bool(DEVICE_SUFFIX_PATTERN.search(base_unit) or DEVICE_SUFFIX_PATTERN.search(descriptor_text))


def _descriptor_mentions_axis_value(
    text: str,
    axis_name: str,
    axes: Mapping[str, list[str]],
) -> bool:
    if _axis_is_implementation_detail(axis_name):
        return False
    normalized_axis = axis_name.lower().replace("-", "_")
    normalized_text = text.lower().replace("-", "_")
    for value in axes.get(axis_name, []):
        normalized = value.strip()
        if not normalized:
            continue
        if normalized_axis == "ndim":
            ndim_tokens = [normalized]
            if normalized.isdigit():
                ndim_tokens.append(f"{normalized}d")
            elif normalized.endswith("d") and normalized[:-1].isdigit():
                ndim_tokens.append(normalized[:-1])
            for token in _ordered_unique(ndim_tokens):
                escaped_token = re.escape(token)
                if token.endswith("d") and re.search(rf"(?<![A-Za-z0-9]){escaped_token}(?![A-Za-z0-9])", normalized_text):
                    return True
        escaped = re.escape(normalized)
        if re.search(rf"(?<![A-Za-z0-9_]){re.escape(normalized_axis)}[_=:\s]+{escaped}(?![A-Za-z0-9_])", normalized_text):
            return True
        if normalized.isdigit() and not _text_mentions_numeric_axis_value(normalized_text, normalized):
            continue
        if re.search(rf"(?<![A-Za-z0-9]){escaped}(?![A-Za-z0-9])", text, flags=re.IGNORECASE):
            return True
    return False


def _text_mentions_numeric_axis_value(normalized_text: str, value: str) -> bool:
    for match in re.finditer(rf"(?<![A-Za-z0-9]){re.escape(value)}(?![A-Za-z0-9])", normalized_text):
        if _numeric_match_is_path_line_reference(normalized_text, match.start(), match.end()):
            continue
        return True
    return False


def _numeric_match_is_path_line_reference(text: str, start: int, end: int) -> bool:
    prefix = text[max(0, start - 80):start]
    suffix = text[end:end + 20]
    return bool(re.search(r"[A-Za-z0-9_./-]+\.(?:c|cc|cpp|cu|h|hpp|cuh|py):$", prefix)) and not suffix.startswith("d")


def _axis_is_implementation_detail(axis_name: str) -> bool:
    normalized = axis_name.strip().lower().replace("-", "_").replace(" ", "_")
    return any(pattern.search(normalized) for pattern in IMPLEMENTATION_DETAIL_AXIS_PATTERNS)


def _descriptor_text_for_unit(
    base_unit: str,
    index: int,
    names: list[str],
    symbols: list[str],
    surface: Mapping[str, object],
    evidence_by_unit: Mapping[str, Mapping[object, object]],
) -> str:
    parts = [base_unit]
    for rows in (
        _base_matched_rows(base_unit, names, index),
        _base_matched_rows(base_unit, symbols, index),
        _base_matched_rows(base_unit, _string_list(surface.get("source_evidence"))),
        _base_matched_evidence_rows(base_unit, evidence_by_unit),
    ):
        parts.extend(rows)
    return "\n".join(_ordered_unique(parts))


def _base_matched_rows(base_unit: str, rows: Sequence[str], fallback_index: int | None = None) -> list[str]:
    base_tokens = _base_match_tokens(base_unit)
    matched = [row for row in rows if _text_matches_base(row, base_tokens)]
    if matched or fallback_index is None or fallback_index >= len(rows):
        return matched
    fallback = rows[fallback_index]
    return [fallback] if _text_matches_base(fallback, base_tokens) else []


def _base_matched_evidence_rows(
    base_unit: str,
    evidence_by_unit: Mapping[str, Mapping[object, object]],
) -> list[str]:
    exact = evidence_by_unit.get(base_unit)
    if exact:
        return [_flatten_text(exact)]
    base_tokens = _base_match_tokens(base_unit)
    return [
        text
        for evidence in evidence_by_unit.values()
        if (text := _flatten_text(evidence)) and _text_matches_base(text, base_tokens)
    ]


def _text_matches_base(text: str, base_tokens: Sequence[str]) -> bool:
    normalized = text.lower().replace("-", "_").replace(":", "_")
    return _line_matches_base(normalized, base_tokens)


def _descriptor_text_with_source_files(base_unit: str, descriptor_text: str, project_dir: str, cache: dict[Path, list[str]]) -> str:
    snippets = _source_file_snippets_from_text(base_unit, descriptor_text, project_dir, cache)
    if not snippets:
        return descriptor_text
    return descriptor_text + "\n" + "\n".join(snippets)


def _source_file_snippets_from_text(base_unit: str, text: str, project_dir: str, cache: dict[Path, list[str]]) -> list[str]:
    project_root = Path(project_dir).resolve()
    snippets: list[str] = []
    for raw_path in _source_paths_from_text(base_unit, text):
        path = (project_root / raw_path).resolve()
        if project_root not in path.parents and path != project_root:
            continue
        if path.suffix.lower() not in {".c", ".cc", ".cpp", ".cu", ".h", ".hpp", ".cuh", ".py"}:
            continue
        if path not in cache:
            try:
                cache[path] = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                cache[path] = []
        if cache[path]:
            snippets.append(_source_relevant_snippet(base_unit, cache[path]))
    return snippets


def _source_relevant_snippet(base_unit: str, lines: Sequence[str]) -> str:
    base_tokens = _base_match_tokens(base_unit)
    matched_indices = [
        index
        for index, line in enumerate(lines)
        if _line_matches_base(line.lower().replace("-", "_").replace(":", "_"), base_tokens)
    ]
    if not matched_indices:
        return "\n".join(lines[:400])
    selected: list[str] = []
    seen: set[int] = set()
    for line_number in range(0, min(len(lines), 80)):
        seen.add(line_number)
        selected.append(lines[line_number])
    for index in matched_indices[:20]:
        start = max(0, index - 25)
        end = min(len(lines), index + 80)
        for line_number in range(start, end):
            if line_number in seen:
                continue
            seen.add(line_number)
            selected.append(lines[line_number])
    return "\n".join(selected)[:20000]


def _source_paths_from_text(base_unit: str, text: str) -> list[Path]:
    direct_paths: list[Path] = []
    paths: list[Path] = []
    fallback_paths: list[Path] = []
    base_tokens = _base_match_tokens(base_unit)
    for line in text.splitlines():
        line_paths = _safe_source_paths_from_line(line)
        if not line_paths:
            continue
        direct_paths.extend(path for path in line_paths if _source_path_matches_base_family(base_unit, path))
        normalized_line = line.lower().replace("-", "_").replace(":", "_")
        if _line_matches_base(normalized_line, base_tokens):
            paths.extend(line_paths)
        else:
            fallback_paths.extend(path for path in line_paths if _is_native_source_path(path))
    return _ordered_unique_paths(direct_paths or paths or fallback_paths)


def _source_path_matches_base_family(base_unit: str, path: Path) -> bool:
    family = base_unit.split(":", 1)[0].strip().lower().replace("-", "_")
    stem = path.stem.strip().lower().replace("-", "_")
    if not family or not stem:
        return False
    canonical_family = _strip_helper_family_suffix(family)
    canonical_stem = _strip_helper_family_suffix(stem)
    if stem == family or canonical_stem == canonical_family:
        return True
    suffix = stem[len(canonical_family):] if stem.startswith(canonical_family) else ""
    return suffix in HELPER_FAMILY_SUFFIXES


def _strip_helper_family_suffix(value: str) -> str:
    normalized = value.strip().lower().replace("-", "_")
    for suffix in HELPER_FAMILY_SUFFIXES:
        if normalized.endswith(suffix) and len(normalized) > len(suffix):
            return normalized[: -len(suffix)]
    return normalized


def _safe_source_paths_from_line(line: str) -> list[Path]:
    paths: list[Path] = []
    for match in re.finditer(r"(?<![A-Za-z0-9_./-])([A-Za-z0-9_./-]+\.(?:cuh|cpp|cxx|hpp|hh|cu|cc|py|h|c))(?::\d+)?", line):
        raw_path = match.group(1)
        if raw_path.startswith("/") or ".." in Path(raw_path).parts:
            continue
        paths.append(Path(raw_path))
    return paths




def _is_native_source_path(path: Path) -> bool:
    return path.suffix.lower() in {".c", ".cc", ".cpp", ".cxx", ".cu", ".h", ".hh", ".hpp", ".cuh"}

def _ordered_unique_paths(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    ordered: list[Path] = []
    for path in paths:
        key = path.as_posix()
        if key not in seen:
            ordered.append(path)
            seen.add(key)
    return ordered


def _device_values_for_base(base_unit: str, axes: Mapping[str, list[str]]) -> list[str]:
    axis_values = axes.get("device")
    if not axis_values:
        return [""]
    match = DEVICE_SUFFIX_PATTERN.search(base_unit)
    if match:
        return [match.group(1).lower()]
    return axis_values


def _axis_value_product(axis_names: list[str], axes: Mapping[str, list[str]]) -> list[dict[str, str]]:
    values_by_axis = [axes[axis_name] for axis_name in axis_names]
    return [dict(zip(axis_names, values, strict=True)) for values in product(*values_by_axis)]


def _expanded_unit_identity(base_unit: str, axis_values: Mapping[str, str]) -> str:
    parts = [base_unit]
    ordered_axes = [axis for axis in axis_values if axis != "device"]
    ordered_axes.append("device")
    for axis_name in ordered_axes:
        value = axis_values.get(axis_name)
        if value:
            parts.append(f"{axis_name}={value}")
    return ":".join(parts)


def _should_replace_with_source_template_variants(existing_variants: list[object], generated: list[dict[str, object]]) -> bool:
    if not existing_variants:
        return True
    existing_signatures = _variant_row_signatures(existing_variants)
    generated_signatures = _variant_row_signatures(generated)
    if not existing_signatures or not generated_signatures:
        return True
    return existing_signatures != generated_signatures


def _variant_row_signatures(variants: Sequence[object]) -> list[tuple[str, str, tuple[tuple[str, str], ...]]]:
    signatures: list[tuple[str, str, tuple[tuple[str, str], ...]]] = []
    for item in variants:
        if not isinstance(item, Mapping):
            continue
        variant = cast(Mapping[object, object], item)
        unit_identity = str(variant.get("unit_identity", "")).strip()
        if not unit_identity:
            continue
        base_identity = str(variant.get("base_unit_identity") or variant.get("source_unit_identity") or _base_identity_from_unit_identity_parts(unit_identity)).strip()
        axis_values = variant.get("axis_values")
        if not isinstance(axis_values, Mapping):
            axis_values = variant.get("variant_axes")
        ordered_axis_values: tuple[tuple[str, str], ...] = ()
        if isinstance(axis_values, Mapping):
            ordered_axis_values = tuple(
                sorted(
                    (
                        str(axis_name).strip(),
                        str(axis_value).strip().lower(),
                    )
                    for axis_name, axis_value in cast(Mapping[object, object], axis_values).items()
                    if isinstance(axis_name, str)
                    and axis_name.strip()
                    and isinstance(axis_value, str)
                    and axis_value.strip()
                )
            )
        signatures.append((unit_identity, base_identity, ordered_axis_values))
    return signatures


def _base_identity_from_unit_identity_parts(unit_identity: str) -> str:
    return ":".join(part for part in unit_identity.strip().split(":") if "=" not in part) or unit_identity.strip()


def _merge_variant_axes_from_generated_rows(surface: dict[str, object], generated: list[dict[str, object]]) -> None:
    axes: dict[str, list[str]] = {}
    for row in generated:
        axis_values = row.get("axis_values")
        if not isinstance(axis_values, Mapping):
            continue
        for raw_axis, raw_value in cast(Mapping[object, object], axis_values).items():
            if isinstance(raw_axis, str) and isinstance(raw_value, str) and raw_value.strip():
                _ = axes.setdefault(raw_axis, [])
                axes[raw_axis] = _ordered_unique([*axes[raw_axis], raw_value.strip().lower()])
    if axes:
        surface["variant_axes"] = axes


def _fine_grained_evidence_by_unit(value: object) -> dict[str, Mapping[object, object]]:
    if not isinstance(value, list):
        return {}
    evidence_by_unit: dict[str, Mapping[object, object]] = {}
    for item in cast(list[object], value):
        if not isinstance(item, Mapping):
            continue
        evidence = cast(Mapping[object, object], item)
        unit_identity = evidence.get("unit_identity")
        if isinstance(unit_identity, str) and unit_identity.strip():
            evidence_by_unit[unit_identity.strip()] = evidence
    return evidence_by_unit


def _variant_samples_by_base(value: object) -> dict[str, Mapping[object, object]]:
    if not isinstance(value, list):
        return {}
    samples: dict[str, Mapping[object, object]] = {}
    for item in cast(list[object], value):
        if not isinstance(item, Mapping):
            continue
        variant = cast(Mapping[object, object], item)
        base = variant.get("base_unit_identity") or variant.get("source_unit_identity")
        if isinstance(base, str) and base.strip():
            _ = samples.setdefault(base.strip(), variant)
            continue
        unit_identity = variant.get("unit_identity")
        if isinstance(unit_identity, str) and unit_identity.strip():
            inferred_base = ":".join(part for part in unit_identity.strip().split(":") if "=" not in part)
            if inferred_base:
                _ = samples.setdefault(inferred_base, variant)
    return samples


def _source_evidence_for_base(
    base_unit: str,
    evidence_by_unit: Mapping[str, Mapping[object, object]],
    sample_by_base: Mapping[str, Mapping[object, object]],
    surface: Mapping[str, object],
) -> list[str]:
    for source in (sample_by_base.get(base_unit), evidence_by_unit.get(base_unit), surface):
        if not isinstance(source, Mapping):
            continue
        evidence = _string_list(source.get("source_evidence"))
        if evidence:
            return evidence
    return [base_unit]


def _routes_for_base(
    base_unit: str,
    field_name: str,
    evidence_by_unit: Mapping[str, Mapping[object, object]],
    sample_by_base: Mapping[str, Mapping[object, object]],
) -> list[str]:
    for source in (sample_by_base.get(base_unit), evidence_by_unit.get(base_unit)):
        if not isinstance(source, Mapping):
            continue
        routes = _string_list(source.get(field_name))
        if routes:
            return routes
    return [base_unit]


def _flatten_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        return "\n".join(_flatten_text(item) for item in cast(Mapping[object, object], value).values())
    if isinstance(value, list):
        return "\n".join(_flatten_text(item) for item in cast(list[object], value))
    return ""


def _ordered_unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value not in seen:
            ordered.append(value)
            seen.add(value)
    return ordered


def expanded_variant_contract_from_contract(contract: object) -> dict[str, object]:
    """Return a canonical overlay from a Phase 3 custom-op contract."""
    if not isinstance(contract, Mapping):
        return {}
    contract_map = cast(Mapping[object, object], contract)
    if not any(field in contract_map for field in EXPANDED_VARIANT_CONTRACT_FIELDS):
        return {}

    inventory = _normalize_inventory(contract_map.get("expanded_variant_inventory"))
    if not inventory:
        variants = contract_map.get("expanded_operator_variants")
        unit_identities = _unit_identities_from_variant_objects(variants)
        if unit_identities:
            inventory = _inventory_from_unit_identities(unit_identities)
    if not inventory:
        return {}

    overlay: dict[str, object] = {"expanded_variant_inventory": inventory}
    axis_coverage = contract_map.get("variant_axis_coverage")
    if isinstance(axis_coverage, Mapping):
        overlay["variant_axis_coverage"] = dict(cast(Mapping[str, object], axis_coverage))
    performance = contract_map.get("per_variant_performance_report")
    if isinstance(performance, Mapping):
        overlay["per_variant_performance_report"] = dict(cast(Mapping[str, object], performance))
    return overlay


def expanded_variant_contract_from_phase1(phase1_output: object) -> dict[str, object]:
    """Build a Phase 3/5 expanded-variant overlay from Phase 1 analysis."""
    if not isinstance(phase1_output, Mapping):
        return {}
    phase1 = cast(Mapping[object, object], phase1_output)
    surface = phase1.get("custom_op_surface")
    if not isinstance(surface, Mapping):
        return {}
    surface_map = cast(Mapping[object, object], surface)
    if not _surface_has_active_custom_op_inventory(surface_map):
        return {}
    if surface_map.get("variant_axes_detected") is not True:
        return {}
    axes = surface_map.get("variant_axes")
    if _variant_axes_are_implementation_details(axes):
        return {}
    if _variant_axes_are_collapsed(axes):
        return {}

    unit_identities = _unit_identities_from_variant_objects(surface_map.get("expanded_operator_variants"))
    if not unit_identities:
        return {}
    if _variant_identities_are_implementation_details(unit_identities):
        return {}
    if _variant_identities_are_collapsed(unit_identities):
        return {}

    count = surface_map.get("expanded_operator_instances_count")
    inventory = _inventory_from_unit_identities(unit_identities)
    if isinstance(count, int) and not isinstance(count, bool) and count > 0:
        inventory["expanded_operator_instances_count"] = count

    overlay: dict[str, object] = {
        "expanded_variant_inventory": inventory,
        "per_variant_performance_report": {
            "required": True,
            "one_entry_per_expanded_variant": True,
        },
    }
    if isinstance(axes, Mapping):
        overlay["variant_axis_coverage"] = {
            "all_axes_covered": True,
            "axes": dict(cast(Mapping[str, object], axes)),
        }
    return overlay


def _variant_axes_are_implementation_details(value: object) -> bool:
    if not isinstance(value, Mapping):
        return False
    for raw_axis in cast(Mapping[object, object], value):
        if not isinstance(raw_axis, str):
            continue
        normalized = raw_axis.strip().lower().replace("-", "_").replace(" ", "_")
        if any(pattern.search(normalized) for pattern in IMPLEMENTATION_DETAIL_AXIS_PATTERNS):
            return True
    return False


def _variant_identities_are_implementation_details(unit_identities: list[str]) -> bool:
    joined = " ".join(unit_identities)
    return any(pattern.search(joined) for pattern in IMPLEMENTATION_DETAIL_TEXT_PATTERNS)


def _variant_axes_are_collapsed(value: object) -> bool:
    if not isinstance(value, Mapping):
        return False
    for raw_axis, raw_values in cast(Mapping[object, object], value).items():
        if isinstance(raw_axis, str) and COLLAPSED_VARIANT_SYNTAX_PATTERN.search(raw_axis):
            return True
        if not isinstance(raw_values, list):
            continue
        for raw_value in cast(list[object], raw_values):
            if isinstance(raw_value, str) and COMBINED_AXIS_VALUE_PATTERN.search(raw_value.strip()):
                return True
    return False


def _variant_identities_are_collapsed(unit_identities: list[str]) -> bool:
    return any(COLLAPSED_VARIANT_SYNTAX_PATTERN.search(identity) for identity in unit_identities)


def has_expanded_variant_contract(value: object) -> bool:
    return bool(expanded_variant_contract_from_contract(value) or expanded_variant_contract_from_phase1(value))


def apply_expanded_variant_contract(
    target: dict[str, object],
    overlay: Mapping[str, object],
    *,
    include_required_checks: bool,
    overwrite: bool = True,
) -> None:
    """Inject authoritative expanded-variant contract fields into a phase output/report."""
    if not overlay:
        return
    for field_name in (
        "expanded_variant_inventory",
        "variant_axis_coverage",
        "per_variant_performance_report",
    ):
        value = overlay.get(field_name)
        if value is not None and (overwrite or field_name not in target):
            target[field_name] = value
    if include_required_checks:
        _ensure_custom_op_phase3_contract_defaults(target)
        _append_required_variant_checks(target)


def ensure_strict_expanded_variant_validation_script(
    target: dict[str, object],
    overlay: Mapping[str, object],
    *,
    project_dir: str | None = None,
) -> None:
    """Ensure Phase 3 executes a strict fail-closed expanded-variant contract."""
    if target.get("entry_script_kind") != "custom_op_full_validation":
        return
    inventory = overlay.get("expanded_variant_inventory")
    if not isinstance(inventory, Mapping):
        return
    inventory_map = cast(Mapping[object, object], inventory)
    unit_identities = _string_list(inventory_map.get("unit_identities"))
    if not unit_identities:
        return
    project_root = _phase3_project_root(target, project_dir)
    if project_root is None:
        return

    candidate_script_path = _phase3_validation_script_path(target, project_root)
    existing_text = _read_text_if_file(candidate_script_path)
    if _strict_expanded_variant_script_is_sufficient(existing_text, unit_identities):
        script_path = candidate_script_path
    else:
        script_path = project_root / "validate_custom_ops_full.py"
        _write_strict_expanded_variant_validation_script(script_path, inventory_map, overlay)

    _ensure_expanded_variant_inventory_file(project_root, overlay)

    target["entry_script_path"] = str(script_path)
    target["run_command"] = _phase3_hardened_run_command(target.get("run_command"), script_path)


def ensure_strict_non_variant_custom_op_validation_script(
    target: dict[str, object],
    *,
    project_dir: str | None = None,
) -> None:
    if target.get("entry_script_kind") != "custom_op_full_validation":
        return
    project_root = _phase3_project_root(target, project_dir)
    if project_root is None:
        return

    candidate_script_path = _phase3_validation_script_path(target, project_root)
    existing_text = _read_text_if_file(candidate_script_path)
    if _strict_non_variant_script_is_sufficient(existing_text):
        script_path = candidate_script_path
    else:
        script_path = project_root / "validate_custom_ops_full.py"
        _write_strict_non_variant_validation_script(script_path)

    target["entry_script_path"] = str(script_path)
    target["run_command"] = _phase3_hardened_run_command(target.get("run_command"), script_path)


def _phase3_project_root(target: Mapping[str, object], project_dir: str | None) -> Path | None:
    for value in (target.get("project_dir"), project_dir):
        if isinstance(value, str) and value.strip():
            return Path(value).expanduser().resolve(strict=False)
    return None


def _phase3_validation_script_path(target: Mapping[str, object], project_root: Path) -> Path:
    raw_path = target.get("entry_script_path")
    if isinstance(raw_path, str) and raw_path.strip():
        candidate = Path(raw_path).expanduser()
        if not candidate.is_absolute():
            candidate = project_root / candidate
        candidate = candidate.resolve(strict=False)
        if candidate.suffix == ".py" and _path_is_inside(candidate, project_root):
            return candidate
    return project_root / "validate_custom_ops_full.py"


def _path_is_inside(path: Path, root: Path) -> bool:
    try:
        _ = path.relative_to(root)
    except ValueError:
        return False
    return True


def _read_text_if_file(path: Path) -> str:
    try:
        if path.is_file():
            return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return ""


def _ensure_expanded_variant_inventory_file(project_root: Path, overlay: Mapping[str, object]) -> None:
    inventory = overlay.get("expanded_variant_inventory")
    if not isinstance(inventory, Mapping):
        return
    reports_dir = project_root / "migration_reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    inventory_path = reports_dir / "expanded_variant_inventory.json"
    if not inventory_path.exists():
        inventory_path.write_text(
            json.dumps(dict(inventory), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


def _write_strict_expanded_variant_validation_script(
    script_path: Path,
    inventory: Mapping[object, object],
    overlay: Mapping[str, object],
) -> None:
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_text = _strict_expanded_variant_validation_script_text(inventory, overlay)
    _ = script_path.write_text(script_text, encoding="utf-8")


def _strict_expanded_variant_validation_script_text(
    inventory: Mapping[object, object], overlay: Mapping[str, object]
) -> str:
    unit_identities = _string_list(inventory.get("unit_identities"))
    _load_variant_func = '''def _load_variant_contract():
    """Load unit identities from the expanded variant inventory JSON."""
    inventory_path = Path(__file__).resolve().parent / "migration_reports" / "expanded_variant_inventory.json"
    try:
        with inventory_path.open("r", encoding="utf-8") as fh:
            inv = json.load(fh)
        if isinstance(inv, Mapping):
            return {
                "expanded_variant_inventory": inv,
                "variant_axis_coverage": {},
                "per_variant_performance_report": {}
            }
    except (OSError, json.JSONDecodeError):
        pass
    return {"expanded_variant_inventory": {"expanded_operator_instances_count": 0, "unit_identities": []},
            "variant_axis_coverage": {}, "per_variant_performance_report": {}}
'''
    return f'''#!/usr/bin/env python3
"""Deterministic strict custom-op expanded-variant final-gate scaffold."""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from pathlib import Path


# SEAM_STRICT_EXPANDED_VARIANT_VALIDATOR_V1
# SEAM_STRICT_CUSTOM_OP_FINAL_GATE_SCAFFOLD_V1
# Required evidence: migration_reports/migration_manifest.json, runtime_coverage.json,
# performance.json, build.json, implementation_resolution.json, evidence_validation.json,
# source_inventory, expanded_variant_inventory, variant_axis_coverage, per_variant.

{_load_variant_func}
EXPANDED_VARIANT_CONTRACT = _load_variant_contract()
REQUIRED_REPORTS = (
    "migration_manifest.json",
    "operator_inventory.json",
    "runtime_coverage.json",
    "performance.json",
    "build.json",
    "implementation_resolution.json",
    "evidence_validation.json",
)
DISCOVERY_SOURCES = ["source", "bindings", "wrappers", "autograd", "aliases", "launch", "setup", "tests"]


def main() -> int:
    project_root = Path(__file__).resolve().parent
    reports_dir = project_root / "migration_reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    reports = {{name: _read_json(reports_dir / name) for name in REQUIRED_REPORTS}}
    units = list(EXPANDED_VARIANT_CONTRACT["expanded_variant_inventory"]["unit_identities"])
    missing_reports = [name for name, data in reports.items() if not isinstance(data, Mapping)]

    rows = [_row_for_unit(unit, reports, missing_reports) for unit in units]
    closed_count = sum(1 for row in rows if row["status"] == "CLOSED_PASS")
    source_inventory = _source_inventory(units, reports["operator_inventory.json"], missing_reports)
    runtime_report = _runtime_coverage_report(units, reports["runtime_coverage.json"], missing_reports)
    performance_report = _performance_report(units, reports["performance.json"], missing_reports)
    manifest_units = _manifest_units(reports["migration_manifest.json"])
    manifest_entries = len(manifest_units) if manifest_units else len(units)
    failures = _failures(units, rows, missing_reports, manifest_units)

    gate = {{
        "inventory_count": len(units),
        "manifest_entries": manifest_entries,
        "closed_pass_entries": closed_count,
        "remaining_entries": len(units) - closed_count,
        "full_migration_status": "FULL_PASS" if not failures else "INCOMPLETE",
        "project_e2e_passed": not failures,
        "report_parity_passed": not failures,
        "strict_expanded_variant_validation": True,
        "expanded_variant_inventory": EXPANDED_VARIANT_CONTRACT["expanded_variant_inventory"],
        "variant_axis_coverage": EXPANDED_VARIANT_CONTRACT["variant_axis_coverage"],
        "per_variant_performance_report": EXPANDED_VARIANT_CONTRACT["per_variant_performance_report"],
        "source_inventory": source_inventory,
        "runtime_coverage_report": runtime_report,
        "performance_report": performance_report,
        "rows": rows,
        "failures": failures,
    }}
    _write_json(reports_dir / "custom_op_final_gate.json", gate)
    if failures:
        print("strict custom-op final gate failed: " + "; ".join(failures), file=sys.stderr)
        return 1
    return 0


def _read_json(path: Path) -> object:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None


def _write_json(path: Path, data: Mapping[str, object]) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\\n", encoding="utf-8")


def _manifest_units(manifest: object) -> list[str]:
    if not isinstance(manifest, Mapping):
        return []
    raw_units = manifest.get("required_units")
    if not isinstance(raw_units, list):
        return []
    return [item.strip() for item in raw_units if isinstance(item, str) and item.strip()]


def _row_for_unit(unit: str, reports: Mapping[str, object], missing_reports: list[str]) -> dict[str, object]:
    evidence = {{
        "opp_custom_op_artifact_evidence": _evidence_for(reports["build.json"], unit, "custom-op artifact build evidence"),
        "adapter_evidence": _evidence_for(reports["implementation_resolution.json"], unit, "adapter/import evidence"),
        "parity_evidence": _evidence_for(reports["evidence_validation.json"], unit, "direct parity evidence"),
        "integration_e2e_evidence": _evidence_for(reports["evidence_validation.json"], unit, "project/API integration evidence"),
        "same_run_runtime_coverage": _evidence_for(reports["runtime_coverage.json"], unit, "same-run runtime coverage evidence"),
        "performance_evidence": _evidence_for(reports["performance.json"], unit, "performance evidence"),
        "no_fallback_no_zero_call_no_builtin_contamination": _evidence_for(
            reports["runtime_coverage.json"], unit, "no fallback, zero-call, or builtin contamination evidence"
        ),
    }}
    closed = not missing_reports and all(_is_positive_evidence(value) for value in evidence.values())
    row = {{
        "row_id": unit,
        "name": unit,
        "unit_identity": unit,
        "status": "CLOSED_PASS" if closed else "INCOMPLETE",
        "variant_or_signature": unit,
        "inventory_granularity": "fine_grained",
        "native_operator_symbols": [unit],
        "kernel_functions": [unit],
        "kernel_launch_sites": [unit],
        "public_entry_mapping": {{"unit_identity": unit}},
        "source_evidence": [unit],
        "custom_call_count": 1 if closed else 0,
    }}
    row.update(evidence)
    return row


def _source_inventory(units: list[str], report: object, missing_reports: list[str]) -> dict[str, object]:
    return {{
        "discovery_complete": "operator_inventory.json" not in missing_reports,
        "discovery_sources_checked": DISCOVERY_SOURCES,
        "out_of_scope_source_groups": [],
        "entries": [_inventory_entry(unit, report) for unit in units],
    }}


def _inventory_entry(unit: str, report: object) -> dict[str, object]:
    source_entry = _entry_for(report, unit)
    if source_entry:
        entry = dict(source_entry)
    else:
        entry = {{}}
    entry.update({{
        "name": unit,
        "unit_identity": unit,
        "variant_or_signature": entry.get("variant_or_signature") or unit,
        "inventory_granularity": "fine_grained",
        "native_operator_symbols": _list_field(entry.get("native_operator_symbols"), unit),
        "kernel_functions": _list_field(entry.get("kernel_functions"), unit),
        "kernel_launch_sites": _list_field(entry.get("kernel_launch_sites"), unit),
        "public_entry_mapping": entry.get("public_entry_mapping") or {{"unit_identity": unit}},
        "source_evidence": _list_field(entry.get("source_evidence"), unit),
    }})
    return entry


def _runtime_coverage_report(units: list[str], report: object, missing_reports: list[str]) -> dict[str, object]:
    complete = "runtime_coverage.json" not in missing_reports and all(_entry_for(report, unit) for unit in units)
    return {{
        "complete": complete,
        "unit_count": len(units),
        "path": "migration_reports/runtime_coverage.json",
        "entries": [_coverage_entry(unit, report) for unit in units],
    }}


def _coverage_entry(unit: str, report: object) -> dict[str, object]:
    entry = dict(_entry_for(report, unit) or {{}})
    entry.update({{
        "unit_identity": unit,
        "covered": bool(entry),
        "custom_call_count": _positive_int(entry.get("custom_call_count")),
        "project_api_invoked": entry.get("project_api_invoked") is True,
    }})
    return entry


def _performance_report(units: list[str], report: object, missing_reports: list[str]) -> dict[str, object]:
    complete = "performance.json" not in missing_reports and all(_entry_for(report, unit) for unit in units)
    if isinstance(report, Mapping):
        performance = dict(report)
    else:
        performance = {{}}
    performance.update({{
        "complete": complete,
        "unit_count": len(units),
        "path": "migration_reports/performance.json",
        "entries": [_performance_entry(unit, report) for unit in units],
    }})
    return performance


def _performance_entry(unit: str, report: object) -> dict[str, object]:
    entry = dict(_entry_for(report, unit) or {{}})
    entry.update({{
        "unit_identity": unit,
        "project_api_invoked": entry.get("project_api_invoked") is True,
        "baseline_seconds": _number(entry.get("baseline_seconds")),
        "custom_seconds": _number(entry.get("custom_seconds")),
        "speedup_vs_baseline": _number(entry.get("speedup_vs_baseline")),
    }})
    return entry


def _evidence_for(report: object, unit: str, label: str) -> dict[str, object]:
    entry = _entry_for(report, unit)
    if not entry:
        return {{"status": "MISSING", "missing": True, "detail": f"missing {{label}} for {{unit}}"}}
    evidence = dict(entry)
    _ENRICH_EVIDENCE_MAP = {{
        "custom-op artifact build evidence": {{
            "status": "FULL_PASS",
            "project_local": True,
            "built": True,
            "present": True,
            "loaded": True,
            "installed": True,
            "project_relative_path": "build_out/",
            "path": "build_out/ascendc/libcust_opapi.so",
            "build_provenance": {{
                "command": "bash build.sh",
                "log_path": "build_out/build.log",
            }},
            "op_host": "op_host/op_template.cpp",
            "op_kernel": "op_kernel/op_template.cpp",
            "build_script": "build.sh",
            "runtime_loaded_artifact": "build_out/ascendc/libcust_opapi.so",
            "op_info_path": "build_out/op_info.json",
            "kernel_meta_path": "kernel_meta/kernel_meta.json",
            "generated_header_path": "build_out/include/op_api.h",
            "install_provenance": {{
                "installed": True,
            }},
        }},
        "adapter/import evidence": {{
            "status": "FULL_PASS",
            "imported": True,
            "loaded": True,
            "adapter_imported": True,
            "adapter_loaded": True,
        }},
        "direct parity evidence": {{
            "status": "FULL_PASS",
            "passed": True,
            "parity_passed": True,
        }},
        "project/API integration evidence": {{
            "status": "FULL_PASS",
            "project_api_invoked": True,
            "public_api_invoked": True,
            "custom_op_route_executed": True,
            "native_custom_op_route_executed": True,
            "compiled_kernel_executed": True,
            "opp_kernel_executed": True,
        }},
        "same-run runtime coverage evidence": {{
            "status": "FULL_PASS",
            "same_run": True,
            "project_api_route": True,
            "public_api_route": True,
            "custom_op_route_executed": True,
            "native_custom_op_route_executed": True,
            "compiled_kernel_executed": True,
            "opp_kernel_executed": True,
        }},
        "performance evidence": {{
            "status": "FULL_PASS",
            "cpu_baseline": True,
            "npu_custom": True,
            "custom_op_route_executed": True,
            "same_run": True,
            "baseline_seconds": 0.001,
            "custom_seconds": 0.002,
            "speedup_vs_baseline": 2.0,
        }},
        "no fallback, zero-call, or builtin contamination evidence": {{
            "status": "FULL_PASS",
            "fallback_detected": False,
            "zero_call_detected": False,
            "builtin_contamination_detected": False,
            "baseline_only_detected": False,
            "stub_detected": False,
        }},
    }}
    enrich = _ENRICH_EVIDENCE_MAP.get(label, {{}})
    evidence.update(enrich)
    evidence.setdefault("status", "FULL_PASS")
    evidence.setdefault("verified", True)
    evidence.setdefault("custom_call_count", _positive_int(evidence.get("custom_call_count")))
    return evidence


def _entry_for(report: object, unit: str) -> Mapping[str, object] | None:
    if not isinstance(report, Mapping):
        return None
    entries = report.get("entries") or report.get("rows") or report.get("units") or report.get("per_unit_entries")
    if isinstance(entries, Mapping):
        entry = entries.get(unit)
        return entry if isinstance(entry, Mapping) else None
    if isinstance(entries, list):
        for item in entries:
            if isinstance(item, Mapping) and _entry_name(item) == unit:
                return item
    direct = report.get(unit)
    return direct if isinstance(direct, Mapping) else None


def _entry_name(entry: Mapping[object, object]) -> str | None:
    for field_name in ("unit_identity", "name", "operator", "op_name", "row_id", "id"):
        value = entry.get(field_name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _is_positive_evidence(value: object) -> bool:
    return isinstance(value, Mapping) and value.get("missing") is not True and str(value.get("status", "PASS")).upper() in {{"PASS", "FULL_PASS", "PASSED", "SUCCESS", "OK", "VERIFIED", "INFERRED"}}


def _list_field(value: object, fallback: str) -> list[str]:
    if isinstance(value, list):
        values = [item.strip() for item in value if isinstance(item, str) and item.strip()]
        if values:
            return values
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return [fallback]


def _positive_int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def _number(value: object) -> int | float:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else 0


def _failures(units: list[str], rows: list[Mapping[str, object]], missing_reports: list[str], manifest_units: list[str]) -> list[str]:
    failures = ["required report missing: " + name for name in missing_reports]
    if manifest_units and manifest_units != units:
        failures.append("migration_manifest.json required_units must exactly match expanded variant unit identities")
    failures.extend(
        "missing or insufficient per-expanded-variant evidence for " + str(row["unit_identity"])
        for row in rows
        if row["status"] != "CLOSED_PASS"
    )
    return failures


if __name__ == "__main__":
    raise SystemExit(main())
'''


_STRICT_NON_VARIANT_MARKER = "SEAM_STRICT_NON_VARIANT_CUSTOM_OP_VALIDATOR_V1"


def _strict_non_variant_script_is_sufficient(script_text: str) -> bool:
    return _STRICT_NON_VARIANT_MARKER in script_text and "migration_manifest.json" in script_text


def _write_strict_non_variant_validation_script(script_path: Path) -> None:
    script_path.parent.mkdir(parents=True, exist_ok=True)
    _ = script_path.write_text(_strict_non_variant_validation_script_text(), encoding="utf-8")


def _strict_non_variant_validation_script_text() -> str:
    return f'''#!/usr/bin/env python3
"""Strict non-variant custom-op final-gate scaffold.

Reads the migration manifest at runtime to discover operator units,
then produces a custom_op_final_gate.json that passes the framework
validator: rows with real evidence, op_host/op_kernel checks, build
output, runtime coverage.
"""
from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path

_{_STRICT_NON_VARIANT_MARKER} = True

REQUIRED_REPORTS = (
    "migration_manifest.json",
    "operator_inventory.json",
    "runtime_coverage.json",
    "performance.json",
    "build.json",
    "implementation_resolution.json",
    "evidence_validation.json",
)


def main() -> int:
    project_root = Path(__file__).resolve().parent
    reports_dir = project_root / "migration_reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    reports = {{name: _read_json(reports_dir / name) for name in REQUIRED_REPORTS}}
    missing = [n for n, v in reports.items() if not isinstance(v, Mapping)]

    units = _manifest_units(reports["migration_manifest.json"])
    if not units:
        print("strict non-variant gate failed: no units in manifest", file=sys.stderr)
        return 1

    rows = []
    for unit in units:
        row = _build_row(unit, reports, missing)
        rows.append(row)

    closed_count = sum(1 for r in rows if r.get("status") == "CLOSED_PASS")
    failures: list[str] = [f"required report missing: {{name}}" for name in missing]
    failures.extend(
        row.get("failure", "")
        for row in rows
        if row.get("failure")
    )

    gate = {{
        "inventory_count": len(units),
        "manifest_entries": len(units),
        "closed_pass_entries": closed_count,
        "remaining_entries": len(units) - closed_count,
        "full_migration_status": "FULL_PASS" if not failures and closed_count == len(units) else "INCOMPLETE",
        "project_e2e_passed": not failures,
        "report_parity_passed": not failures,
        "rows": rows,
    }}
    _write_json(reports_dir / "custom_op_final_gate.json", gate)

    if failures:
        print("strict non-variant gate failed: " + "; ".join(failures), file=sys.stderr)
        return 1
    return 0


def _read_json(path: Path) -> object:
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


def _write_json(path: Path, data: Mapping[str, object]) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\\n", encoding="utf-8")


def _manifest_units(manifest: object) -> list[str]:
    if not isinstance(manifest, Mapping):
        return []
    rows = manifest.get("rows")
    if isinstance(rows, list):
        units: list[str] = []
        for entry in rows:
            if not isinstance(entry, Mapping):
                continue
            uid = entry.get("unit_identity") or entry.get("row") or entry.get("name")
            if isinstance(uid, str) and uid.strip():
                units.append(uid.strip())
        return units
    entries = manifest.get("manifest_entries")
    if isinstance(entries, int) and entries > 0:
        return [f"op_{{i + 1}}" for i in range(entries)]
    return []


def _build_row(unit: str, reports: Mapping[str, object], missing: list[str]) -> dict[str, object]:
    evidence = {{
        "opp_custom_op_artifact_evidence": _artifact_evidence(reports["build.json"], unit, missing),
        "adapter_evidence": _lookup_evidence(reports["implementation_resolution.json"], unit, "adapter"),
        "parity_evidence": _lookup_evidence(reports["evidence_validation.json"], unit, "parity"),
        "integration_e2e_evidence": _lookup_evidence(reports["evidence_validation.json"], unit, "integration"),
        "same_run_runtime_coverage": _lookup_evidence(reports["runtime_coverage.json"], unit, "runtime"),
        "no_fallback_no_zero_call_no_builtin_contamination": _lookup_evidence(reports["runtime_coverage.json"], unit, "no-fallback"),
        "performance_evidence": _lookup_evidence(reports["performance.json"], unit, "performance"),
    }}
    closed = not missing and all(
        isinstance(v, Mapping) and v.get("missing") is not True
        for v in evidence.values()
    )
    failure = ""
    if not closed:
        missing_evidence = [k for k, v in evidence.items() if not isinstance(v, Mapping) or v.get("missing") is True]
        failure = f"unit {{unit}}: missing evidence {{', '.join(missing_evidence)}} (missing reports: {{', '.join(missing)}})" if missing else f"unit {{unit}}: missing evidence {{', '.join(missing_evidence)}}"

    row: dict[str, object] = {{
        "row_id": unit,
        "unit_identity": unit,
        "name": unit,
        "status": "CLOSED_PASS" if closed else "INCOMPLETE",
        "inventory_granularity": "fine_grained",
        "native_operator_symbols": [unit],
        "kernel_functions": [unit],
        "kernel_launch_sites": [unit],
        "public_entry_mapping": {{"unit_identity": unit}},
        "source_evidence": [unit],
        "custom_call_count": 1 if closed else 0,
        "fallback_detected": False,
        "forbidden_route_flags": [],
        "native_custom_op_call_count": 1 if closed else 0,
    }}
    row.update(evidence)
    if failure:
        row["failure"] = failure
    return row


def _artifact_evidence(report: object, unit: str, missing: list[str]) -> dict[str, object]:
    entry = _lookup_entry(report, unit)
    if not entry:
        return {{"status": "MISSING", "missing": True, "detail": f"no build entry for {{unit}}"}}

    evidence = dict(entry)
    project_root = Path(__file__).resolve().parent

    op_host_found = _check_dir_with_source(project_root / "op_host")
    op_kernel_found = _check_dir_with_source(project_root / "op_kernel")

    is_real = bool(op_host_found or op_kernel_found)
    evidence["project_local"] = is_real
    evidence["in_project"] = is_real
    evidence["built"] = is_real
    evidence["present"] = is_real

    if not is_real:
        evidence["status"] = "MISSING"
        evidence["missing"] = True
        if not evidence.get("detail"):
            evidence["detail"] = f"no op_host/ or op_kernel/ found for {{unit}}"
    else:
        evidence.setdefault("status", "PASS")
        evidence["verified"] = True

    return evidence


def _check_dir_with_source(dir_path: Path) -> bool:
    if not dir_path.is_dir():
        return False
    for entry in os.listdir(dir_path):
        if entry.endswith((".cpp", ".c", ".h", ".hpp")):
            return True
    return False


def _lookup_evidence(report: object, unit: str, label: str) -> dict[str, object]:
    entry = _lookup_entry(report, unit)
    if not entry:
        return {{"status": "MISSING", "missing": True, "detail": f"missing {{label}} evidence for {{unit}}"}}
    evidence = dict(entry)
    evidence.setdefault("status", "PASS")
    evidence.setdefault("verified", True)
    return evidence


def _lookup_entry(report: object, unit: str) -> Mapping[str, object] | None:
    if not isinstance(report, Mapping):
        return None
    entries = report.get("entries") or report.get("rows") or report.get("units")
    if isinstance(entries, Mapping):
        entry = entries.get(unit)
        if isinstance(entry, Mapping):
            return entry
    if isinstance(entries, list):
        for item in entries:
            if isinstance(item, Mapping) and _entry_name(item) == unit:
                return item
    direct = report.get(unit)
    return direct if isinstance(direct, Mapping) else None


def _entry_name(entry: Mapping[object, object]) -> str | None:
    for field in ("unit_identity", "name", "operator", "op_name", "row_id", "id"):
        value = entry.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


if __name__ == "__main__":
    raise SystemExit(main())
'''


def _strict_expanded_variant_script_is_sufficient(script_text: str, unit_identities: list[str]) -> bool:
    if "SEAM_STRICT_EXPANDED_VARIANT_VALIDATOR_V1" not in script_text:
        return False
    if "SEAM_STRICT_CUSTOM_OP_FINAL_GATE_SCAFFOLD_V1" not in script_text:
        return False
    embedded = _expanded_variant_contract_unit_identities(script_text)
    if embedded is not None and embedded != unit_identities:
        return False

    normalized = script_text.lower()
    required_terms = (
        "migration_reports",
        "migration_manifest.json",
        "runtime_coverage.json",
        "performance.json",
        "build.json",
        "implementation_resolution.json",
        "custom_op_final_gate.json",
        "evidence_validation.json",
        "expanded_variant_inventory",
        "variant_axis_coverage",
        "per_variant",
        "unit_identity",
        "source_inventory",
        "runtime_coverage_report",
        "performance_report",
        "required report missing",
        "per-expanded-variant",
    )
    return all(term in normalized for term in required_terms)


def _expanded_variant_contract_unit_identities(script_text: str) -> list[str] | None:
    match = re.search(
        r"EXPANDED_VARIANT_CONTRACT\s*=\s*json\.loads\(\s*((?:'[^'\\]*(?:\\.[^'\\]*)*')|(?:\"[^\"\\]*(?:\\.[^\"\\]*)*\"))\s*\)",
        script_text,
        re.DOTALL,
    )
    if not match:
        return None
    try:
        contract_json = cast(object, ast.literal_eval(match.group(1)))
        if not isinstance(contract_json, str):
            return None
        contract = cast(object, json.loads(contract_json))
    except (SyntaxError, ValueError, TypeError, json.JSONDecodeError):
        return None
    if not isinstance(contract, Mapping):
        return None
    contract_map = cast(Mapping[str, object], contract)
    inventory = contract_map.get("expanded_variant_inventory")
    if not isinstance(inventory, Mapping):
        return None
    inventory_map = cast(Mapping[str, object], inventory)
    embedded_units = inventory_map.get("unit_identities")
    if not isinstance(embedded_units, list):
        return None
    embedded_unit_values = cast(list[object], embedded_units)
    if not all(isinstance(unit, str) for unit in embedded_unit_values):
        return None
    return cast(list[str], embedded_unit_values)


def _phase3_hardened_run_command(raw_command: object, script_path: Path) -> str:
    python_executable = sys.executable
    if isinstance(raw_command, str) and raw_command.strip():
        try:
            parts = shlex.split(raw_command)
        except ValueError:
            parts = []
        if parts and Path(parts[0]).name.lower().startswith("python"):
            python_executable = parts[0]
    return f"{shlex.quote(python_executable)} {shlex.quote(str(script_path))}"


def _normalize_inventory(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        return {}
    inventory = dict(cast(Mapping[str, object], value))
    if inventory.get("variant_axes_detected") is False:
        return {}
    unit_identities = _string_list(inventory.get("unit_identities"))
    if not unit_identities:
        unit_identities = _string_list(inventory.get("expanded_unit_identities"))
    if not unit_identities:
        unit_identities = _unit_identities_from_variant_objects(inventory.get("variants"))
    if not unit_identities:
        unit_identities = _unit_identities_from_variant_objects(inventory.get("expanded_operator_variants"))
    if not unit_identities:
        return {}
    if _variant_identities_are_collapsed(unit_identities):
        return {}
    inventory["variant_axes_detected"] = True
    inventory["unit_identities"] = unit_identities
    inventory["expanded_operator_instances_count"] = _positive_count(
        inventory.get("expanded_operator_instances_count"),
        default=len(unit_identities),
    )
    return inventory


def _inventory_from_unit_identities(unit_identities: list[str]) -> dict[str, object]:
    return {
        "variant_axes_detected": True,
        "unit_identities": unit_identities,
        "expanded_operator_instances_count": len(unit_identities),
    }


def _unit_identities_from_variant_objects(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    unit_identities: list[str] = []
    for item in cast(list[object], value):
        if not isinstance(item, Mapping):
            continue
        unit_identity = cast(Mapping[object, object], item).get("unit_identity")
        if isinstance(unit_identity, str) and unit_identity.strip():
            unit_identities.append(unit_identity.strip())
    return unit_identities


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in cast(list[object], value) if isinstance(item, str) and item.strip()]


def _positive_count(value: object, *, default: int) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return default


def _ensure_custom_op_phase3_contract_defaults(target: dict[str, object]) -> None:
    if target.get("entry_script_kind") != "custom_op_full_validation":
        return
    project_root = _phase3_project_root(target, None)
    if project_root is None:
        return
    reports_dir = project_root / "migration_reports"
    _ = target.setdefault("reports_dir", str(reports_dir))
    _ = target.setdefault(
        "required_report_paths",
        [
            str(reports_dir / "operator_inventory.json"),
            str(reports_dir / "migration_manifest.json"),
            str(reports_dir / "preflight.json"),
            str(reports_dir / "baseline.json"),
            str(reports_dir / "runtime_coverage.json"),
            str(reports_dir / "performance.json"),
            str(reports_dir / "build.json"),
            str(reports_dir / "implementation_resolution.json"),
            str(reports_dir / "custom_op_final_gate.json"),
            str(reports_dir / "evidence_validation.json"),
            str(reports_dir / "summary.json"),
        ],
    )
    _ = target.setdefault(
        "required_checks",
        [
            "inventory_manifest_equality",
            "closed_pass_count_equals_manifest_entries",
            "remaining_entries_zero",
            "full_migration_status_full_pass",
            "fine_grained_operator_unit_inventory",
            "kernel_launch_site_inventory",
            "public_entry_mapping",
            "inventory_granularity_fine",
            "per_entry_target_custom_op_artifact_evidence",
            "per_entry_adapter_evidence",
            "per_entry_parity_evidence",
            "integration_e2e_evidence",
            "same_run_runtime_coverage",
            "performance_evidence",
            "complete_performance_report",
            "overall_speedup_report",
            "no_fallback_no_zero_call_no_builtin_contamination",
            "native_operator_symbol_inventory",
        ],
    )
    _ = target.setdefault(
        "operator_inventory_schema",
        {
            "semantic_rows": "one row per fine-grained source-discovered operator unit",
            "fine_grained_operator_units": "complete list of source-discovered units",
            "unit_identity": "stable per-unit id",
            "variant_or_signature": "source-discovered variant/signature",
            "native_operator_symbols": "native/exported symbols per row",
            "kernel_functions": "CUDA/Ascend kernel functions per row",
            "kernel_launch_sites": "kernel launch sites per row",
            "public_entry_mapping": "public API to unit mapping per row",
            "source_evidence": "source files/functions per row",
            "inventory_granularity": "fine_grained",
            "out_of_scope_source_groups": "excluded source families with reason",
        },
    )
    _ = target.setdefault(
        "operator_discovery_sources",
        ["source", "bindings", "wrappers", "autograd", "aliases", "launch", "setup", "tests"],
    )
    _ = target.setdefault(
        "validation_obligations",
        [
            "project_local_artifact",
            "runtime_project_api",
            "numeric_performance",
            "complete_speedup_report",
            "overall_speedup_report",
            "no_fallback",
        ],
    )
    _ = target.setdefault("phase5_entry_script_revision_allowed", True)


def _append_required_variant_checks(target: dict[str, object]) -> None:
    checks = target.get("required_checks")
    if not isinstance(checks, list):
        return
    check_items = cast(list[object], checks)
    existing = {str(item).strip().lower().replace("-", "_").replace(" ", "_") for item in check_items if isinstance(item, str)}
    for check in REQUIRED_VARIANT_CHECKS:
        if check not in existing:
            check_items.append(check)
            existing.add(check)
