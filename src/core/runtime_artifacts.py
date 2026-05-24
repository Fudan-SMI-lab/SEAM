"""Runtime markdown artifacts for slim repair prompts."""

from __future__ import annotations

import re
import json
from pathlib import Path
from typing import cast

from core.custom_op_variants import expanded_variant_contract_from_contract


NO_EXPERIENCE_CARDS_NOTE = "(No analyzer-selected experience cards)"


def sanitize_project_name(project_dir: str) -> str:
    name = Path(project_dir).resolve().name
    sanitized = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    return sanitized or "project"


def write_repair_runtime_artifacts(
    *,
    artifact_dir: str,
    project_dir: str,
    entry_script: str,
    error_text: str,
    category: str,
    root_cause: str,
    suggested_fix: str,
    repair_role: str,
    experience_action_cards: object = None,
) -> tuple[str, str]:
    runtime_dir = Path(artifact_dir) / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    project_name = sanitize_project_name(project_dir)

    runtime_error_path = runtime_dir / f"runtime_error_{project_name}.md"
    runtime_card_path = runtime_dir / f"runtimeCard_{project_name}.md"

    _ = runtime_error_path.write_text(
        _repair_runtime_error_markdown(
            project_dir=project_dir,
            entry_script=entry_script,
            error_text=error_text,
            category=category,
            root_cause=root_cause,
            suggested_fix=suggested_fix,
            repair_role=repair_role,
        ),
        encoding="utf-8",
    )
    _ = runtime_card_path.write_text(
        _repair_runtime_card_markdown(repair_role, experience_action_cards),
        encoding="utf-8",
    )

    return str(runtime_error_path.resolve()), str(runtime_card_path.resolve())


def write_operator_runtime_artifacts(
    *,
    artifact_dir: str,
    project_dir: str,
    entry_script: str,
    error_text: str,
    category: str,
    root_cause: str,
    suggested_fix: str,
    repair_role: str,
    experience_action_cards: object = None,
) -> tuple[str, str]:
    return write_repair_runtime_artifacts(
        artifact_dir=artifact_dir,
        project_dir=project_dir,
        entry_script=entry_script,
        error_text=error_text,
        category=category,
        root_cause=root_cause,
        suggested_fix=suggested_fix,
        repair_role=repair_role,
        experience_action_cards=experience_action_cards,
    )


def write_operator_repair_context_artifact(
    *,
    artifact_dir: str,
    project_dir: str,
    entry_script: str,
    phase3_contract: dict[str, object] | None = None,
    phase1_analysis: dict[str, object] | None = None,
) -> str:
    runtime_dir = Path(artifact_dir) / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    project_name = sanitize_project_name(project_dir)
    context_path = runtime_dir / f"operatorRepairContext_{project_name}.md"
    contract = dict(phase3_contract or {})

    _ = context_path.write_text(
        _operator_repair_context_markdown(
            project_dir=project_dir,
            entry_script=entry_script,
            contract=contract,
            phase1_analysis=dict(phase1_analysis or {}),
        ),
        encoding="utf-8",
    )
    return str(context_path.resolve())


def _role_title(repair_role: str) -> str:
    titles = {
        "dependency_fixer": "Dependency Fixer",
        "operator_fixer": "Operator Fixer",
        "code_adapter": "Code Adapter",
    }
    return titles.get(repair_role, repair_role.replace("_", " ").title())


def _repair_runtime_error_markdown(
    *,
    project_dir: str,
    entry_script: str,
    error_text: str,
    category: str,
    root_cause: str,
    suggested_fix: str,
    repair_role: str,
) -> str:
    title = _role_title(repair_role)
    return "\n".join(
        [
            f"# {title}",
            "",
            "## Execution Failure",
            "```",
            error_text or "(No execution failure text available)",
            "```",
            "",
            "## Error Classification",
            f"- Category: {category or 'unknown'}",
            f"- Root Cause: {root_cause or '(not provided)'}",
            f"- Suggested Fix: {suggested_fix or '(not provided)'}",
            f"- Repair Role: {repair_role or 'operator_fixer'}",
            f"- Project Dir: {project_dir}",
            f"- Entry Command: {entry_script or '(not provided)'}",
            "",
        ]
    )


