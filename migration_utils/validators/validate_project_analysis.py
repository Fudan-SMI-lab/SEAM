"""Validation for Phase 1 project analysis output."""

from dataclasses import dataclass
from pathlib import Path
import re
from typing import cast

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

    source_discovered_units = _discover_required_cuda_native_units_from_project(project_dir)
    custom_op_surface = data.get("custom_op_surface")
    if custom_op_surface is None:
        if source_discovered_units:
            errors.append(
                "custom_op_surface must be present and custom_op_detected must be true when CUDA/native custom-op units are discovered from source: "
                + _format_native_units(source_discovered_units)
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
            if custom_op_detected is True:
                if surface.get("discovery_complete") is not True:
                    errors.append("custom_op_surface.discovery_complete must be true when custom_op_detected is true")
                elif cast(list[object], surface.get("unresolved_source_groups", [])):
                    errors.append("custom_op_surface.unresolved_source_groups must be empty when discovery_complete is true")
                _validate_required_sources(surface, errors)
                _validate_fine_grained_unit_evidence(surface, errors)
                _validate_source_discovered_cuda_units(source_discovered_units, surface, errors)

    return {"passed": not errors, "errors": errors, "warnings": []}


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

    if unit_identities:
        expected = set(unit_identities)
        observed = set(evidence_unit_identities)
        if observed != expected:
            missing = sorted(expected - observed)
            extra = sorted(observed - expected)
            details: list[str] = []
            if missing:
                details.append("missing unit evidence: " + ", ".join(missing))
            if extra:
                details.append("evidence without matching units: " + ", ".join(extra))
            errors.append(
                "custom_op_surface.fine_grained_operator_unit_evidence must provide one source-linked entry for every fine_grained_operator_unit"
                + (" (" + "; ".join(details) + ")" if details else "")
            )


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
