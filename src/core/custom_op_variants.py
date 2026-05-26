"""Helpers for propagating expanded custom-op variant contracts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from itertools import product
from pathlib import Path
import json
import re
import shlex
import sys
from typing import cast

from core.custom_op_source_discovery import discover_required_cuda_native_units_from_project


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
            retained_evidence.append(item)
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
            retained_variants.append(item)
        surface["expanded_operator_variants"] = retained_variants


def _sample_backed_existing_units(surface: Mapping[str, object]) -> set[str]:
    protected: set[str] = set()
    variants = surface.get("expanded_operator_variants")
    if isinstance(variants, list):
        for item in cast(list[object], variants):
            if not isinstance(item, Mapping):
                continue
            variant = cast(Mapping[object, object], item)
            base_identity = str(variant.get("base_unit_identity") or variant.get("source_unit_identity") or "").strip()
            if base_identity:
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
        base_axes = _axes_for_base_from_variant_axes(base_unit, raw_axes_map, axes)
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
        return axes
    narrowed = dict(axes)
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
    for raw_value in raw_values.split(","):
        value = raw_value.strip().strip("'\"").lower()
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
    base_tokens = set()
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


def _axes_for_base_from_variant_axes(base_unit: str, raw_axes: Mapping[object, object], default_axes: Mapping[str, list[str]]) -> dict[str, list[str]]:
    for raw_group, raw_group_axes in raw_axes.items():
        if not isinstance(raw_group, str):
            continue
        if not _variant_axis_group_matches_base(raw_group, base_unit):
            continue
        group_axes = _normalized_axis_group(raw_group_axes)
        if group_axes:
            return group_axes
    return dict(default_axes)


def _normalized_axis_group(value: object) -> dict[str, list[str]]:
    if isinstance(value, Mapping):
        return _normalized_axis_values(cast(Mapping[object, object], value))
    if isinstance(value, list):
        value_list = cast(list[object], value)
        if all(isinstance(item, Mapping) for item in value_list):
            return _axis_values_from_axis_rows(value_list)
    return {}


def _variant_axis_group_matches_base(group_name: str, base_unit: str) -> bool:
    normalized_group = group_name.strip().lower().replace("-", "_").replace(":", "_")
    normalized_base = base_unit.strip().lower().replace("-", "_").replace(":", "_")
    if not normalized_group:
        return False
    if normalized_group in normalized_base:
        return True
    if normalized_group.endswith("s") and normalized_group[:-1] in normalized_base:
        return True
    return normalized_group in {"propagator", "propagators"} and any(
        family in normalized_base
        for family in ("scalar", "scalar_born", "elastic", "acoustic")
    )


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
    macro_axis = _macro_axis_name(axis)
    if macro_axis:
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


def _macro_axis_name(axis_name: str) -> str:
    normalized = axis_name.strip().lower().replace("-", "_").replace(" ", "_")
    if normalized == "ndim":
        return "DW_NDIM"
    if normalized == "dtype":
        return "DW_DTYPE"
    return ""


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

    script_path = _phase3_validation_script_path(target, project_root)
    existing_text = _read_text_if_file(script_path)
    if not _strict_expanded_variant_script_is_sufficient(existing_text):
        script_path.parent.mkdir(parents=True, exist_ok=True)
        _ = script_path.write_text(
            _render_strict_expanded_variant_validation_script(unit_identities, overlay),
            encoding="utf-8",
        )

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


def _strict_expanded_variant_script_is_sufficient(script_text: str) -> bool:
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
        "provenance",
        "opp",
        "cann",
        "install",
        "required report missing",
        "build_by_id",
        "build rows do not close",
        "per-expanded-variant",
    )
    return all(term in normalized for term in required_terms)


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


def _render_strict_expanded_variant_validation_script(
    unit_identities: Sequence[str],
    overlay: Mapping[str, object],
) -> str:
    raw_axis_coverage = overlay.get("variant_axis_coverage")
    axis_coverage: Mapping[str, object] = cast(Mapping[str, object], raw_axis_coverage) if isinstance(raw_axis_coverage, Mapping) else {}
    raw_per_variant_report = overlay.get("per_variant_performance_report")
    per_variant_report: Mapping[str, object] = (
        cast(Mapping[str, object], raw_per_variant_report) if isinstance(raw_per_variant_report, Mapping) else {}
    )
    unit_payload = json.dumps(list(unit_identities), ensure_ascii=False)
    axis_payload = json.dumps(axis_coverage, ensure_ascii=False, sort_keys=True)
    performance_payload = json.dumps(per_variant_report, ensure_ascii=False, sort_keys=True)
    lines = [
        "#!/usr/bin/env python3",
        "\"\"\"Fail-closed expanded custom-op validation contract generated by SEAM Phase 3.\"\"\"",
        "",
        "from __future__ import annotations",
        "",
        "import json",
        "from pathlib import Path",
        "from typing import Any",
        "",
        "ROOT = Path(__file__).resolve().parent",
        "REPORTS_DIR = ROOT / \"migration_reports\"",
        "REQUIRED_REPORTS = [",
        "    \"operator_inventory.json\",",
        "    \"migration_manifest.json\",",
        "    \"preflight.json\",",
        "    \"baseline.json\",",
        "    \"runtime_coverage.json\",",
        "    \"performance.json\",",
        "    \"build.json\",",
        "    \"implementation_resolution.json\",",
        "    \"custom_op_final_gate.json\",",
        "    \"evidence_validation.json\",",
        "    \"summary.json\",",
        "]",
        f"EXPECTED_UNIT_IDENTITIES = json.loads({unit_payload!r})",
        "EXPANDED_VARIANT_INVENTORY = {",
        "    \"variant_axes_detected\": True,",
        "    \"unit_identities\": EXPECTED_UNIT_IDENTITIES,",
        "    \"expanded_operator_instances_count\": len(EXPECTED_UNIT_IDENTITIES),",
        "    \"target_closure_only\": True,",
        "}",
        f"VARIANT_AXIS_COVERAGE = json.loads({axis_payload!r})",
        f"PER_VARIANT_PERFORMANCE_REPORT = json.loads({performance_payload!r})",
        "",
        "def fail(message: str) -> None:",
        "    raise SystemExit(message)",
        "",
        "def load_json(name: str) -> Any:",
        "    path = REPORTS_DIR / name",
        "    if not path.is_file():",
        "        fail(f\"required report missing: {name}\")",
        "    try:",
        "        return json.loads(path.read_text(encoding=\"utf-8\"))",
        "    except json.JSONDecodeError as exc:",
        "        fail(f\"required report invalid JSON: {name}: {exc}\")",
        "",
        "def candidate_rows(report: Any) -> list[Any]:",
        "    if isinstance(report, list):",
        "        return report",
        "    if not isinstance(report, dict):",
        "        return []",
        "    for key in (\"rows\", \"entries\", \"items\", \"operator_inventory\", \"manifest\", \"build_rows\", \"runtime_rows\", \"performance_rows\", \"final_gate_rows\"):",
        "        value = report.get(key)",
        "        if isinstance(value, list):",
        "            return value",
        "    source_inventory = report.get(\"source_inventory\")",
        "    if isinstance(source_inventory, dict) and isinstance(source_inventory.get(\"entries\"), list):",
        "        return source_inventory[\"entries\"]",
        "    return []",
        "",
        "def row_identity(row: Any) -> str:",
        "    if not isinstance(row, dict):",
        "        return \"\"",
        "    for key in (\"unit_identity\", \"row_id\", \"manifest_row_id\", \"operator\", \"name\", \"id\"):",
        "        value = row.get(key)",
        "        if isinstance(value, str) and value.strip():",
        "            return value.strip()",
        "    return \"\"",
        "",
        "def rows_by_identity(rows: list[Any], report_name: str) -> dict[str, dict[str, Any]]:",
        "    row_by_id: dict[str, dict[str, Any]] = {}",
        "    for index, row in enumerate(rows):",
        "        if not isinstance(row, dict):",
        "            fail(f\"{report_name} row {index} must be an object\")",
        "        identity = row_identity(row)",
        "        if not identity:",
        "            fail(f\"{report_name} row {index} missing unit_identity\")",
        "        if identity in row_by_id:",
        "            fail(f\"{report_name} duplicate unit_identity: {identity}\")",
        "        row_by_id[identity] = row",
        "    return row_by_id",
        "",
        "def assert_exact_identity_set(label: str, row_by_id: dict[str, dict[str, Any]]) -> None:",
        "    expected = EXPECTED_UNIT_IDENTITIES",
        "    if set(row_by_id) != set(expected):",
        "        missing = sorted(set(expected) - set(row_by_id))",
        "        extra = sorted(set(row_by_id) - set(expected))",
        "        fail(f\"{label} rows do not close over every per-expanded-variant unit_identity; missing={missing[:20]} extra={extra[:20]}\")",
        "",
        "def validate_inventory_like(report: Any, report_name: str) -> dict[str, dict[str, Any]]:",
        "    row_by_id = rows_by_identity(candidate_rows(report), report_name)",
        "    assert_exact_identity_set(report_name, row_by_id)",
        "    return row_by_id",
        "",
        "def has_any(row: dict[str, Any], keys: tuple[str, ...]) -> bool:",
        "    return any(bool(row.get(key)) for key in keys)",
        "",
        "def validate_build_report(report: Any) -> dict[str, dict[str, Any]]:",
        "    build_rows = candidate_rows(report)",
        "    build_by_id = rows_by_identity(build_rows, \"build.json\")",
        "    expected = EXPECTED_UNIT_IDENTITIES",
        "    if set(build_by_id) != set(expected):",
        "        missing = sorted(set(expected) - set(build_by_id))",
        "        extra = sorted(set(build_by_id) - set(expected))",
        "        fail(f\"build rows do not close over every per-expanded-variant unit_identity; missing={missing[:20]} extra={extra[:20]}\")",
        "    for unit_identity, build_row in build_by_id.items():",
        "        if not has_any(build_row, (\"cann_build_provenance\", \"cann_build_log\", \"cann_build_evidence\")):",
        "            fail(f\"build row missing CANN build provenance: {unit_identity}\")",
        "        if not has_any(build_row, (\"opp_install_provenance\", \"opp_install_log\", \"install_provenance\")):",
        "            fail(f\"build row missing OPP install provenance: {unit_identity}\")",
        "        if not has_any(build_row, (\"op_host_source\", \"op_host_source_evidence\", \"op_host\")):",
        "            fail(f\"build row missing op_host source evidence: {unit_identity}\")",
        "        if not has_any(build_row, (\"op_kernel_source\", \"ascendc_source\", \"op_kernel_source_evidence\", \"op_kernel\")):",
        "            fail(f\"build row missing op_kernel/AscendC source evidence: {unit_identity}\")",
        "        if not has_any(build_row, (\"generated_opp_package_artifacts\", \"opp_package\", \"kernel_meta\", \"op_info\")):",
        "            fail(f\"build row missing generated OPP package artifacts: {unit_identity}\")",
        "    return build_by_id",
        "",
        "def validate_variant_axis_coverage() -> None:",
        "    if not isinstance(VARIANT_AXIS_COVERAGE, dict) or not VARIANT_AXIS_COVERAGE.get(\"all_axes_covered\"):",
        "        fail(\"variant_axis_coverage missing all_axes_covered=true\")",
        "",
        "def validate_per_variant_performance(report: Any) -> dict[str, dict[str, Any]]:",
        "    row_by_id = validate_inventory_like(report, \"performance.json\")",
        "    if not PER_VARIANT_PERFORMANCE_REPORT.get(\"one_entry_per_expanded_variant\", True):",
        "        fail(\"per_variant performance report must require one entry per expanded variant\")",
        "    return row_by_id",
        "",
        "def main() -> int:",
        "    for name in REQUIRED_REPORTS:",
        "        load_json(name)",
        "    manifest = load_json(\"migration_manifest.json\")",
        "    runtime = load_json(\"runtime_coverage.json\")",
        "    performance = load_json(\"performance.json\")",
        "    build = load_json(\"build.json\")",
        "    implementation = load_json(\"implementation_resolution.json\")",
        "    final_gate = load_json(\"custom_op_final_gate.json\")",
        "    evidence = load_json(\"evidence_validation.json\")",
        "    validate_inventory_like(manifest, \"migration_manifest.json\")",
        "    validate_inventory_like(runtime, \"runtime_coverage.json\")",
        "    validate_per_variant_performance(performance)",
        "    validate_build_report(build)",
        "    validate_inventory_like(implementation, \"implementation_resolution.json\")",
        "    validate_inventory_like(final_gate, \"custom_op_final_gate.json\")",
        "    validate_inventory_like(evidence, \"evidence_validation.json\")",
        "    validate_variant_axis_coverage()",
        "    print(json.dumps({\"status\": \"completed\", \"expanded_variant_inventory\": EXPANDED_VARIANT_INVENTORY, \"variant_axis_coverage\": VARIANT_AXIS_COVERAGE, \"per_variant\": \"per-expanded-variant performance report closed\", \"unit_count\": len(EXPECTED_UNIT_IDENTITIES)}, indent=2, sort_keys=True))",
        "    return 0",
        "",
        "if __name__ == \"__main__\":",
        "    raise SystemExit(main())",
        "",
    ]
    return "\n".join(lines)


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