def _repair_runtime_card_markdown(repair_role: str, experience_action_cards: object) -> str:
    title = _role_title(repair_role)
    cards = [str(card) for card in cast(list[object], experience_action_cards)] if isinstance(experience_action_cards, list) else []
    if not cards:
        return f"# {title} Runtime Cards\n\n{NO_EXPERIENCE_CARDS_NOTE}\n"

    lines = [f"# {title} Runtime Cards", ""]
    for index, card in enumerate(cards, start=1):
        lines.extend([f"## Experience Card {index}", str(card).strip(), ""])
    return "\n".join(lines)


def _operator_repair_context_markdown(
    *,
    project_dir: str,
    entry_script: str,
    contract: dict[str, object],
    phase1_analysis: dict[str, object],
) -> str:
    project_path = Path(project_dir).resolve()
    reports_dir = _reports_dir(project_path, contract)
    inventory_path = reports_dir / "operator_inventory.json"
    manifest_path = reports_dir / "migration_manifest.json"
    gate_path = reports_dir / "custom_op_final_gate.json"
    warnings: list[str] = []

    inventory = _read_json_report(inventory_path, warnings)
    manifest = _read_json_report(manifest_path, warnings)
    gate = _read_json_report(gate_path, warnings)

    expanded_variant_units = _expanded_variant_units_from_contract(contract)
    phase3_units = _phase3_contract_operator_units(contract)
    expanded_variant_count = _expanded_variant_count_from_contract(contract, expanded_variant_units)
    total_count = _best_effort_total_count(inventory, manifest, gate)
    units = _operator_units(inventory, manifest, gate)
    inventory_source = "migration_reports"
    if not units:
        fallback_units = expanded_variant_units or phase3_units
        if fallback_units:
            units = fallback_units
            inventory_source = "phase_3_expanded_variant_contract_fallback" if expanded_variant_units else "phase_3_contract_fallback"
            warnings.append(
                "Using Phase 3 custom-op inventory fallback because migration_reports inventory/manifest/final gate are not available yet; fallback rows are scope only, not final evidence."
            )
    if total_count is None and units:
        total_count = len(units)
    if expanded_variant_units and units and not _all_units_present(expanded_variant_units, units):
        warnings.append(
            "Current migration_reports do not cover every Phase 3 expanded variant identity; continue repair from the expanded variant scope, not from collapsed report rows."
        )
    if expanded_variant_units and total_count is not None and expanded_variant_count is not None and total_count < expanded_variant_count:
        warnings.append(
            f"Current report total_count={total_count} is smaller than Phase 3 expanded_variant_count={expanded_variant_count}; treat reports as incomplete closure evidence."
        )
    if phase3_units and units and not _all_units_present(phase3_units, units):
        warnings.append(
            "Current migration_reports do not cover every Phase 3 operator identity; continue repair from the Phase 3 operator scope."
        )
    progress = _progress_summary(gate)

    required_report_paths = _string_list(contract.get("required_report_paths"))
    required_checks = _string_list(contract.get("required_checks"))
    if not required_report_paths:
        required_report_paths = [str(inventory_path), str(manifest_path), str(gate_path)]
    if not required_checks:
        required_checks = [
            "inventory_manifest_equality",
            "closed_pass_count_equals_manifest_entries",
            "remaining_entries_zero",
            "full_migration_status_full_pass",
            "no_fallback_no_zero_call_no_builtin_contamination",
        ]

    lines = [
        "# Operator Repair Context",
        "",
        "## Scope",
        f"- Project Dir: {project_path}",
        f"- Entry Command: {entry_script or str(contract.get('run_command', '(not provided)'))}",
        f"- Entry Script Path: {contract.get('entry_script_path', '(not provided)')}",
        f"- Entry Script Kind: {contract.get('entry_script_kind', '(not provided)')}",
        f"- Phase 5 Entry Script Revision Allowed: {contract.get('phase5_entry_script_revision_allowed', False)}",
        f"- Reports Dir: {reports_dir}",
        "",
        "## Phase 1 Discovery Summary",
        *_phase1_discovery_lines(phase1_analysis),
        "",
        "## Phase 3 Validation Contract Summary",
        f"- Run Command: {contract.get('run_command', entry_script or '(not provided)')}",
        f"- Required Report Paths Count: {len(required_report_paths)}",
        f"- Required Checks Count: {len(required_checks)}",
        "- The Phase 3 command is the validation source of truth; run it after repairs and make its emitted reports close the full Phase 1/Phase 3 scope.",
        "",
        "## Inventory / Manifest / Final-Gate Closure",
        "- Inventory is the discovery output: it records fine-grained operator/custom-op units, their variants/signatures, launch sites, public entries, and source evidence.",
        "- Manifest is the closure output: it records the coverage rows that must close every discovered fine-grained unit.",
        "- The final gate is the machine check that compares inventory, manifest, and runtime evidence and fails closed on mismatches.",
        "- source_inventory is the authoritative source-discovery proof for each manifest row; if a row cannot be matched back to source_inventory, the run must re-discover or re-close instead of passing.",
        "- Missing rows, mismatched rows, or incomplete evidence must force a re-discovery / re-closure loop rather than a false FULL_PASS.",
        "",
        "## Final Validation Goal",
        "- FULL_PASS is required.",
        "- remaining_entries must be 0.",
        "- Every manifest/inventory entry must be a fine-grained unit and must be closed with passing custom-op artifact, adapter, parity, integration, runtime coverage, and performance evidence.",
        "- No CPU fallback, zero-call fake coverage, or builtin contamination is allowed.",
        "",
        "## Required Reports",
        *[f"- {path}" for path in required_report_paths],
        "",
        "## Required Checks",
        *[f"- {check}" for check in required_checks],
        "",
        "## Discovered Inventory Paths",
        f"- Operator Inventory: {inventory_path}",
        f"- Migration Manifest: {manifest_path}",
        f"- Custom-Op Final Gate: {gate_path}",
        "",
        "## Operator Inventory Summary",
        f"- Total Count: {total_count if total_count is not None else 'unknown'}",
        f"- Unit Count Listed Here: {len(units)}",
        f"- Inventory Source: {inventory_source}",
        "",
        "## Expanded Variant Inventory",
        f"- Expanded Variant Count: {expanded_variant_count if expanded_variant_count is not None else 'unknown'}",
        f"- Expanded Variant Units Listed Here: {len(expanded_variant_units)}",
        "",
    ]
    if phase3_units:
        lines.append("## Phase 3 Operator Units")
        for index, unit in enumerate(phase3_units, start=1):
            lines.append(f"- Phase3 Unit {index}: {unit}")
        lines.append("")
    if expanded_variant_units:
        lines.append("## Expanded Variant Units")
        for index, unit in enumerate(expanded_variant_units, start=1):
            lines.append(f"- Variant {index}: {unit}")
        lines.append("")
    if units:
        lines.append("## Parallelizable Operator Units")
        for index, unit in enumerate(units, start=1):
            lines.append(f"- Unit {index}: {unit}")
        lines.append("")
    else:
        lines.extend(["## Parallelizable Operator Units", "- No per-operator units found in reports; inspect the discovered inventory paths before editing.", ""])

    lines.extend([
        "## Current Final-Gate Progress",
        *[f"- {item}" for item in progress],
        "",
        "## Bounded Parallelization Guidance",
        "- Repair only the currently failing or assigned operator/custom-op units needed for the final gate.",
        "- Independent operator units may be split into bounded sub-tasks when their source files, build artifacts, and tests do not overlap.",
        "- Merge sub-task results before running the entry command and final-gate checks.",
        "- Treat the discovered inventory, manifest coverage rows, and final gate as the source of truth; do not invent passes for rows that are missing from source_inventory.",
        "- Do not execute docs/cuda_custom_op_skill_test_prompt.md as a workplan; this artifact is the bounded repair context.",
        "",
        "## Warnings",
    ])
    lines.extend([f"- {warning}" for warning in warnings] or ["- None"])
    lines.append("")
    return "\n".join(lines)


