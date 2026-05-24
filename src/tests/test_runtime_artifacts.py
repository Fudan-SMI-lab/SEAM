from pathlib import Path
from core.runtime_artifacts import write_operator_repair_context_artifact


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
