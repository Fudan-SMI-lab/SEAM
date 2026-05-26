"""Runtime markdown artifacts for slim repair prompts."""

from __future__ import annotations

import re
import json
from pathlib import Path
from typing import cast

from core.custom_op_variants import expanded_variant_contract_from_contract
from validators.validate_validation_final import custom_op_final_gate_unit_ledger, validate_custom_op_final_gate


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
    unit_source = "migration_reports"
    fallback_units = expanded_variant_units or phase3_units
    fallback_needed = bool(fallback_units) and (
        not units or (not _all_units_present(fallback_units, units) and _reports_lack_unit_identities(inventory, manifest, gate))
    )
    if fallback_needed:
        units = fallback_units
        inventory_source = "phase_3_expanded_variant_contract_fallback" if expanded_variant_units else "phase_3_contract_fallback"
        unit_source = "Phase 3 expanded variant contract" if expanded_variant_units else "Phase 3 contract"
        warnings.append(
            "Using Phase 3 custom-op inventory fallback because migration_reports inventory/manifest/final gate are missing, stale, or incomplete; fallback rows are scope only, not final evidence."
        )
    if fallback_needed:
        total_count = len(units)
    elif total_count is None and units:
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
    ledger_target_units = expanded_variant_units or phase3_units or _operator_unit_identities(inventory, manifest, gate)
    ledger = custom_op_final_gate_unit_ledger(
        _object_dict(gate) or {},
        target_units=ledger_target_units,
        project_root=project_path,
    )

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
        f"- Unit Source: {unit_source}",
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
        "## Strict Per-Unit Progress Ledger",
        *_strict_progress_lines(ledger),
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
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = project_path / candidate
        try:
            resolved = candidate.resolve()
            resolved.relative_to(project_path)
        except (OSError, ValueError):
            return _safe_default_reports_dir(project_path)
        return resolved
    return _safe_default_reports_dir(project_path)


def _safe_default_reports_dir(project_path: Path) -> Path:
    for name in ("migration_reports", ".seam_migration_reports"):
        candidate = project_path / name
        if candidate.is_symlink():
            try:
                _ = candidate.resolve().relative_to(project_path)
            except (OSError, ValueError):
                continue
        return candidate.resolve()
    return (project_path / ".seam_migration_reports_safe").resolve()


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


def _reports_lack_unit_identities(inventory: object, manifest: object, gate: object) -> bool:
    saw_entry = False
    for data in (inventory, manifest, gate):
        for entry in _candidate_entries(data):
            if not isinstance(entry, dict):
                continue
            saw_entry = True
            value = cast(dict[str, object], entry).get("unit_identity")
            if isinstance(value, str) and value.strip():
                return False
    return saw_entry


def _operator_unit_identities(inventory: object, manifest: object, gate: object) -> list[str]:
    units: list[str] = []
    for data in (inventory, manifest, gate):
        for entry in _candidate_entries(data):
            if not isinstance(entry, dict):
                continue
            entry_dict = cast(dict[str, object], entry)
            value = entry_dict.get("unit_identity") or entry_dict.get("name") or entry_dict.get("operator") or entry_dict.get("op_name")
            if isinstance(value, str) and value.strip() and value.strip() not in units:
                units.append(value.strip())
            if len(units) >= 500:
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
    return units


def _expanded_variant_units_from_contract(contract: dict[str, object]) -> list[str]:
    overlay = expanded_variant_contract_from_contract(contract)
    inventory = overlay.get("expanded_variant_inventory")
    if not isinstance(inventory, dict):
        return []
    return _string_list(cast(dict[str, object], inventory).get("unit_identities"))


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


def _strict_progress_lines(ledger: dict[str, object]) -> list[str]:
    lines = [
        f"Total Target Units: {ledger.get('total_count', 0)}",
        f"Strict Pass Units: {ledger.get('strict_pass_count', 0)}",
        f"Remaining Units: {ledger.get('remaining_count', 0)}",
    ]
    strict_units = ledger.get("strict_pass_units")
    if isinstance(strict_units, list) and strict_units:
        lines.append("Strict Pass Unit Identities: " + ", ".join(str(unit) for unit in strict_units[:50]))
    remaining_units = ledger.get("remaining_units")
    if isinstance(remaining_units, list) and remaining_units:
        lines.append("Remaining Unit Identities: " + ", ".join(str(unit) for unit in remaining_units[:50]))
    raw_units = ledger.get("units")
    detail_count = 0
    if isinstance(raw_units, list):
        for item in raw_units:
            if not isinstance(item, dict) or item.get("status") == "strict_pass":
                continue
            unit = str(item.get("unit_identity") or "").strip()
            missing = item.get("missing_evidence")
            missing_items = [str(value) for value in missing[:6]] if isinstance(missing, list) else []
            detail = "; ".join(missing_items) if missing_items else "strict evidence incomplete"
            lines.append(f"Remaining Detail [{unit}]: {detail}")
            detail_count += 1
            if detail_count >= 20:
                lines.append("Remaining Detail: truncated; inspect final-gate/unit ledger for the full list")
                break
    return lines


