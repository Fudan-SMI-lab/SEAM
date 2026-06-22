"""Validation for Phase 1 project analysis output."""

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
    "source_evidence",
    "negative_evidence",
    "dynamic_loading_checks",
    "build_load_checks",
    "unresolved_source_groups",
    "out_of_scope_source_groups",
    "fine_grained_operator_unit_evidence",
)

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

    custom_op_surface = data.get("custom_op_surface")
    if custom_op_surface is not None:
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
                non_empty_message=(
                    "custom_op_surface.discovery_sources_checked must contain "
                    "the full source discovery category set when custom_op_detected is true"
                ),
                require_non_empty=custom_op_detected is True,
            )
            _validate_string_list(
                surface,
                "searched_source_roots",
                errors,
                non_empty_message=(
                    "custom_op_surface.searched_source_roots must contain "
                    "at least one source root when custom_op_detected is true"
                ),
                require_non_empty=custom_op_detected is True,
            )
            _validate_string_list(
                surface,
                "searched_source_paths",
                errors,
                non_empty_message=(
                    "custom_op_surface.searched_source_paths must contain "
                    "at least one source path when custom_op_detected is true"
                ),
                require_non_empty=custom_op_detected is True,
            )
            _validate_string_list(
                surface,
                "operator_families",
                errors,
                non_empty_message=(
                    "custom_op_surface.operator_families must contain "
                    "at least one family when custom_op_detected is true"
                ),
                require_non_empty=custom_op_detected is True,
            )
            _validate_string_list(
                surface,
                "fine_grained_operator_units",
                errors,
                non_empty_message=(
                    "custom_op_surface.fine_grained_operator_units must contain "
                    "at least one source-discovered fine-grained operator unit "
                    "when custom_op_detected is true"
                ),
                require_non_empty=custom_op_detected is True,
            )
            _validate_string_list(
                surface,
                "discovered_operator_names",
                errors,
                non_empty_message=(
                    "custom_op_surface.discovered_operator_names must contain "
                    "at least one source-discovered operator name "
                    "when custom_op_detected is true"
                ),
                require_non_empty=custom_op_detected is True,
            )
            _validate_string_list(
                surface,
                "source_evidence",
                errors,
                non_empty_message=(
                    "custom_op_surface.source_evidence must contain "
                    "at least one source proof when custom_op_detected is true"
                ),
                require_non_empty=custom_op_detected is True,
            )
            _validate_string_list(
                surface,
                "negative_evidence",
                errors,
                non_empty_message=(
                    "custom_op_surface.negative_evidence must contain "
                    "at least one negative probe when custom_op_detected is true"
                ),
                require_non_empty=custom_op_detected is True,
            )
            _validate_string_list(
                surface,
                "dynamic_loading_checks",
                errors,
                non_empty_message=(
                    "custom_op_surface.dynamic_loading_checks must contain "
                    "at least one dynamic loading check when custom_op_detected is true"
                ),
                require_non_empty=custom_op_detected is True,
            )
            _validate_string_list(
                surface,
                "build_load_checks",
                errors,
                non_empty_message=(
                    "custom_op_surface.build_load_checks must contain "
                    "at least one build/load check when custom_op_detected is true"
                ),
                require_non_empty=custom_op_detected is True,
            )
            _validate_string_list(surface, "unresolved_source_groups", errors)
            _validate_string_list(surface, "out_of_scope_source_groups", errors)
            if custom_op_detected is True:
                if surface.get("discovery_complete") is not True:
                    errors.append(
                        "custom_op_surface.discovery_complete must be true ",
                        "when custom_op_detected is true",
                    )
                elif cast(list[object], surface.get("unresolved_source_groups", [])):
                    errors.append(
                        "custom_op_surface.unresolved_source_groups must be empty "
                        "when discovery_complete is true"
                    )
                _validate_required_sources(surface, errors)
                _validate_fine_grained_unit_evidence(surface, errors)

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
        errors.append(
            f"custom_op_surface.{field_name} must contain only non-empty strings"
        )
    if require_non_empty and not items:
        errors.append(
            non_empty_message
            or (
                f"custom_op_surface.{field_name} must contain "
                f"at least one item when custom_op_detected is true"
            )
        )


def _validate_required_sources(surface: dict[str, object], errors: list[str]) -> None:
    sources = surface.get("discovery_sources_checked")
    if not isinstance(sources, list):
        return
    source_values = {
        str(source).strip().lower().replace("-", "_")
        for source in cast(list[object], sources)
    }
    missing_sources = sorted(set(REQUIRED_DISCOVERY_SOURCES) - source_values)
    if missing_sources:
        errors.append(
            "custom_op_surface.discovery_sources_checked missing required sources: "
            + ", ".join(missing_sources)
        )


def _validate_fine_grained_unit_evidence(
    surface: dict[str, object], errors: list[str]
) -> None:
    unit_values = surface.get("fine_grained_operator_units")
    if not isinstance(unit_values, list):
        return
    unit_identities = [
        unit
        for unit in cast(list[object], unit_values)
        if isinstance(unit, str) and unit.strip()
    ]

    evidence_values = surface.get("fine_grained_operator_unit_evidence")
    if not isinstance(evidence_values, list):
        errors.append(
            "custom_op_surface.fine_grained_operator_unit_evidence must be a list"
        )
        return
    evidence_items = cast(list[object], evidence_values)
    if not evidence_items:
        errors.append(
            "custom_op_surface.fine_grained_operator_unit_evidence must ",
            "contain at least one source-linked entry when custom_op_detected is true",
        )
        return

    evidence_unit_identities: list[str] = []
    for index, item in enumerate(evidence_items):
        if not isinstance(item, dict):
            errors.append(
                f"custom_op_surface.fine_grained_operator_unit_evidence[{index}] must be an object"
            )
            continue
        evidence = cast(dict[str, object], item)
        unit_identity = evidence.get("unit_identity")
        if not isinstance(unit_identity, str) or not unit_identity.strip():
            errors.append(
                f"custom_op_surface.fine_grained_operator_unit_evidence[{index}]."
                f"unit_identity must be a non-empty string"
            )
        else:
            evidence_unit_identities.append(unit_identity.strip())

        source_evidence = evidence.get("source_evidence")
        if not isinstance(source_evidence, list):
            errors.append(
                f"custom_op_surface.fine_grained_operator_unit_evidence[{index}]."
                f"source_evidence must be a list"
            )
            continue
        source_items = cast(list[object], source_evidence)
        if not source_items or not all(
            isinstance(source, str) and source.strip() for source in source_items
        ):
            errors.append(
                f"custom_op_surface.fine_grained_operator_unit_evidence[{index}]."
                f"source_evidence must contain only non-empty strings"
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
                "custom_op_surface.fine_grained_operator_unit_evidence must ",
                "provide one source-linked entry for every ",
                "fine_grained_operator_unit"
                + (" (" + "; ".join(details) + ")" if details else ""),
            )
