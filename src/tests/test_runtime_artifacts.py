import json
from pathlib import Path
from core.runtime_artifacts import CANONICAL_CUSTOM_OP_REPORT_NAMES, scaffold_or_refresh_custom_op_canonical_reports, write_operator_repair_context_artifact


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


def test_scaffold_or_refresh_custom_op_canonical_reports_fail_closed_for_full_variant_scope(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    units = [f"variant_route:dtype={'double' if i >= 2 else 'float'}:variant={i}" for i in range(4)]
    phase3_contract: dict[str, object] = {
        "entry_script_kind": "custom_op_full_validation",
        "reports_dir": str(project_dir / "migration_reports"),
        "expanded_variant_inventory": {
            "variant_axes_detected": True,
            "unit_identities": units,
            "expanded_operator_instances_count": 4,
        },
    }

    result = scaffold_or_refresh_custom_op_canonical_reports(project_dir=str(project_dir), phase3_contract=phase3_contract)

    reports_dir = project_dir / "migration_reports"
    assert result["unit_count"] == 4
    assert {path.name for path in reports_dir.iterdir()} == set(CANONICAL_CUSTOM_OP_REPORT_NAMES)
    gate = json.loads((reports_dir / "custom_op_final_gate.json").read_text(encoding="utf-8"))
    assert gate["full_migration_status"] == "INCOMPLETE"
    assert gate["closed_pass_entries"] == 0
    assert gate["remaining_entries"] == 4
    assert [row["unit_identity"] for row in gate["strict_per_unit_ledger"]] == units
    assert all(row["strict_pass"] is False for row in gate["strict_per_unit_ledger"])


def test_scaffold_refresh_replaces_invalid_full_pass_final_gate_fail_closed(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    reports_dir = project_dir / "migration_reports"
    reports_dir.mkdir(parents=True)
    _ = (reports_dir / "custom_op_final_gate.json").write_text(
        json.dumps({"status": "FULL_PASS", "rows": [{"unit_identity": "op_a"}]}),
        encoding="utf-8",
    )
    phase3_contract: dict[str, object] = {
        "entry_script_kind": "custom_op_full_validation",
        "reports_dir": str(reports_dir),
        "operator_inventory_schema": {"fine_grained_operator_units": ["op_a"]},
    }

    result = scaffold_or_refresh_custom_op_canonical_reports(project_dir=str(project_dir), phase3_contract=phase3_contract)

    gate = json.loads((reports_dir / "custom_op_final_gate.json").read_text(encoding="utf-8"))
    written_reports = result["written_reports"]
    assert isinstance(written_reports, list)
    assert str(reports_dir / "custom_op_final_gate.json") in written_reports
    assert gate["full_migration_status"] == "INCOMPLETE"
    assert gate["strict_per_unit_ledger"][0]["unit_identity"] == "op_a"
    assert gate["strict_per_unit_ledger"][0]["strict_pass"] is False


def test_scaffold_phase1_expanded_variants_extracts_unit_identity_from_rows(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    phase1_analysis: dict[str, object] = {
        "custom_op_surface": {
            "expanded_operator_variants": [
                {"unit_identity": "op:variant=a", "family": "op"},
                {"unit_identity": "op:variant=b", "family": "op"},
            ]
        }
    }

    result = scaffold_or_refresh_custom_op_canonical_reports(
        project_dir=str(project_dir),
        phase1_analysis=phase1_analysis,
    )

    gate = json.loads((project_dir / "migration_reports" / "custom_op_final_gate.json").read_text(encoding="utf-8"))
    assert result["unit_source"] == "phase_1_analysis"
    assert [row["unit_identity"] for row in gate["strict_per_unit_ledger"]] == ["op:variant=a", "op:variant=b"]
    assert not any(str(row["unit_identity"]).startswith("{") for row in gate["strict_per_unit_ledger"])


def test_scaffold_rejects_symlinked_migration_reports(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    outside_dir = tmp_path / "outside_reports"
    outside_dir.mkdir()
    (project_dir / "migration_reports").symlink_to(outside_dir, target_is_directory=True)
    phase3_contract: dict[str, object] = {
        "entry_script_kind": "custom_op_full_validation",
        "operator_inventory_schema": {"fine_grained_operator_units": ["op_a"]},
    }

    try:
        scaffold_or_refresh_custom_op_canonical_reports(project_dir=str(project_dir), phase3_contract=phase3_contract)
    except ValueError as exc:
        assert "migration_reports must not be a symlink" in str(exc)
    else:
        raise AssertionError("symlinked migration_reports must be rejected")
    assert not (outside_dir / "custom_op_final_gate.json").exists()


def test_scaffold_falls_back_from_external_reports_dir_to_project_reports(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    external_dir = tmp_path / "external_reports"
    external_dir.mkdir()
    phase3_contract: dict[str, object] = {
        "entry_script_kind": "custom_op_full_validation",
        "reports_dir": str(external_dir),
        "operator_inventory_schema": {"fine_grained_operator_units": ["op_a"]},
    }

    result = scaffold_or_refresh_custom_op_canonical_reports(project_dir=str(project_dir), phase3_contract=phase3_contract)

    project_gate = project_dir / "migration_reports" / "custom_op_final_gate.json"
    assert result["reports_dir"] == str(project_dir / "migration_reports")
    assert project_gate.exists()
    assert not (external_dir / "custom_op_final_gate.json").exists()