CANONICAL_CUSTOM_OP_REPORT_NAMES: tuple[str, ...] = (
    "operator_inventory.json",
    "migration_manifest.json",
    "preflight.json",
    "baseline.json",
    "runtime_coverage.json",
    "performance.json",
    "build.json",
    "implementation_resolution.json",
    "evidence_validation.json",
    "summary.json",
    "custom_op_final_gate.json",
)


def scaffold_or_refresh_custom_op_canonical_reports(
    *,
    project_dir: str,
    phase3_contract: dict[str, object] | None = None,
    phase1_analysis: dict[str, object] | None = None,
) -> dict[str, object]:
    """Create or refresh fail-closed canonical custom-op reports from authoritative scope."""
    project_path = Path(project_dir).resolve()
    contract = dict(phase3_contract or {})
    phase1 = dict(phase1_analysis or {})
    reports_dir = _reports_dir(project_path, contract)
    reports_dir.mkdir(parents=True, exist_ok=True)
    units, source = _canonical_scaffold_units(contract, phase1)
    if not units:
        return {"reports_dir": str(reports_dir), "unit_count": 0, "unit_source": source, "written_reports": []}
    rows = [_canonical_fail_closed_row(unit, source) for unit in units]
    now_message = "fail-closed scaffold only; run Phase 5 validation to replace with measured current-run evidence"

    report_payloads: dict[str, dict[str, object]] = {
        "operator_inventory.json": {
            "status": "INCOMPLETE",
            "unit_source": source,
            "inventory_count": len(rows),
            "rows": rows,
            "message": now_message,
        },
        "migration_manifest.json": {
            "status": "INCOMPLETE",
            "unit_source": source,
            "manifest_entries": len(rows),
            "closed_pass_entries": 0,
            "remaining_entries": len(rows),
            "rows": rows,
            "message": now_message,
        },
        "preflight.json": _canonical_stage_report("preflight", rows, source),
        "baseline.json": _canonical_stage_report("baseline", rows, source),
        "runtime_coverage.json": _canonical_stage_report("runtime_coverage", rows, source),
        "performance.json": _canonical_stage_report("performance", rows, source),
        "build.json": _canonical_stage_report("build", rows, source),
        "implementation_resolution.json": _canonical_stage_report("implementation_resolution", rows, source),
        "evidence_validation.json": _canonical_stage_report("evidence_validation", rows, source),
        "summary.json": {
            "status": "INCOMPLETE",
            "full_migration_status": "INCOMPLETE",
            "unit_source": source,
            "inventory_count": len(rows),
            "manifest_entries": len(rows),
            "closed_pass_entries": 0,
            "remaining_entries": len(rows),
            "blocking_gaps": ["canonical reports are scaffolded fail-closed until measured evidence closes every row"],
            "message": now_message,
        },
        "custom_op_final_gate.json": {
            "status": "INCOMPLETE",
            "full_migration_status": "INCOMPLETE",
            "unit_source": source,
            "inventory_count": len(rows),
            "manifest_entries": len(rows),
            "closed_pass_entries": 0,
            "remaining_entries": len(rows),
            "strict_per_unit_ledger": rows,
            "blocking_gaps": ["strict custom-op final gate remains fail-closed until real OPP/runtime/parity/performance evidence is present for every row"],
            "message": now_message,
        },
    }

    written: list[str] = []
    for name in CANONICAL_CUSTOM_OP_REPORT_NAMES:
        path = reports_dir / name
        existing = _read_existing_report_object(path)
        payload = report_payloads[name]
        if existing is not None:
            existing_rows = existing.get("rows") or existing.get("strict_per_unit_ledger")
            existing_units = _rows_unit_identities(existing_rows)
            target_units = [str(row["unit_identity"]) for row in rows]
            if target_units and existing_units and all(unit in existing_units for unit in target_units):
                if name == "custom_op_final_gate.json" and _report_claims_full_pass(existing):
                    validation = validate_custom_op_final_gate(existing, project_root=project_path)
                    if validation.get("passed") is not True:
                        payload["refresh_reason"] = "existing full-pass final gate did not validate; replaced with fail-closed scaffold"
                        path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
                        written.append(str(path))
                    continue
                continue
            payload = _merge_fail_closed_scaffold(existing, payload, rows, source)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
        written.append(str(path))
    return {"reports_dir": str(reports_dir), "unit_count": len(rows), "unit_source": source, "written_reports": written}