def _phase1_discovery_lines(phase1_analysis: dict[str, object]) -> list[str]:
    if not phase1_analysis:
        return ["- Phase 1 project analysis was not provided in this repair context."]
    lines: list[str] = []
    for key in ("project_type", "entry_script", "operator_unit_count", "inventory_count", "expanded_operator_instances_count"):
        value = phase1_analysis.get(key)
        if value not in (None, "", []):
            lines.append(f"- phase1.{key}: {value}")
    surface = phase1_analysis.get("custom_op_surface")
    if isinstance(surface, dict):
        surface_dict = cast(dict[str, object], surface)
        for key in (
            "custom_op_detected",
            "variant_axes_detected",
            "expanded_operator_instances_count",
        ):
            value = surface_dict.get(key)
            if value not in (None, "", []):
                lines.append(f"- custom_op_surface.{key}: {value}")
        for key in (
            "fine_grained_operator_units",
            "discovered_operator_names",
            "native_operator_symbols",
            "kernel_launch_sites",
            "source_evidence",
        ):
            value = surface_dict.get(key)
            if isinstance(value, list) and value:
                sample = ", ".join(str(item) for item in cast(list[object], value)[:20])
                lines.append(f"- custom_op_surface.{key}: count={len(value)} sample={sample}")
        axes = surface_dict.get("variant_axes")
        if isinstance(axes, dict) and axes:
            lines.append("- custom_op_surface.variant_axes: " + json.dumps(axes, ensure_ascii=False, default=str))
        variants = surface_dict.get("expanded_operator_variants")
        if isinstance(variants, list) and variants:
            unit_ids: list[str] = []
            for item in cast(list[object], variants)[:50]:
                if isinstance(item, dict) and item.get("unit_identity"):
                    unit_ids.append(str(item["unit_identity"]))
                else:
                    unit_ids.append(str(item)[:200])
            lines.append(f"- custom_op_surface.expanded_operator_variants: count={len(variants)}")
            lines.extend(f"  - {unit_id}" for unit_id in unit_ids)
    if not lines:
        lines.append("- Phase 1 analysis is present but has no custom-op discovery summary fields.")
    return lines


