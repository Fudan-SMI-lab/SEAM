from pathlib import Path
import json
import os

from core.runtime_artifacts import scaffold_or_refresh_custom_op_canonical_reports, write_operator_repair_context_artifact


def test_operator_repair_context_uses_phase3_inventory_when_reports_missing(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    artifact_dir = tmp_path / ".sm-artifacts" / "run"
    phase3_contract: dict[str, object] = {
        "entry_script_path": str(project_dir / "validate_custom_ops_full.py"),
        "entry_script_kind": "custom_op_full_validation",
        "reports_dir": str(project_dir / "migration_reports"),
        "operator_inventory_schema": {
            "fine_grained_operator_units": [
                "sampling:gather_points(points,idx)",
                "interpolate:three_nn(unknowns,knowns)",
            ]
        },
        "required_checks": ["strict_ascend_c_cann_opp_artifacts"],
    }

    context_path = write_operator_repair_context_artifact(
        artifact_dir=str(artifact_dir),
        project_dir=str(project_dir),
        entry_script=f"{project_dir}/.venv/bin/python {project_dir}/validate_custom_ops_full.py",
        phase3_contract=phase3_contract,
    )

    text = Path(context_path).read_text(encoding="utf-8")
    assert "Unit Count Listed Here: 2" in text
    assert "Inventory Source: phase_3_contract_fallback" in text
    assert "sampling:gather_points(points,idx)" in text
    assert "interpolate:three_nn(unknowns,knowns)" in text
    assert "fallback rows are scope only, not final evidence" in text
    assert "strict_ascend_c_cann_opp_artifacts" in text
    assert "Strict Per-Unit Progress Ledger" in text
    assert "Strict Pass Units: 0" in text
    assert "Remaining Units: 2" in text
    assert "Remaining Unit Identities: sampling:gather_points(points,idx), interpolate:three_nn(unknowns,knowns)" in text


def test_operator_repair_context_prefers_report_inventory_over_phase3_fallback(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    reports_dir = project_dir / "migration_reports"
    reports_dir.mkdir(parents=True)
    _ = (reports_dir / "operator_inventory.json").write_text(
        '{"rows": [{"unit_identity": "report:unit", "status": "discovered"}]}',
        encoding="utf-8",
    )
    _ = (reports_dir / "migration_manifest.json").write_text('{"rows": []}', encoding="utf-8")
    _ = (reports_dir / "custom_op_final_gate.json").write_text('{"inventory_count": 1}', encoding="utf-8")
    artifact_dir = tmp_path / ".sm-artifacts" / "run"
    phase3_contract: dict[str, object] = {
        "entry_script_kind": "custom_op_full_validation",
        "reports_dir": str(reports_dir),
        "operator_inventory_schema": {
            "fine_grained_operator_units": ["phase3:unit"]
        },
    }

    context_path = write_operator_repair_context_artifact(
        artifact_dir=str(artifact_dir),
        project_dir=str(project_dir),
        entry_script="python validate_custom_ops_full.py",
        phase3_contract=phase3_contract,
    )

    text = Path(context_path).read_text(encoding="utf-8")
    assert "Unit Count Listed Here: 1" in text
    assert "Inventory Source: migration_reports" in text
    assert "name=report:unit" in text
    assert "Phase3 Unit 1: phase3:unit" in text
    assert "fallback rows are scope only" not in text
    assert "continue repair from the Phase 3 operator scope" in text


def test_operator_repair_context_ignores_external_reports_dir(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    project_reports = project_dir / "migration_reports"
    external_reports = tmp_path / "external_reports"
    project_reports.mkdir(parents=True)
    external_reports.mkdir()
    _ = (project_reports / "operator_inventory.json").write_text(
        '{"rows": [{"unit_identity": "project:unit", "status": "discovered"}]}',
        encoding="utf-8",
    )
    _ = (external_reports / "operator_inventory.json").write_text(
        '{"rows": [{"unit_identity": "external:unit", "status": "discovered"}]}',
        encoding="utf-8",
    )
    phase3_contract: dict[str, object] = {
        "entry_script_kind": "custom_op_full_validation",
        "reports_dir": str(external_reports),
        "operator_inventory_schema": {"fine_grained_operator_units": ["phase3:unit"]},
    }

    context_path = write_operator_repair_context_artifact(
        artifact_dir=str(tmp_path / ".sm-artifacts" / "run"),
        project_dir=str(project_dir),
        entry_script="python validate.py",
        phase3_contract=phase3_contract,
    )

    text = Path(context_path).read_text(encoding="utf-8")
    assert "project:unit" in text
    assert "external:unit" not in text


def test_operator_repair_context_lists_expanded_variants_as_missing_report_scope(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    artifact_dir = tmp_path / ".sm-artifacts" / "run"
    phase3_contract: dict[str, object] = {
        "entry_script_path": str(project_dir / "validate_custom_ops_full.py"),
        "entry_script_kind": "custom_op_full_validation",
        "reports_dir": str(project_dir / "migration_reports"),
        "expanded_variant_inventory": {
            "variant_axes_detected": True,
            "unit_identities": [
                "generic_kernel:shape=small:precision=fp16",
                "generic_kernel:shape=large:precision=fp16",
                "generic_kernel:shape=small:precision=fp32",
            ],
            "expanded_operator_instances_count": 3,
        },
    }

    context_path = write_operator_repair_context_artifact(
        artifact_dir=str(artifact_dir),
        project_dir=str(project_dir),
        entry_script="python validate_custom_ops_full.py",
        phase3_contract=phase3_contract,
    )

    text = Path(context_path).read_text(encoding="utf-8")
    assert "Inventory Source: phase_3_expanded_variant_contract_fallback" in text
    assert "Total Count: 3" in text
    assert "Unit Count Listed Here: 3" in text
    assert "Expanded Variant Count: 3" in text
    assert "Expanded Variant Units Listed Here: 3" in text
    assert "Variant 1: generic_kernel:shape=small:precision=fp16" in text
    assert "Unit 3: generic_kernel:shape=small:precision=fp32" in text



def test_operator_repair_context_keeps_full_expanded_variant_scope_and_falls_back_from_collapsed_reports(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    reports_dir = project_dir / "migration_reports"
    reports_dir.mkdir(parents=True)
    (reports_dir / "operator_inventory.json").write_text(
        json.dumps({"rows": [{"name": "collapsed_kernel", "status": "INCOMPLETE"}], "total_count": 1}),
        encoding="utf-8",
    )
    units = [f"variant_route:op={i // 120}:dtype={'double' if i >= 120 else 'float'}:variant={i}" for i in range(240)]
    phase3_contract: dict[str, object] = {
        "entry_script_kind": "custom_op_full_validation",
        "reports_dir": str(reports_dir),
        "expanded_variant_inventory": {
            "variant_axes_detected": True,
            "unit_identities": units,
            "expanded_operator_instances_count": 240,
        },
    }

    context_path = write_operator_repair_context_artifact(
        artifact_dir=str(tmp_path / ".sm-artifacts" / "run"),
        project_dir=str(project_dir),
        entry_script="python validate_custom_ops_full.py",
        phase3_contract=phase3_contract,
    )

    text = Path(context_path).read_text(encoding="utf-8")
    assert "Inventory Source: phase_3_expanded_variant_contract_fallback" in text
    assert "Unit Source: Phase 3 expanded variant contract" in text
    assert "Total Count: 240" in text
    assert "Unit Count Listed Here: 240" in text
    assert "Expanded Variant Units Listed Here: 240" in text
    assert units[0] in text
    assert units[239] in text
    assert "fallback rows are scope only, not final evidence" in text


def test_scaffold_or_refresh_custom_op_canonical_reports_fail_closed_for_full_variant_scope(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    units = [f"variant_route:dtype={'double' if i >= 120 else 'float'}:variant={i}" for i in range(240)]
    phase3_contract: dict[str, object] = {
        "entry_script_kind": "custom_op_full_validation",
        "reports_dir": str(project_dir / "migration_reports"),
        "expanded_variant_inventory": {
            "variant_axes_detected": True,
            "unit_identities": units,
            "expanded_operator_instances_count": 240,
        },
    }

    result = scaffold_or_refresh_custom_op_canonical_reports(project_dir=str(project_dir), phase3_contract=phase3_contract)

    assert result["unit_count"] == 240
    reports_dir = project_dir / "migration_reports"
    expected_names = {
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
    }
    assert {path.name for path in reports_dir.iterdir()} == expected_names
    gate = json.loads((reports_dir / "custom_op_final_gate.json").read_text(encoding="utf-8"))
    assert gate["full_migration_status"] == "INCOMPLETE"
    assert gate["inventory_count"] == 240
    assert gate["closed_pass_entries"] == 0
    assert gate["remaining_entries"] == 240
    assert len(gate["strict_per_unit_ledger"]) == 240
    assert gate["strict_per_unit_ledger"][239]["unit_identity"] == units[239]
    assert gate["strict_per_unit_ledger"][239]["strict_pass"] is False


def test_scaffold_refresh_replaces_collapsed_rows_with_phase3_identities(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    reports_dir = project_dir / "migration_reports"
    reports_dir.mkdir(parents=True)
    (reports_dir / "runtime_coverage.json").write_text(
        json.dumps({"status": "INCOMPLETE", "rows": [{"name": "collapsed"}]}),
        encoding="utf-8",
    )
    phase3_contract: dict[str, object] = {
        "entry_script_kind": "custom_op_full_validation",
        "reports_dir": str(reports_dir),
        "operator_inventory_schema": {"fine_grained_operator_units": ["unit:a", "unit:b"]},
    }

    scaffold_or_refresh_custom_op_canonical_reports(project_dir=str(project_dir), phase3_contract=phase3_contract)

    coverage = json.loads((reports_dir / "runtime_coverage.json").read_text(encoding="utf-8"))
    assert coverage["unit_source"] == "phase_3_contract"
    assert coverage["refresh_reason"] == "existing report missing authoritative unit_identity rows or collapsed Phase 3 scope"
    assert [row["unit_identity"] for row in coverage["rows"]] == ["unit:a", "unit:b"]
    assert coverage["closed_pass_entries"] == 0


def test_scaffold_refresh_preserves_existing_unit_rows_and_appends_missing_variants(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    reports_dir = project_dir / "migration_reports"
    reports_dir.mkdir(parents=True)
    (reports_dir / "runtime_coverage.json").write_text(
        json.dumps(
            {
                "status": "INCOMPLETE",
                "rows": [
                    {
                        "unit_identity": "unit:a",
                        "status": "PASS",
                        "same_run_runtime_coverage": {"same_run": True},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    phase3_contract: dict[str, object] = {
        "entry_script_kind": "custom_op_full_validation",
        "reports_dir": str(reports_dir),
        "operator_inventory_schema": {"fine_grained_operator_units": ["unit:a", "unit:b"]},
    }

    scaffold_or_refresh_custom_op_canonical_reports(project_dir=str(project_dir), phase3_contract=phase3_contract)

    coverage = json.loads((reports_dir / "runtime_coverage.json").read_text(encoding="utf-8"))
    assert coverage["refresh_reason"] == "existing report preserved authoritative unit_identity rows and appended missing Phase 3 scope rows fail-closed"
    assert [row["unit_identity"] for row in coverage["rows"]] == ["unit:a", "unit:b"]
    assert coverage["rows"][0]["same_run_runtime_coverage"] == {"same_run": True}
    assert coverage["rows"][1]["status"] == "INCOMPLETE"


def test_scaffold_refresh_does_not_touch_complete_existing_unit_reports(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    reports_dir = project_dir / "migration_reports"
    reports_dir.mkdir(parents=True)
    coverage_path = reports_dir / "runtime_coverage.json"
    coverage_payload = {
        "status": "PASS",
        "rows": [
            {"unit_identity": "unit:a", "same_run_runtime_coverage": {"same_run": True}},
            {"unit_identity": "unit:b", "same_run_runtime_coverage": {"same_run": True}},
        ],
    }
    coverage_path.write_text(json.dumps(coverage_payload), encoding="utf-8")
    os.utime(coverage_path, (1000.0, 1000.0))
    phase3_contract: dict[str, object] = {
        "entry_script_kind": "custom_op_full_validation",
        "reports_dir": str(reports_dir),
        "operator_inventory_schema": {"fine_grained_operator_units": ["unit:a", "unit:b"]},
    }

    scaffold_or_refresh_custom_op_canonical_reports(project_dir=str(project_dir), phase3_contract=phase3_contract)

    assert coverage_path.stat().st_mtime == 1000.0
    assert json.loads(coverage_path.read_text(encoding="utf-8")) == coverage_payload


def test_scaffold_refresh_replaces_invalid_full_pass_final_gate(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    reports_dir = project_dir / "migration_reports"
    reports_dir.mkdir(parents=True)
    gate_path = reports_dir / "custom_op_final_gate.json"
    gate_path.write_text(
        json.dumps(
            {
                "full_migration_status": "FULL_PASS",
                "inventory_count": 1,
                "manifest_entries": 1,
                "closed_pass_entries": 1,
                "remaining_entries": 0,
                "strict_per_unit_ledger": [{"unit_identity": "unit:a", "strict_pass": True}],
            }
        ),
        encoding="utf-8",
    )
    phase3_contract: dict[str, object] = {
        "entry_script_kind": "custom_op_full_validation",
        "reports_dir": str(reports_dir),
        "operator_inventory_schema": {"fine_grained_operator_units": ["unit:a"]},
    }

    scaffold_or_refresh_custom_op_canonical_reports(project_dir=str(project_dir), phase3_contract=phase3_contract)

    gate = json.loads(gate_path.read_text(encoding="utf-8"))
    assert gate["full_migration_status"] == "INCOMPLETE"
    assert gate["refresh_reason"] == "existing full-pass final gate did not validate; replaced with fail-closed scaffold"


def test_scaffold_uses_safe_fallback_when_default_reports_dir_is_external_symlink(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    outside_dir = tmp_path / "outside_reports"
    outside_dir.mkdir()
    (project_dir / "migration_reports").symlink_to(outside_dir, target_is_directory=True)
    phase3_contract: dict[str, object] = {
        "entry_script_kind": "custom_op_full_validation",
        "operator_inventory_schema": {"fine_grained_operator_units": ["unit:a"]},
    }

    result = scaffold_or_refresh_custom_op_canonical_reports(project_dir=str(project_dir), phase3_contract=phase3_contract)

    reports_dir = Path(str(result["reports_dir"]))
    assert reports_dir == (project_dir / ".seam_migration_reports").resolve()
    assert (reports_dir / "custom_op_final_gate.json").is_file()
    assert not (outside_dir / "custom_op_final_gate.json").exists()