def _canonical_scaffold_units(contract: dict[str, object], phase1_analysis: dict[str, object]) -> tuple[list[str], str]:
    expanded_units = _expanded_variant_units_from_contract(contract)
    if expanded_units:
        return expanded_units, "phase_3_expanded_variant_contract"
    phase3_units = _phase3_contract_operator_units(contract)
    if phase3_units:
        return phase3_units, "phase_3_contract"
    phase1_units = _phase1_analysis_operator_units(phase1_analysis)
    if phase1_units:
        return phase1_units, "phase_1_analysis"
    return [], "empty_scope"


def _phase1_analysis_operator_units(phase1_analysis: dict[str, object]) -> list[str]:
    surface = phase1_analysis.get("custom_op_surface")
    if not isinstance(surface, dict):
        return []
    surface_dict = cast(dict[str, object], surface)
    for key in ("expanded_operator_variants", "fine_grained_operator_units", "operator_units", "discovered_operator_names", "native_operator_symbols"):
        value = surface_dict.get(key)
        if isinstance(value, list):
            return _string_list(value)
    return []


def _canonical_fail_closed_row(unit_identity: str, source: str) -> dict[str, object]:
    return {
        "unit_identity": unit_identity,
        "unit_source": source,
        "status": "INCOMPLETE",
        "migration_status": "INCOMPLETE",
        "strict_pass": False,
        "missing_evidence": [
            "opp_custom_op_artifact_evidence",
            "adapter_evidence",
            "parity_evidence",
            "integration_e2e_evidence",
            "same_run_runtime_coverage",
            "performance_evidence",
            "no_fallback_no_zero_call_no_builtin_contamination",
        ],
        "scaffold_scope_only": True,
    }


def _canonical_stage_report(stage: str, rows: list[dict[str, object]], source: str) -> dict[str, object]:
    return {
        "status": "INCOMPLETE",
        "stage": stage,
        "unit_source": source,
        "total_count": len(rows),
        "closed_pass_entries": 0,
        "remaining_entries": len(rows),
        "rows": rows,
        "message": "fail-closed scaffold only; no pass evidence has been fabricated",
    }


def _read_existing_report_object(path: Path) -> dict[str, object] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return cast(dict[str, object], data) if isinstance(data, dict) else None


def _report_claims_full_pass(report: dict[str, object]) -> bool:
    for key in ("status", "full_migration_status", "result", "outcome"):
        value = report.get(key)
        if isinstance(value, str) and value.strip().upper() in {"PASS", "PASSED", "FULL_PASS", "SUCCESS"}:
            return True
    return False


def _merge_fail_closed_scaffold(
    existing: dict[str, object],
    scaffold: dict[str, object],
    rows: list[dict[str, object]],
    source: str,
) -> dict[str, object]:
    existing_rows = existing.get("rows") or existing.get("strict_per_unit_ledger")
    existing_units = _rows_unit_identities(existing_rows)
    target_units = [str(row["unit_identity"]) for row in rows]
    if not existing_units:
        merged = dict(scaffold)
        merged["refresh_reason"] = "existing report missing authoritative unit_identity rows or collapsed Phase 3 scope"
        return merged
    if not all(unit in existing_units for unit in target_units):
        existing_row_items = cast(list[object], existing_rows) if isinstance(existing_rows, list) else []
        rows_by_unit: dict[str, dict[str, object]] = {}
        for row in existing_row_items:
            if not isinstance(row, dict):
                continue
            row_dict = cast(dict[str, object], row)
            unit = row_dict.get("unit_identity")
            if isinstance(unit, str) and unit.strip():
                rows_by_unit[unit.strip()] = row_dict
        merged_rows = [rows_by_unit.get(str(row["unit_identity"]), row) for row in rows]
        merged = dict(scaffold)
        if "strict_per_unit_ledger" in merged:
            merged["strict_per_unit_ledger"] = merged_rows
        else:
            merged["rows"] = merged_rows
        merged["refresh_reason"] = "existing report preserved authoritative unit_identity rows and appended missing Phase 3 scope rows fail-closed"
        return merged
    merged = dict(existing)
    merged.setdefault("unit_source", source)
    return merged


def _rows_unit_identities(rows_obj: object) -> list[str]:
    units: list[str] = []
    if not isinstance(rows_obj, list):
        return units
    for item in rows_obj:
        if not isinstance(item, dict):
            continue
        unit = cast(dict[str, object], item).get("unit_identity")
        if isinstance(unit, str) and unit.strip() and unit.strip() not in units:
            units.append(unit.strip())
    return units