def _reports_dir(project_path: Path, contract: dict[str, object]) -> Path:
    raw = contract.get("reports_dir")
    if isinstance(raw, str) and raw.strip():
        return Path(raw).expanduser().resolve()
    return (project_path / "migration_reports").resolve()


def _read_json_report(path: Path, warnings: list[str]) -> object:
    if not path.is_file():
        warnings.append(f"Missing report: {path}")
        return None
    try:
        return cast(object, json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as exc:
        warnings.append(f"Could not parse report {path}: {exc}")
        return None


def _string_list(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        items = cast(list[object], value)
        return [str(item) for item in items if str(item).strip()]
    return []


def _candidate_entries(data: object) -> list[object]:
    if isinstance(data, list):
        return cast(list[object], data)
    data_dict = _object_dict(data)
    if data_dict is None:
        return []
    source_inventory = data_dict.get("source_inventory")
    if isinstance(source_inventory, dict):
        source_entries = cast(dict[str, object], source_inventory).get("entries")
        if isinstance(source_entries, list):
            return cast(list[object], source_entries)
    for key in ("operators", "custom_operators", "entries", "items", "rows", "operator_inventory", "manifest"):
        value = data_dict.get(key)
        if isinstance(value, list):
            return cast(list[object], value)
    return []


def _object_dict(value: object) -> dict[str, object] | None:
    if isinstance(value, dict):
        return cast(dict[str, object], value)
    return None


def _best_effort_total_count(inventory: object, manifest: object, gate: object) -> int | None:
    sources: list[tuple[object, tuple[str, ...]]] = [
        (inventory, ("total_count", "inventory_count", "count")),
        (manifest, ("manifest_entries", "total_count", "count")),
        (gate, ("inventory_count", "manifest_entries")),
    ]
    for data_obj, keys in sources:
        data_dict = _object_dict(data_obj)
        if data_dict is not None:
            for key in keys:
                value = data_dict.get(key)
                if isinstance(value, int):
                    return value
                if isinstance(value, str) and value.isdigit():
                    return int(value)
        entries = _candidate_entries(data_obj)
        if entries:
            return len(entries)
    return None


def _operator_units(inventory: object, manifest: object, gate: object) -> list[str]:
    units: list[str] = []
    for data in (inventory, manifest, gate):
        for entry in _candidate_entries(data):
            summary = _entry_summary(entry)
            if summary and summary not in units:
                units.append(summary)
            if len(units) >= 50:
                return units
    return units


def _phase3_contract_operator_units(contract: dict[str, object]) -> list[str]:
    candidates: list[object] = []

    schema = contract.get("operator_inventory_schema")
    if isinstance(schema, dict):
        schema_dict = cast(dict[str, object], schema)
        for key in ("fine_grained_operator_units", "operator_units", "operators", "rows"):
            value = schema_dict.get(key)
            if isinstance(value, list):
                candidates.extend(cast(list[object], value))
                break

    if not candidates:
        surface = contract.get("custom_op_surface")
        if isinstance(surface, dict):
            surface_dict = cast(dict[str, object], surface)
            for key in ("fine_grained_operator_units", "operator_units", "native_operator_symbols", "discovered_operator_names"):
                value = surface_dict.get(key)
                if isinstance(value, list):
                    candidates.extend(cast(list[object], value))
                    break

    if not candidates:
        source_inventory = contract.get("source_inventory")
        if isinstance(source_inventory, dict):
            entries = cast(dict[str, object], source_inventory).get("entries")
            if isinstance(entries, list):
                candidates.extend(cast(list[object], entries))

    units: list[str] = []
    for item in candidates:
        if isinstance(item, dict):
            summary = _entry_summary(cast(dict[str, object], item))
        else:
            summary = str(item).strip()
        if summary and summary not in units:
            units.append(summary[:300])
        if len(units) >= 50:
            break
    return units


def _expanded_variant_units_from_contract(contract: dict[str, object]) -> list[str]:
    overlay = expanded_variant_contract_from_contract(contract)
    inventory = overlay.get("expanded_variant_inventory")
    if not isinstance(inventory, dict):
        return []
    return _string_list(cast(dict[str, object], inventory).get("unit_identities"))[:50]


def _expanded_variant_count_from_contract(contract: dict[str, object], units: list[str]) -> int | None:
    overlay = expanded_variant_contract_from_contract(contract)
    inventory = overlay.get("expanded_variant_inventory")
    if isinstance(inventory, dict):
        count = cast(dict[str, object], inventory).get("expanded_operator_instances_count")
        if isinstance(count, int) and not isinstance(count, bool) and count > 0:
            return count
    if units:
        return len(units)
    return None


def _all_units_present(required_units: list[str], candidate_units: list[str]) -> bool:
    normalized_candidates = "\n".join(candidate_units).lower()
    return all(str(unit).lower() in normalized_candidates for unit in required_units)


def _entry_summary(entry: object) -> str:
    if not isinstance(entry, dict):
        return str(entry)[:300]
    entry_dict = cast(dict[str, object], entry)
    parts: list[str] = []
    for key in ("unit_identity", "name", "op_name", "operator", "schema", "symbol"):
        if entry_dict.get(key):
            parts.append(f"name={entry_dict[key]}")
            break
    for key in ("variant_or_signature", "inventory_granularity"):
        if entry_dict.get(key):
            parts.append(f"{key}={entry_dict[key]}")
    for key in ("status", "state", "migration_status"):
        if entry_dict.get(key):
            parts.append(f"status={entry_dict[key]}")
            break
    for key in ("native_operator_symbols", "kernel_functions", "kernel_launch_sites", "public_entry_mapping", "source_evidence"):
        if entry_dict.get(key):
            parts.append(f"{key}={_compact_entry_value(entry_dict[key])}")
    for key in ("path", "file", "source", "source_file", "source_path"):
        if entry_dict.get(key):
            parts.append(f"path={entry_dict[key]}")
            break
    if not parts:
        for key, value in list(entry_dict.items())[:4]:
            parts.append(f"{key}={value}")
    return ", ".join(parts)[:300]


def _compact_entry_value(value: object) -> str:
    if isinstance(value, list):
        return ",".join(str(item) for item in cast(list[object], value)[:4])
    if isinstance(value, tuple):
        return ",".join(str(item) for item in cast(tuple[object, ...], value)[:4])
    if isinstance(value, dict):
        return ",".join(str(key) for key in list(cast(dict[object, object], value))[:4])
    return str(value)


def _progress_summary(gate: object) -> list[str]:
    if not isinstance(gate, dict):
        return ["custom_op_final_gate.json unavailable or malformed"]
    gate_dict = cast(dict[str, object], gate)
    fields = (
        "inventory_count",
        "manifest_entries",
        "closed_pass_entries",
        "remaining_entries",
        "full_migration_status",
        "project_e2e_passed",
        "report_parity_passed",
    )
    return [f"{field}: {gate_dict.get(field, '(missing)')}" for field in fields]
