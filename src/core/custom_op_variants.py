"""Helpers for propagating expanded custom-op variant contracts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from itertools import product
import re
from typing import cast


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
    variants = surface.get("expanded_operator_variants")
    if isinstance(variants, list) and variants:
        surface["expanded_operator_instances_count"] = len(variants)

    generated = _source_template_expanded_variants(surface)
    if not generated:
        return
    existing_variants = cast(list[object], variants) if isinstance(variants, list) else []
    if not _should_replace_with_source_template_variants(existing_variants, generated):
        return
    surface["expanded_operator_variants"] = generated
    surface["expanded_operator_instances_count"] = len(generated)
    _merge_variant_axes_from_generated_rows(surface, generated)


def _source_template_expanded_variants(surface: Mapping[str, object]) -> list[dict[str, object]]:
    if surface.get("variant_axes_detected") is not True:
        return []
    raw_axes = surface.get("variant_axes")
    if not isinstance(raw_axes, Mapping):
        return []
    axes = _normalized_axis_values(cast(Mapping[object, object], raw_axes))
    if not axes:
        return []
    fine_units = _string_list(surface.get("fine_grained_operator_units"))
    base_units = [unit for unit in fine_units if "=" not in unit]
    if not base_units:
        return []

    names = _string_list(surface.get("discovered_operator_names"))
    symbols = _string_list(surface.get("native_operator_symbols"))
    evidence_by_unit = _fine_grained_evidence_by_unit(surface.get("fine_grained_operator_unit_evidence"))
    sample_by_base = _variant_samples_by_base(surface.get("expanded_operator_variants"))

    generated: list[dict[str, object]] = []
    for index, base_unit in enumerate(base_units):
        descriptor_text = _descriptor_text_for_unit(base_unit, index, names, symbols, evidence_by_unit)
        axis_names = [axis for axis in _template_axis_order(axes) if _descriptor_mentions_axis(descriptor_text, axis)]
        if not axis_names:
            continue
        device_values = _device_values_for_base(base_unit, axes)
        source_evidence = _source_evidence_for_base(base_unit, evidence_by_unit, sample_by_base, surface)
        public_routes = _routes_for_base(base_unit, "candidate_public_api_routes", evidence_by_unit, sample_by_base)
        framework_routes = _routes_for_base(base_unit, "candidate_framework_integration_routes", evidence_by_unit, sample_by_base)

        for axis_values in _axis_value_product(axis_names, axes):
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


def _normalized_axis_values(raw_axes: Mapping[object, object]) -> dict[str, list[str]]:
    axes: dict[str, list[str]] = {}
    for raw_axis, raw_values in raw_axes.items():
        if not isinstance(raw_axis, str) or not isinstance(raw_values, list):
            continue
        axis_name = raw_axis.strip()
        values = [str(value).strip().lower() for value in cast(list[object], raw_values) if isinstance(value, (str, int, float)) and not isinstance(value, bool) and str(value).strip()]
        if values:
            axes[axis_name] = _ordered_unique(values)
    return axes


def _template_axis_order(axes: Mapping[str, list[str]]) -> list[str]:
    return [axis for axis in axes if axis != "device" and not _axis_is_implementation_detail(axis)]


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
    if compact != escaped:
        patterns = (*patterns, rf"<\s*{compact}\s*>", rf"\$\{{\s*{compact}\s*\}}")
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def _axis_is_implementation_detail(axis_name: str) -> bool:
    normalized = axis_name.strip().lower().replace("-", "_").replace(" ", "_")
    return any(pattern.search(normalized) for pattern in IMPLEMENTATION_DETAIL_AXIS_PATTERNS)


def _descriptor_text_for_unit(
    base_unit: str,
    index: int,
    names: list[str],
    symbols: list[str],
    evidence_by_unit: Mapping[str, Mapping[object, object]],
) -> str:
    parts = [base_unit]
    if index < len(names):
        parts.append(names[index])
    if index < len(symbols):
        parts.append(symbols[index])
    evidence = evidence_by_unit.get(base_unit)
    if evidence:
        parts.append(_flatten_text(evidence))
    return "\n".join(parts)


def _device_values_for_base(base_unit: str, axes: Mapping[str, list[str]]) -> list[str]:
    match = DEVICE_SUFFIX_PATTERN.search(base_unit)
    if match:
        return [match.group(1).lower()]
    axis_values = axes.get("device")
    if axis_values:
        return axis_values
    return [""]


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
    existing_bases = _base_identities_from_variant_rows(existing_variants)
    generated_bases = _base_identities_from_variant_rows(generated)
    if not generated_bases.issubset(existing_bases):
        return True
    return len(existing_variants) < len(generated)


def _base_identities_from_variant_rows(variants: Sequence[object]) -> set[str]:
    bases: set[str] = set()
    for item in variants:
        if not isinstance(item, Mapping):
            continue
        variant = cast(Mapping[object, object], item)
        base = variant.get("base_unit_identity") or variant.get("source_unit_identity")
        if isinstance(base, str) and base.strip():
            bases.add(base.strip())
            continue
        unit_identity = variant.get("unit_identity")
        if isinstance(unit_identity, str) and unit_identity.strip():
            bases.add(":".join(part for part in unit_identity.strip().split(":") if "=" not in part))
    return bases


def _merge_variant_axes_from_generated_rows(surface: dict[str, object], generated: list[dict[str, object]]) -> None:
    axes = _normalized_axis_values(cast(Mapping[object, object], surface.get("variant_axes", {}))) if isinstance(surface.get("variant_axes"), Mapping) else {}
    for row in generated:
        axis_values = row.get("axis_values")
        if not isinstance(axis_values, Mapping):
            continue
        for raw_axis, raw_value in cast(Mapping[object, object], axis_values).items():
            if isinstance(raw_axis, str) and isinstance(raw_value, str) and raw_value.strip():
                axes.setdefault(raw_axis, [])
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
            samples.setdefault(base.strip(), variant)
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
