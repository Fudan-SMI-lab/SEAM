import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TypeAlias, cast

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.validator_engine import ValidationDict, ValidationResult, ValidatorEngine
from validators.validate_entry_script import validate as validate_entry_script
from validators.validate_entry_static import validate as validate_entry_static
from validators.validate_env_detect import validate as validate_env_detect
from validators.validate_project_analysis import validate as validate_project_analysis
from validators.validate_reports import validate as validate_reports
from validators.validate_rule_migration import validate as validate_rule_migration
from validators.validate_validation_final import (
    validate as validate_validation_final,
    validate_custom_op_final_gate,
)
from validators.validate_venv import validate as validate_venv


ValidatorCallable: TypeAlias = Callable[[dict[str, object]], ValidationDict]
ValidatorCase: TypeAlias = tuple[ValidatorCallable, dict[str, object]]


VALID_CASES: list[ValidatorCase] = [
    (
        validate_env_detect,
        {"platform": "npu", "npu_detected": True, "python_version": "3.10", "cann_version": "8.0.RC", "ascendc_available": True, "driver_version": "driver-1"},
    ),
    (
        validate_project_analysis,
        {
            "project_dir": "/tmp/project",
            "dependencies": ["torch"],
            "cuda_detected": True,
            "entry_script": "train.py",
        },
    ),
    (
        validate_venv,
        {
            "venv_path": "/tmp/.venv",
            "python_path": "/tmp/.venv/bin/python",
            "installed_packages": ["torch"],
        },
    ),
    (
        validate_entry_script,
        {"entry_script_path": "train.py", "run_command": "python train.py"},
    ),
    (
        validate_entry_static,
        {
            "validation_passed": True,
            "issues": [],
            "fix_plan": "No issues found. Script is headless-compliant.",
        },
    ),
    (
        validate_rule_migration,
        {"files_migrated": 2, "files_skipped": 1, "replacement_counts": {"cuda_method": 4}},
    ),
    (
        validate_validation_final,
        {"success": True, "iteration_count": 0, "errors": []},
    ),
    (
        validate_reports,
        {
            "report_paths": ["report.json"],
            "migration_summary": {"files_migrated": 2, "files_skipped": 1},
        },
    ),
]


INVALID_CASES: list[ValidatorCase] = [
    (validate_env_detect, {"platform": "cpu"}),
    (validate_project_analysis, {"project_dir": "", "dependencies": "torch"}),
    (validate_venv, {"venv_path": 1, "python_path": "", "installed_packages": "torch"}),
    (validate_entry_script, {"entry_script_path": "", "run_command": None}),
    (validate_entry_static, {"validation_passed": True, "issues": ["contradiction"], "fix_plan": ""}),
    (validate_rule_migration, {"files_migrated": -1, "files_skipped": "0", "replacement_counts": []}),
    (validate_validation_final, {"success": "yes", "iteration_count": -1, "errors": {}}),
    (validate_reports, {"report_paths": "report.json", "migration_summary": []}),
]


def _valid_custom_op_contract(
    script_path: str = "/tmp/project/validate_custom_ops_full.py",
    project_dir: str | None = None,
) -> dict[str, object]:
    if project_dir is None:
        project_dir = str(Path(script_path).parent)
    reports_dir = f"{project_dir}/migration_reports"
    return {
        "entry_script_path": script_path,
        "run_command": f"{project_dir}/.venv/bin/python {script_path}",
        "entry_script_kind": "custom_op_full_validation",
        "reports_dir": reports_dir,
        "operator_discovery_sources": [
            "source",
            "bindings",
            "wrappers",
            "autograd",
            "aliases",
            "launch",
            "setup",
            "tests",
        ],
        "operator_inventory_schema": {
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
        "required_report_paths": [
            f"{reports_dir}/operator_inventory.json",
            f"{reports_dir}/migration_manifest.json",
            f"{reports_dir}/preflight.json",
            f"{reports_dir}/baseline.json",
            f"{reports_dir}/runtime_coverage.json",
            f"{reports_dir}/performance.json",
            f"{reports_dir}/build.json",
            f"{reports_dir}/implementation_resolution.json",
            f"{reports_dir}/custom_op_final_gate.json",
            f"{reports_dir}/evidence_validation.json",
            f"{reports_dir}/summary.json",
        ],
        "required_checks": [
            "inventory_manifest_equality",
            "closed_pass_count_equals_manifest_entries",
            "remaining_entries_zero",
            "full_migration_status_full_pass",
            "fine_grained_operator_unit_inventory",
            "kernel_launch_site_inventory",
            "public_entry_mapping",
            "inventory_granularity_fine",
            "per_entry_opp_custom_op_artifact_evidence",
            "per_entry_adapter_evidence",
            "per_entry_parity_evidence",
            "integration_e2e_evidence",
            "same_run_runtime_coverage",
            "performance_evidence",
            "complete_performance_report",
            "overall_speedup_report",
            "strict_ascend_c_cann_opp_artifacts",
            "op_host_op_kernel_source_evidence",
            "cann_opp_build_install_provenance",
            "generated_opp_package_artifacts",
            "reject_npuextension_aten_only_as_opp_evidence",
            "reject_non_opp_producer_evidence",
            "project_root_artifact_existence",
            "final_chinese_per_row_table_parity",
            "no_fallback_no_zero_call_no_builtin_contamination",
            "native_operator_symbol_inventory",
        ],
        "validation_obligations": [
            "project_local_artifact",
            "strict_opp_artifact",
            "op_host_op_kernel_source",
            "cann_opp_build_install",
            "generated_opp_package_artifacts",
            "reject_npuextension_aten_only",
            "reject_non_opp_producer_evidence",
            "project_root_artifact_existence",
            "runtime_project_api",
            "numeric_performance",
            "complete_speedup_report",
            "overall_speedup_report",
            "final_chinese_per_row_table",
            "no_fallback",
        ],
        "phase5_entry_script_revision_allowed": True,
    }


def _valid_custom_op_final_gate() -> dict[str, object]:
    return {
        "inventory_count": 1,
        "manifest_entries": 1,
        "closed_pass_entries": 1,
        "remaining_entries": 0,
        "full_migration_status": "FULL_PASS",
        "project_e2e_passed": True,
        "report_parity_passed": True,
        "performance_report": {
            "complete": True,
            "unit_count": 1,
            "path": "migration_reports/performance.json",
            "project_api_invoked": True,
            "baseline_device": "cuda",
            "custom_device": "npu",
            "overall_baseline_seconds": 0.05,
            "overall_custom_seconds": 0.04,
            "overall_speedup_vs_baseline": 1.25,
            "overall_project_api_invoked": True,
            "overall_all_units_replaced": True,
            "overall_baseline_device": "cuda",
            "overall_custom_device": "npu",
            "entries": [
                {
                    "unit_identity": "ScalarFwd2D",
                    "baseline_seconds": 0.02,
                    "custom_seconds": 0.01,
                    "speedup_vs_baseline": 2.0,
                    "project_api_invoked": True,
                    "baseline_device": "cuda",
                    "custom_device": "npu",
                }
            ],
        },
        "source_inventory": {
            "discovery_complete": True,
            "discovery_sources_checked": [
                "source",
                "bindings",
                "wrappers",
                "autograd",
                "aliases",
                "launch",
                "setup",
                "tests",
            ],
            "out_of_scope_source_groups": [],
            "entries": [
                {
                    "name": "ScalarFwd2D",
                    "unit_identity": "ScalarFwd2D",
                    "variant_or_signature": "forward(dim=2)",
                    "inventory_granularity": "fine_grained",
                    "native_operator_symbols": ["scalar_forward"],
                    "kernel_functions": ["forward_kernel"],
                    "kernel_launch_sites": ["src/scalar_fwd_2d.cu:launch_forward"],
                    "public_entry_mapping": {"python_api": "deepwave.scalar", "autograd": "ScalarForward"},
                    "source_evidence": ["src/scalar_fwd_2d.cu"],
                    "source_path": "src/scalar_fwd_2d.cu",
                }
            ],
        },
        "rows": [
            {
                "row_id": "ScalarFwd2D",
                "unit_identity": "ScalarFwd2D",
                "variant_or_signature": "forward(dim=2)",
                "inventory_granularity": "fine_grained",
                "status": "PASS",
                "native_operator_symbols": ["scalar_forward"],
                "kernel_functions": ["forward_kernel"],
                "kernel_launch_sites": ["src/scalar_fwd_2d.cu:launch_forward"],
                "public_entry_mapping": {"python_api": "deepwave.scalar", "autograd": "ScalarForward"},
                "source_evidence": ["src/scalar_fwd_2d.cu"],
                "opp_custom_op_artifact_evidence": {
                    "path": "opp/ScalarFwd2D/libscalar_fwd_2d.so",
                    "runtime_loaded_artifact_path": "opp/ScalarFwd2D/libscalar_fwd_2d.so",
                    "op_host_source_path": "opp/ScalarFwd2D/op_host/scalar_fwd_2d.cpp",
                    "op_kernel_source_path": "opp/ScalarFwd2D/op_kernel/scalar_fwd_2d.cpp",
                    "build_script_path": "opp/ScalarFwd2D/build.sh",
                    "install_log_path": "migration_reports/opp_install.log",
                    "generated_header_path": "opp/ScalarFwd2D/build_out/autogen/scalar_fwd_2d.h",
                    "op_info_path": "opp/ScalarFwd2D/build_out/op_info/scalar_fwd_2d.json",
                    "kernel_meta_path": "opp/ScalarFwd2D/build_out/kernel_meta/scalar_fwd_2d.o",
                    "project_local": True,
                    "built": True,
                    "installed": True,
                    "native_artifact": True,
                    "compiled_extension": True,
                    "build_provenance": {
                        "command": "bash opp/ScalarFwd2D/build.sh",
                        "log_path": "migration_reports/build.log",
                    },
                },
                "adapter_evidence": {"imported": True},
                "parity_evidence": {"max_abs_error": 0.0, "tolerance": 1e-5},
                "integration_e2e_evidence": {
                    "command": "python test_e2e.py",
                    "passed": True,
                    "project_api_invoked": True,
                    "custom_op_route_executed": True,
                    "native_custom_op_route_executed": True,
                },
                "same_run_runtime_coverage": {
                    "custom_call_count": 3,
                    "same_run": True,
                    "project_api_route": True,
                    "native_custom_op_route_executed": True,
                },
                "performance_evidence": {
                    "baseline_seconds": 0.02,
                    "custom_seconds": 0.01,
                    "speedup_vs_baseline": 2.0,
                    "project_api_invoked": True,
                    "baseline_device": "cuda",
                    "custom_device": "npu",
                },
                "no_fallback_no_zero_call_no_builtin_contamination": _valid_no_fallback_evidence(),
            }
        ],
    }


def _valid_no_fallback_evidence() -> dict[str, object]:
    return {
        "passed": True,
        "fallback_detected": False,
        "zero_call_detected": False,
        "builtin_contamination_detected": False,
        "baseline_only_detected": False,
        "stub_detected": False,
    }


def _write_custom_op_manifest(project_root: Path, required_units: list[str] | None = None) -> None:
    reports_dir = project_root / "migration_reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    units = required_units or ["ScalarFwd2D"]
    _ = (reports_dir / "migration_manifest.json").write_text(
        json.dumps({"required_units": units}),
        encoding="utf-8",
    )


def _write_strict_opp_fixture(project_root: Path) -> None:
    artifact_path = project_root / "opp" / "ScalarFwd2D" / "libscalar_fwd_2d.so"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    _ = artifact_path.write_bytes(b"\x7fELF\x02\x01\x01\x00libascendcl aclrt op_host op_kernel kernel_operator aicore")
    host_source = project_root / "opp" / "ScalarFwd2D" / "op_host" / "scalar_fwd_2d.cpp"
    kernel_source = project_root / "opp" / "ScalarFwd2D" / "op_kernel" / "scalar_fwd_2d.cpp"
    host_source.parent.mkdir(parents=True, exist_ok=True)
    kernel_source.parent.mkdir(parents=True, exist_ok=True)
    _ = host_source.write_text("#include <acl/acl.h>\n// op_host tiling and registration\n", encoding="utf-8")
    _ = kernel_source.write_text("#include <kernel_operator.h>\n// op_kernel AscendC aicore implementation\n", encoding="utf-8")
    build_script = project_root / "opp" / "ScalarFwd2D" / "build.sh"
    _ = build_script.write_text("cmake -S . -B build_out && cmake --build build_out && cmake --install build_out\n", encoding="utf-8")
    generated_header = project_root / "opp" / "ScalarFwd2D" / "build_out" / "autogen" / "scalar_fwd_2d.h"
    op_info = project_root / "opp" / "ScalarFwd2D" / "build_out" / "op_info" / "scalar_fwd_2d.json"
    kernel_meta = project_root / "opp" / "ScalarFwd2D" / "build_out" / "kernel_meta" / "scalar_fwd_2d.o"
    generated_header.parent.mkdir(parents=True, exist_ok=True)
    op_info.parent.mkdir(parents=True, exist_ok=True)
    kernel_meta.parent.mkdir(parents=True, exist_ok=True)
    _ = generated_header.write_text("// generated CANN OPP header\n", encoding="utf-8")
    _ = op_info.write_text('{"op":"ScalarFwd2D"}\n', encoding="utf-8")
    _ = kernel_meta.write_bytes(b"\x7fELF\x02\x01\x01\x00kernel_operator op_kernel")
    reports_dir = project_root / "migration_reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    build_log_text = (
        "bash opp/ScalarFwd2D/build.sh\n"
        "CANN OPP build: op_host/scalar_fwd_2d.cpp op_kernel/scalar_fwd_2d.cpp kernel_operator.h -lascendcl\n"
        "install package to vendors/customize/op_impl/ai_core/tbe\n"
    )
    _ = (reports_dir / "build.log").write_text(build_log_text, encoding="utf-8")
    _ = (reports_dir / "opp_install.log").write_text("install OPP package into ASCEND_OPP_PATH vendors/customize\n", encoding="utf-8")


def _valid_project_analysis_custom_op_surface() -> dict[str, object]:
    return {
        "custom_op_detected": True,
        "discovery_complete": True,
        "discovery_sources_checked": ["source", "bindings", "wrappers", "autograd", "aliases", "launch", "setup", "tests"],
        "searched_source_roots": ["src", "csrc", "tests"],
        "searched_source_paths": ["csrc/op_alpha.cpp", "tests/test_op_alpha.py"],
        "operator_families": ["op_alpha"],
        "fine_grained_operator_units": ["op_alpha:float32", "op_alpha:float16"],
        "discovered_operator_names": ["op_alpha_float32", "op_alpha_float16"],
        "source_evidence": ["csrc/op_alpha.cpp:op_alpha_float32", "csrc/op_alpha.cpp:op_alpha_float16"],
        "negative_evidence": ["grep under src/ and tests/ found no additional operator families"],
        "dynamic_loading_checks": ["import torch.ops.op_alpha succeeded"],
        "build_load_checks": ["python setup.py build_ext --inplace completed"],
        "unresolved_source_groups": [],
        "out_of_scope_source_groups": [],
        "fine_grained_operator_unit_evidence": [
            {"unit_identity": "op_alpha:float32", "source_evidence": ["csrc/op_alpha.cpp:op_alpha_float32"]},
            {"unit_identity": "op_alpha:float16", "source_evidence": ["csrc/op_alpha.cpp:op_alpha_float16"]},
        ],
    }


def test_entry_script_validator_accepts_legacy_minimal_output() -> None:
    result = validate_entry_script({"entry_script_path": "train.py", "run_command": "python train.py"})

    assert result == {"passed": True, "errors": [], "warnings": []}


def test_project_analysis_accepts_generic_multi_unit_custom_op_surface() -> None:
    result = validate_project_analysis(
        {
            "project_dir": "/tmp/project",
            "dependencies": ["torch"],
            "cuda_detected": True,
            "entry_script": "validate_custom_ops_full.py",
            "custom_op_surface": _valid_project_analysis_custom_op_surface(),
        }
    )

    assert result == {"passed": True, "errors": [], "warnings": []}


def test_project_analysis_rejects_detected_custom_op_surface_without_fine_grained_units() -> None:
    surface = _valid_project_analysis_custom_op_surface()
    surface["fine_grained_operator_units"] = []
    surface["fine_grained_operator_unit_evidence"] = []
    result = validate_project_analysis(
        {
            "project_dir": "/tmp/project",
            "dependencies": ["torch"],
            "cuda_detected": True,
            "entry_script": "validate_custom_ops_full.py",
            "custom_op_surface": surface,
        }
    )

    assert result["passed"] is False
    assert any("fine_grained_operator_units" in error and "at least one" in error for error in result["errors"])


def test_project_analysis_rejects_detected_custom_op_surface_without_discovery_complete() -> None:
    surface = _valid_project_analysis_custom_op_surface()
    surface["discovery_complete"] = False
    result = validate_project_analysis(
        {
            "project_dir": "/tmp/project",
            "dependencies": ["torch"],
            "cuda_detected": True,
            "entry_script": "validate_custom_ops_full.py",
            "custom_op_surface": surface,
        }
    )

    assert result["passed"] is False
    assert any("discovery_complete" in error and "custom_op_detected is true" in error for error in result["errors"])


def test_project_analysis_rejects_detected_custom_op_surface_without_source_path_trail() -> None:
    surface = _valid_project_analysis_custom_op_surface()
    surface["searched_source_paths"] = []
    result = validate_project_analysis(
        {
            "project_dir": "/tmp/project",
            "dependencies": ["torch"],
            "cuda_detected": True,
            "entry_script": "validate_custom_ops_full.py",
            "custom_op_surface": surface,
        }
    )

    assert result["passed"] is False
    assert any("searched_source_paths" in error and "source path" in error for error in result["errors"])


def test_project_analysis_rejects_discovery_complete_with_unresolved_source_groups() -> None:
    surface = _valid_project_analysis_custom_op_surface()
    surface["unresolved_source_groups"] = ["csrc/unresolved_group"]
    result = validate_project_analysis(
        {
            "project_dir": "/tmp/project",
            "dependencies": ["torch"],
            "cuda_detected": True,
            "entry_script": "validate_custom_ops_full.py",
            "custom_op_surface": surface,
        }
    )

    assert result["passed"] is False
    assert any("unresolved_source_groups" in error and "must be empty" in error for error in result["errors"])


@pytest.mark.parametrize(
    "run_command",
    [
        "/tmp/project/.venv/bin/python /tmp/project/train.py --config cfg.yaml",
        "python train.py --config cfg.yaml",
    ],
)
def test_entry_script_validator_accepts_safe_single_process_commands(run_command: str) -> None:
    result = validate_entry_script({"entry_script_path": "train.py", "run_command": run_command})

    assert result == {"passed": True, "errors": [], "warnings": []}


@pytest.mark.parametrize(
    "run_command",
    [
        "python train.py; python report.py",
        "python train.py && python report.py",
        "python train.py || true",
        "python train.py | tee out.log",
        "python `which train.py`",
        "python $(pwd)/train.py",
        "python train.py > out.log",
        "python train.py < input.txt",
        "python train.py\npython report.py",
        "python train.py\rpython report.py",
        "python train.py & python report.py",
        "bash run_validation.sh",
        "sh run_validation.sh",
        "source env.sh",
        "/usr/bin/env sh -c id",
        "/usr/bin/env bash -lc id",
        "/usr/bin/env bash run.sh",
    ],
)
def test_entry_script_validator_rejects_unsafe_shell_run_commands(run_command: str) -> None:
    result = validate_entry_script({"entry_script_path": "train.py", "run_command": run_command})

    assert result["passed"] is False
    assert any("wrapper script" in error or "single process" in error or "shell through env" in error for error in result["errors"])


def test_entry_script_validator_rejects_bare_report_only_final_evidence_validator() -> None:
    result = validate_entry_script(
        {
            "entry_script_path": "/tmp/project/migration_reports/final_evidence_validate.py",
            "run_command": "python /tmp/project/migration_reports/final_evidence_validate.py",
        }
    )

    assert result["passed"] is False
    assert any("report-only evidence validator" in error for error in result["errors"])


def test_entry_script_validator_accepts_valid_custom_op_contract(tmp_path: Path) -> None:
    script_path = tmp_path / "validate_custom_ops_full.py"
    _ = script_path.write_text("print('custom-op validation')\n", encoding="utf-8")

    result = validate_entry_script(_valid_custom_op_contract(str(script_path)))

    assert result == {"passed": True, "errors": [], "warnings": []}


def test_entry_script_validator_accepts_relative_custom_op_entry_script(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    script_path = project_dir / "validate_custom_ops_full.py"
    _ = script_path.write_text("print('custom-op validation')\n", encoding="utf-8")

    result = validate_entry_script(
        _valid_custom_op_contract("validate_custom_ops_full.py", str(project_dir))
    )

    assert result == {"passed": True, "errors": [], "warnings": []}


def test_entry_script_validator_rejects_missing_custom_op_entry_script(tmp_path: Path) -> None:
    missing_script = tmp_path / "validate_custom_ops_full.py"

    result = validate_entry_script(_valid_custom_op_contract(str(missing_script)))

    assert result["passed"] is False
    assert any("existing file for custom-op contracts" in error for error in result["errors"])


def test_entry_script_validator_rejects_project_escape_custom_op_entry_script(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    external_dir = tmp_path / "external"
    external_dir.mkdir()
    outside_script = external_dir / "validate_custom_ops_full.py"
    _ = outside_script.write_text("print('outside project')\n", encoding="utf-8")
    escaped_script = project_dir / "validate_custom_ops_full.py"
    escaped_script.symlink_to(outside_script)

    result = validate_entry_script(
        _valid_custom_op_contract(str(escaped_script), str(project_dir))
    )

    assert result["passed"] is False
    assert any("existing file for custom-op contracts" in error for error in result["errors"])


def test_custom_op_final_gate_rejects_full_pass_without_project_root() -> None:
    result = validate_custom_op_final_gate(_valid_custom_op_final_gate())

    assert result["passed"] is False
    assert any("project_root is required" in error for error in result["errors"])


def test_custom_op_final_gate_accepts_existing_native_artifact_with_project_root(tmp_path: Path) -> None:
    payload = _valid_custom_op_final_gate()
    _write_custom_op_manifest(tmp_path)
    _write_strict_opp_fixture(tmp_path)

    result = validate_custom_op_final_gate(payload, project_root=tmp_path)

    assert result == {"passed": True, "errors": [], "warnings": []}


def test_custom_op_final_gate_accepts_strict_opp_fixture_with_build_install_and_generated_artifacts(tmp_path: Path) -> None:
    payload = _valid_custom_op_final_gate()
    _write_custom_op_manifest(tmp_path)
    _write_strict_opp_fixture(tmp_path)

    result = validate_custom_op_final_gate(payload, project_root=tmp_path)

    assert result == {"passed": True, "errors": [], "warnings": []}


def test_custom_op_final_gate_rejects_self_consistent_report_missing_manifest_required_unit(tmp_path: Path) -> None:
    payload = _valid_custom_op_final_gate()
    _write_custom_op_manifest(tmp_path, ["ScalarFwd2D", "ScalarBwd2D"])
    _write_strict_opp_fixture(tmp_path)

    result = validate_custom_op_final_gate(payload, project_root=tmp_path)

    assert result["passed"] is False
    assert any("migration_manifest.required_units" in error for error in result["errors"])
    assert any("required_units length (2)" in error for error in result["errors"])


def test_custom_op_final_gate_rejects_missing_native_artifact_with_project_root(tmp_path: Path) -> None:
    payload = _valid_custom_op_final_gate()
    _write_custom_op_manifest(tmp_path)
    _write_strict_opp_fixture(tmp_path)
    (tmp_path / "opp" / "ScalarFwd2D" / "libscalar_fwd_2d.so").unlink()

    result = validate_custom_op_final_gate(payload, project_root=tmp_path)

    assert result["passed"] is False
    assert any("native artifact path must exist" in error for error in result["errors"])


def test_custom_op_final_gate_rejects_text_file_disguised_as_native_artifact(tmp_path: Path) -> None:
    payload = _valid_custom_op_final_gate()
    _write_custom_op_manifest(tmp_path)
    _write_strict_opp_fixture(tmp_path)
    artifact_path = tmp_path / "opp" / "ScalarFwd2D" / "libscalar_fwd_2d.so"
    _ = artifact_path.write_text("not a binary", encoding="utf-8")

    result = validate_custom_op_final_gate(payload, project_root=tmp_path)

    assert result["passed"] is False
    assert any("non-empty compiled binary" in error for error in result["errors"])


def test_custom_op_final_gate_rejects_torch_cpu_extension_masquerading_as_ascend_artifact(tmp_path: Path) -> None:
    payload = _valid_custom_op_final_gate()
    _write_custom_op_manifest(tmp_path)
    rows = cast(list[dict[str, object]], payload["rows"])
    artifact = cast(dict[str, object], rows[0]["opp_custom_op_artifact_evidence"])
    fake_path = "pointnet2_ops/ascend_custom_op/op_plugin/lib/_ext.cpython-310-x86_64-linux-gnu.so"
    artifact.update(
        {
            "path": fake_path,
            "project_relative_path": fake_path,
            "opp_artifact_path": fake_path,
            "runtime_loaded_artifact_path": fake_path,
            "runtime_loaded_module_file": fake_path,
            "native_custom_op_artifact": fake_path,
            "build_provenance": {
                "command": ".venv/bin/python setup.py build_ext --inplace",
                "log_path": "migration_reports/build_ext_npu.log",
            },
        }
    )
    artifact_path = tmp_path / fake_path
    artifact_path.parent.mkdir(parents=True)
    _ = artifact_path.write_bytes(b"\x7fELF\x02\x01\x01\x00torch_cpu ATen scatter_add cdist topk")
    build_log = tmp_path / "migration_reports" / "build_ext_npu.log"
    build_log.parent.mkdir(parents=True, exist_ok=True)
    _ = build_log.write_text(
        "g++ -shared sampling.o npu_ops.o -ltorch_cpu -ltorch -ltorch_python -o pointnet2_ops/_ext.so\n",
        encoding="utf-8",
    )

    result = validate_custom_op_final_gate(payload, project_root=tmp_path)

    assert result["passed"] is False
    assert any("NpuExtension/CppExtension/ATen/libtorch-only" in error for error in result["errors"])
    assert any("CANN/ACL/AscendC/OPP build" in error for error in result["errors"])
    assert any("independent CANN/ACL/AscendC binary or source evidence" in error for error in result["errors"])


def test_custom_op_final_gate_rejects_npuextension_aten_only_project_local_opp_artifact(tmp_path: Path) -> None:
    payload = _valid_custom_op_final_gate()
    _write_custom_op_manifest(tmp_path)
    rows = cast(list[dict[str, object]], payload["rows"])
    artifact = cast(dict[str, object], rows[0]["opp_custom_op_artifact_evidence"])
    fake_path = "project_local_opp/ascend/custom_op/op_plugin/lib/_ext.so"
    artifact.clear()
    artifact.update(
        {
            "path": fake_path,
            "project_relative_path": fake_path,
            "runtime_loaded_artifact_path": fake_path,
            "project_local": True,
            "built": True,
            "native_custom_op_artifact": fake_path,
            "build_provenance": {
                "command": "python setup.py build_ext --inplace # torch_npu.utils.cpp_extension.NpuExtension",
                "log_path": "migration_reports/build_ext_npu.log",
            },
        }
    )
    artifact_path = tmp_path / fake_path
    artifact_path.parent.mkdir(parents=True)
    _ = artifact_path.write_bytes(b"\x7fELF\x02\x01\x01\x00torch_npu ATen torch/extension.h")
    build_log = tmp_path / "migration_reports" / "build_ext_npu.log"
    build_log.parent.mkdir(parents=True, exist_ok=True)
    build_log_text = (
        "setup.py uses torch_npu.utils.cpp_extension.NpuExtension\n"
        "g++ npu_ops.cpp -Itorch/include -ltorch_npu -ltorch_cpu -ltorch_python\n"
        "npu_ops.cpp includes torch/extension.h and ATen/ATen.h\n"
    )
    _ = build_log.write_text(build_log_text, encoding="utf-8")

    result = validate_custom_op_final_gate(payload, project_root=tmp_path)

    assert result["passed"] is False
    assert any("NpuExtension/CppExtension/ATen/libtorch-only" in error for error in result["errors"])
    assert any("op_host source path" in error for error in result["errors"])
    assert any("op_kernel/AscendC source path" in error for error in result["errors"])


def test_custom_op_final_gate_rejects_existing_generic_so_without_opp_evidence(tmp_path: Path) -> None:
    payload = _valid_custom_op_final_gate()
    _write_custom_op_manifest(tmp_path)
    rows = cast(list[dict[str, object]], payload["rows"])
    artifact = cast(dict[str, object], rows[0]["opp_custom_op_artifact_evidence"])
    generic_path = "ascend/custom_op/lib/libgeneric.so"
    artifact.clear()
    artifact.update(
        {
            "path": generic_path,
            "project_relative_path": generic_path,
            "runtime_loaded_artifact_path": generic_path,
            "project_local": True,
            "built": True,
            "build_provenance": {
                "command": "g++ -shared generic.cpp -o ascend/custom_op/lib/libgeneric.so",
                "log_path": "migration_reports/build.log",
            },
        }
    )
    artifact_path = tmp_path / generic_path
    artifact_path.parent.mkdir(parents=True)
    _ = artifact_path.write_bytes(b"\x7fELF\x02\x01\x01\x00generic shared library")
    build_log = tmp_path / "migration_reports" / "build.log"
    build_log.parent.mkdir(parents=True, exist_ok=True)
    _ = build_log.write_text("g++ -shared generic.cpp -o libgeneric.so\n", encoding="utf-8")

    result = validate_custom_op_final_gate(payload, project_root=tmp_path)

    assert result["passed"] is False
    assert any("op_host source path" in error for error in result["errors"])
    assert any("OPP build script" in error for error in result["errors"])
    assert any("generated OPP artifact categories" in error for error in result["errors"])


def test_custom_op_final_gate_rejects_opp_named_paths_without_generated_opp_categories(tmp_path: Path) -> None:
    payload = _valid_custom_op_final_gate()
    _write_custom_op_manifest(tmp_path)
    _write_strict_opp_fixture(tmp_path)
    rows = cast(list[dict[str, object]], payload["rows"])
    artifact = cast(dict[str, object], rows[0]["opp_custom_op_artifact_evidence"])
    _ = artifact.pop("generated_header_path")
    _ = artifact.pop("op_info_path")
    _ = artifact.pop("kernel_meta_path")
    artifact["generated_artifacts"] = ["opp/ScalarFwd2D/build_out/not_real_opp_marker.txt"]
    marker = tmp_path / "opp" / "ScalarFwd2D" / "build_out" / "not_real_opp_marker.txt"
    _ = marker.write_text("not a generated OPP category\n", encoding="utf-8")

    result = validate_custom_op_final_gate(payload, project_root=tmp_path)

    assert result["passed"] is False
    assert any("generated OPP artifact categories" in error for error in result["errors"])


def test_custom_op_final_gate_accepts_opp_package_artifact_as_generated_evidence(tmp_path: Path) -> None:
    payload = _valid_custom_op_final_gate()
    _write_custom_op_manifest(tmp_path)
    _write_strict_opp_fixture(tmp_path)
    rows = cast(list[dict[str, object]], payload["rows"])
    artifact = cast(dict[str, object], rows[0]["opp_custom_op_artifact_evidence"])
    _ = artifact.pop("generated_header_path")
    _ = artifact.pop("op_info_path")
    _ = artifact.pop("kernel_meta_path")
    package_path = "opp/ScalarFwd2D/packages/scalar_fwd_2d.run"
    artifact["opp_package_artifact"] = package_path
    package = tmp_path / package_path
    package.parent.mkdir(parents=True, exist_ok=True)
    _ = package.write_bytes(b"CANN OPP package")

    result = validate_custom_op_final_gate(payload, project_root=tmp_path)

    assert result == {"passed": True, "errors": [], "warnings": []}


def test_custom_op_final_gate_accepts_install_provenance_mapping_with_command(tmp_path: Path) -> None:
    payload = _valid_custom_op_final_gate()
    _write_custom_op_manifest(tmp_path)
    _write_strict_opp_fixture(tmp_path)
    rows = cast(list[dict[str, object]], payload["rows"])
    artifact = cast(dict[str, object], rows[0]["opp_custom_op_artifact_evidence"])
    _ = artifact.pop("install_log_path")
    artifact["install_provenance"] = {
        "command": "bash opp/ScalarFwd2D/build.sh --install",
        "log_path": "migration_reports/opp_install.log",
    }

    result = validate_custom_op_final_gate(payload, project_root=tmp_path)

    assert result == {"passed": True, "errors": [], "warnings": []}


def test_custom_op_final_gate_rejects_spoofed_cann_words_without_native_link_or_source(tmp_path: Path) -> None:
    payload = _valid_custom_op_final_gate()
    _write_custom_op_manifest(tmp_path)
    artifact_path = tmp_path / "opp" / "ScalarFwd2D" / "libscalar_fwd_2d.so"
    artifact_path.parent.mkdir(parents=True)
    _ = artifact_path.write_bytes(b"\x7fELF\x02\x01\x01\x00ordinary torch extension with ascend words")
    build_log = tmp_path / "migration_reports" / "build.log"
    build_log.parent.mkdir(parents=True, exist_ok=True)
    _ = build_log.write_text(
        "CANN migration note: g++ -shared scalar.o -ltorch_cpu -ltorch_python -o libscalar_fwd_2d.so\n",
        encoding="utf-8",
    )

    result = validate_custom_op_final_gate(payload, project_root=tmp_path)

    assert result["passed"] is False
    assert any("CANN/ACL/AscendC/OPP build" in error for error in result["errors"])


def test_custom_op_final_gate_rejects_evidence_only_native_marker_artifact(tmp_path: Path) -> None:
    payload = _valid_custom_op_final_gate()
    _write_custom_op_manifest(tmp_path)
    fake_path = "pointnet2_ops/ascend_custom_op/op_plugin/lib/libpointnet2_ascend_custom_op_evidence.so"
    host_source = "pointnet2_ops/ascend_custom_op/op_host/pointnet2_ascend_acl_evidence.cpp"
    kernel_source = "pointnet2_ops/ascend_custom_op/op_kernel/pointnet2_ascendc_kernel_evidence.cpp"
    rows = cast(list[dict[str, object]], payload["rows"])
    artifact = cast(dict[str, object], rows[0]["opp_custom_op_artifact_evidence"])
    artifact.update(
        {
            "path": fake_path,
            "project_relative_path": fake_path,
            "artifact_path": fake_path,
            "binary_path": fake_path,
            "runtime_loaded_artifact_path": fake_path,
            "source_paths": [host_source, kernel_source],
            "build_provenance": {
                "command": f"g++ -shared {host_source} {kernel_source} -lascendcl -o {fake_path}",
                "log_path": "migration_reports/ascend_custom_op_build.log",
            },
        }
    )
    artifact_path = tmp_path / fake_path
    artifact_path.parent.mkdir(parents=True)
    _ = artifact_path.write_bytes(b"\x7fELF\x02\x01\x01\x00libascendcl aclrt op_host op_kernel aicore")
    for source_path in (tmp_path / host_source, tmp_path / kernel_source):
        source_path.parent.mkdir(parents=True, exist_ok=True)
    host_text = (
        "extern \"C\" int pointnet2_ascend_record_unit_call(unsigned long i) { return 1000 + i; }\n"
        "const char* marker = \"aclrt libascendcl op_host evidence\";\n"
    )
    _ = (tmp_path / host_source).write_text(host_text, encoding="utf-8")
    kernel_text = 'extern "C" const char* pointnet2_ascendc_kernel_evidence() { return "kernel_operator.h op_kernel aicore"; }\n'
    _ = (tmp_path / kernel_source).write_text(kernel_text, encoding="utf-8")
    build_log = tmp_path / "migration_reports" / "ascend_custom_op_build.log"
    build_log_text = (
        f"command: g++ -shared {host_source} {kernel_source} -lascendcl -o {fake_path}\n"
        "native_tokens: -lascendcl libascendcl aclrt op_host op_kernel aicore kernel_operator.h\n"
        "returncode: 0\n"
    )
    _ = build_log.write_text(build_log_text, encoding="utf-8")

    result = validate_custom_op_final_gate(payload, project_root=tmp_path)

    assert result["passed"] is False
    assert any("evidence-only/stub/native-marker" in error for error in result["errors"])


def test_custom_op_final_gate_rejects_symlink_escape_native_artifact(tmp_path: Path) -> None:
    payload = _valid_custom_op_final_gate()
    _write_custom_op_manifest(tmp_path)
    _write_strict_opp_fixture(tmp_path)
    outside_dir = tmp_path.parent / f"{tmp_path.name}_outside"
    outside_dir.mkdir()
    outside_artifact = outside_dir / "libscalar_fwd_2d.so"
    _ = outside_artifact.write_bytes(b"\x7fELF\x02\x01\x01\x00libascendcl aclrt native-op")
    artifact_link = tmp_path / "opp" / "ScalarFwd2D" / "libscalar_fwd_2d.so"
    artifact_link.unlink()
    artifact_link.symlink_to(outside_artifact)

    result = validate_custom_op_final_gate(payload, project_root=tmp_path)

    assert result["passed"] is False
    assert any("native artifact path must exist" in error for error in result["errors"])


def test_custom_op_final_gate_rejects_runtime_loaded_artifact_mismatch() -> None:
    payload = _valid_custom_op_final_gate()
    rows = cast(list[dict[str, object]], payload["rows"])
    artifact = cast(dict[str, object], rows[0]["opp_custom_op_artifact_evidence"])
    artifact["runtime_loaded_artifact_path"] = "opp/ScalarFwd2D/libdifferent_op.so"

    result = validate_custom_op_final_gate(payload)

    assert result["passed"] is False
    assert any("same-run runtime loaded the native compiled artifact" in error for error in result["errors"])


def test_custom_op_final_gate_rejects_missing_build_provenance() -> None:
    payload = _valid_custom_op_final_gate()
    rows = cast(list[dict[str, object]], payload["rows"])
    artifact = cast(dict[str, object], rows[0]["opp_custom_op_artifact_evidence"])
    _ = artifact.pop("build_provenance")

    result = validate_custom_op_final_gate(payload)

    assert result["passed"] is False
    assert any("build_provenance" in error for error in result["errors"])


def test_custom_op_final_gate_rejects_python_shim_artifact() -> None:
    payload = _valid_custom_op_final_gate()
    rows = cast(list[dict[str, object]], payload["rows"])
    rows[0]["opp_custom_op_artifact_evidence"] = {
        "path": "pointnet2_ops/_ext.py",
        "project_local": True,
        "built": True,
        "python_shim": True,
        "artifact_type": "python_binding_surface",
    }

    result = validate_custom_op_final_gate(payload)

    assert result["passed"] is False
    assert any("Python shim" in error for error in result["errors"])
    assert any("native compiled Ascend custom-op artifact" in error for error in result["errors"])


def test_custom_op_final_gate_rejects_generic_shared_library_without_ascend_proof() -> None:
    payload = _valid_custom_op_final_gate()
    rows = cast(list[dict[str, object]], payload["rows"])
    rows[0]["opp_custom_op_artifact_evidence"] = {
        "path": "build/libgeneric_extension.so",
        "project_local": True,
        "built": True,
        "compiled_extension": True,
    }

    result = validate_custom_op_final_gate(payload)

    assert result["passed"] is False
    assert any("native compiled Ascend custom-op artifact" in error for error in result["errors"])


def test_custom_op_final_gate_rejects_boolean_native_claim_without_compiled_artifact_path() -> None:
    payload = _valid_custom_op_final_gate()
    rows = cast(list[dict[str, object]], payload["rows"])
    rows[0]["opp_custom_op_artifact_evidence"] = {
        "project_relative_path": "pointnet2_ops/_ext-src/src/bindings.cpp",
        "project_local": True,
        "present": True,
        "loaded": True,
        "native_custom_op_artifact": True,
        "runtime_module_file": "pointnet2_ops/_ext.py",
        "runtime_project_local_artifact": {
            "module_file": "pointnet2_ops/_ext.py",
            "project_local": True,
            "suffix": ".py",
        },
    }

    result = validate_custom_op_final_gate(payload)

    assert result["passed"] is False
    assert any("native compiled Ascend custom-op artifact" in error for error in result["errors"])


def test_custom_op_final_gate_rejects_native_artifact_under_python_bindings_dir_without_project_root() -> None:
    payload = _valid_custom_op_final_gate()
    rows = cast(list[dict[str, object]], payload["rows"])
    artifact = cast(dict[str, object], rows[0]["opp_custom_op_artifact_evidence"])
    artifact["path"] = "opp/python_bindings/op_plugin/libscalar_fwd_2d.so"
    artifact["runtime_loaded_artifact_path"] = "opp/python_bindings/op_plugin/libscalar_fwd_2d.so"

    result = validate_custom_op_final_gate(payload)

    assert result["passed"] is False
    assert any("project_root is required" in error for error in result["errors"])


def test_custom_op_final_gate_accepts_indexed_device_strings() -> None:
    payload = _valid_custom_op_final_gate()
    performance_report = cast(dict[str, object], payload["performance_report"])
    performance_report["baseline_device"] = "cuda:0"
    performance_report["custom_device"] = "npu:0"
    performance_report["overall_baseline_device"] = "torch.cuda"
    performance_report["overall_custom_device"] = "Ascend 910B"
    entries = cast(list[dict[str, object]], performance_report["entries"])
    entries[0]["baseline_device"] = "cuda:0"
    entries[0]["custom_device"] = "torch_npu.npu"
    rows = cast(list[dict[str, object]], payload["rows"])
    performance_evidence = cast(dict[str, object], rows[0]["performance_evidence"])
    performance_evidence["baseline_device"] = "torch.cuda"
    performance_evidence["custom_device"] = "npu:0"

    result = validate_custom_op_final_gate(payload)

    assert result["passed"] is False
    assert any("project_root is required" in error for error in result["errors"])


def test_custom_op_final_gate_rejects_diagnostic_only_baseline() -> None:
    payload = _valid_custom_op_final_gate()
    performance_report = cast(dict[str, object], payload["performance_report"])
    performance_report["baseline_mode"] = "diagnostic_only"
    entries = cast(list[dict[str, object]], performance_report["entries"])
    entries[0]["baseline_mode"] = "diagnostic_only"
    rows = cast(list[dict[str, object]], payload["rows"])
    performance_evidence = cast(dict[str, object], rows[0]["performance_evidence"])
    performance_evidence["baseline_mode"] = "diagnostic_only"

    result = validate_custom_op_final_gate(payload)

    assert result["passed"] is False
    assert any("diagnostic-only" in error for error in result["errors"])


def test_custom_op_final_gate_rejects_missing_complete_performance_report() -> None:
    payload = _valid_custom_op_final_gate()
    _ = payload.pop("performance_report")

    result = validate_custom_op_final_gate(payload)

    assert result["passed"] is False
    assert any("performance_report" in error for error in result["errors"])


def test_custom_op_final_gate_rejects_incomplete_performance_speedup_report() -> None:
    payload = _valid_custom_op_final_gate()
    payload["performance_report"] = {
        "complete": False,
        "unit_count": 1,
        "path": "migration_reports/performance.json",
        "project_api_invoked": True,
        "overall_baseline_seconds": 0.05,
        "overall_custom_seconds": 0.04,
        "overall_speedup_vs_baseline": 1.25,
        "overall_project_api_invoked": True,
        "overall_all_units_replaced": True,
        "entries": [
            {
                "unit_identity": "ScalarFwd2D",
                "baseline_seconds": 0.02,
                "custom_seconds": 0.01,
                "speedup_vs_baseline": 2.0,
                "project_api_invoked": True,
            }
        ],
    }

    result = validate_custom_op_final_gate(payload)

    assert result["passed"] is False
    assert any("performance_report.complete" in error for error in result["errors"])


def test_custom_op_final_gate_rejects_missing_overall_speedup_report() -> None:
    payload = _valid_custom_op_final_gate()
    performance_report = cast(dict[str, object], payload["performance_report"])
    _ = performance_report.pop("overall_baseline_seconds")
    _ = performance_report.pop("overall_custom_seconds")
    _ = performance_report.pop("overall_speedup_vs_baseline")

    result = validate_custom_op_final_gate(payload)

    assert result["passed"] is False
    assert any("overall speedup fields" in error for error in result["errors"])


def test_custom_op_final_gate_rejects_missing_overall_route_proof() -> None:
    payload = _valid_custom_op_final_gate()
    performance_report = cast(dict[str, object], payload["performance_report"])
    _ = performance_report.pop("overall_project_api_invoked")

    result = validate_custom_op_final_gate(payload)

    assert result["passed"] is False
    assert any("overall timing ran through the project API" in error for error in result["errors"])


def test_custom_op_final_gate_rejects_missing_overall_all_units_replaced_proof() -> None:
    payload = _valid_custom_op_final_gate()
    performance_report = cast(dict[str, object], payload["performance_report"])
    _ = performance_report.pop("overall_all_units_replaced")

    result = validate_custom_op_final_gate(payload)

    assert result["passed"] is False
    assert any("after all source-discovered custom-op units were replaced" in error for error in result["errors"])


@pytest.mark.parametrize("field_name", ["native_operator_symbols", "kernel_functions", "source_evidence"])
def test_custom_op_final_gate_rejects_missing_native_inventory_source_fields(field_name: str) -> None:
    payload = _valid_custom_op_final_gate()
    source_inventory = cast(dict[str, object], payload["source_inventory"])
    entries = cast(list[dict[str, object]], source_inventory["entries"])
    entries[0][field_name] = []

    result = validate_custom_op_final_gate(payload)

    assert result["passed"] is False
    assert any(field_name in error for error in result["errors"])


@pytest.mark.parametrize(
    "field_name",
    ["unit_identity", "variant_or_signature", "kernel_launch_sites", "public_entry_mapping", "inventory_granularity"],
)
def test_custom_op_final_gate_rejects_missing_fine_grained_source_fields(field_name: str) -> None:
    payload = _valid_custom_op_final_gate()
    source_inventory = cast(dict[str, object], payload["source_inventory"])
    entries = cast(list[dict[str, object]], source_inventory["entries"])
    _ = entries[0].pop(field_name)

    result = validate_custom_op_final_gate(payload)

    assert result["passed"] is False
    assert any("fine-grained unit fields" in error and field_name in error for error in result["errors"])


@pytest.mark.parametrize("field_name", ["native_operator_symbols", "kernel_functions", "source_evidence"])
def test_custom_op_final_gate_rejects_missing_native_inventory_row_fields(field_name: str) -> None:
    payload = _valid_custom_op_final_gate()
    rows = cast(list[dict[str, object]], payload["rows"])
    rows[0][field_name] = []

    result = validate_custom_op_final_gate(payload)

    assert result["passed"] is False
    assert any(field_name in error for error in result["errors"])


@pytest.mark.parametrize(
    "field_name",
    ["unit_identity", "variant_or_signature", "kernel_launch_sites", "public_entry_mapping", "inventory_granularity"],
)
def test_custom_op_final_gate_rejects_missing_fine_grained_row_fields(field_name: str) -> None:
    payload = _valid_custom_op_final_gate()
    rows = cast(list[dict[str, object]], payload["rows"])
    _ = rows[0].pop(field_name)

    result = validate_custom_op_final_gate(payload)

    assert result["passed"] is False
    assert any("fine-grained unit fields" in error and field_name in error for error in result["errors"])


def test_custom_op_final_gate_rejects_deepwave_like_collapsed_two_row_inventory() -> None:
    payload = _valid_custom_op_final_gate()
    payload["inventory_count"] = 2
    payload["manifest_entries"] = 2
    payload["closed_pass_entries"] = 2
    source_inventory = cast(dict[str, object], payload["source_inventory"])
    source_inventory["entries"] = [
        {
            "name": "scalar_forward",
            "unit_identity": "scalar_forward",
            "variant_or_signature": "family_forward_all_variants",
            "inventory_granularity": "coarse",
            "family_only": True,
            "native_operator_symbols": {"variants": ["scalar_forward_1d", "scalar_forward_2d", "scalar_forward_3d"]},
            "kernel_functions": {"variants": ["forward_kernel", "add_sources", "record_receivers"]},
            "kernel_launch_sites": {"variants": ["scalar.cu:forward"]},
            "public_entry_mapping": {"python_api": "deepwave.scalar"},
            "source_evidence": ["deepwave_original_src/scalar.cu"],
        },
        {
            "name": "scalar_backward",
            "unit_identity": "scalar_backward",
            "variant_or_signature": "family_backward_all_variants",
            "inventory_granularity": "family_only",
            "family_only": True,
            "native_operator_symbols": {"variants": ["scalar_backward_1d", "scalar_backward_2d", "scalar_backward_3d"]},
            "kernel_functions": {"variants": ["backward_kernel", "combine_grad_v"]},
            "kernel_launch_sites": {"variants": ["scalar.cu:backward"]},
            "public_entry_mapping": {"python_api": "deepwave.scalar"},
            "source_evidence": ["deepwave_original_src/scalar.cu"],
        },
    ]
    row_template = cast(list[dict[str, object]], payload["rows"])[0]
    payload["rows"] = []
    for name in ("scalar_forward", "scalar_backward"):
        row = dict(row_template)
        row.update(
            {
                "row_id": name,
                "unit_identity": name,
                "variant_or_signature": f"{name}_all_variants",
                "inventory_granularity": "coarse",
                "family_only": True,
                "native_operator_symbols": {"variants": [f"{name}_1d", f"{name}_2d", f"{name}_3d"]},
                "kernel_functions": {"variants": ["forward_kernel", "backward_kernel", "combine_grad_v"]},
                "kernel_launch_sites": {"variants": ["scalar.cu:launch"]},
                "public_entry_mapping": {"python_api": "deepwave.scalar"},
                "source_evidence": ["deepwave_original_src/scalar.cu"],
            }
        )
        cast(list[dict[str, object]], payload["rows"]).append(row)

    result = validate_custom_op_final_gate(payload)

    assert result["passed"] is False
    assert any("coarse" in error.lower() or "nested family" in error.lower() for error in result["errors"])


def test_custom_op_final_gate_accepts_generic_multi_unit_fine_grained_inventory(tmp_path: Path) -> None:
    payload = _valid_custom_op_final_gate()
    payload["inventory_count"] = 2
    payload["manifest_entries"] = 2
    payload["closed_pass_entries"] = 2
    source_inventory = cast(dict[str, object], payload["source_inventory"])
    row_template = cast(list[dict[str, object]], payload["rows"])[0]
    entries: list[dict[str, object]] = []
    rows: list[dict[str, object]] = []
    performance_entries: list[dict[str, object]] = []
    for unit_name, signature in (("op_alpha_float32", "alpha(float32)"), ("op_alpha_float16", "alpha(float16)")):
        common = {
            "unit_identity": unit_name,
            "variant_or_signature": signature,
            "inventory_granularity": "fine_grained",
            "native_operator_symbols": [f"{unit_name}_native"],
            "kernel_functions": [f"{unit_name}_kernel"],
            "kernel_launch_sites": [f"csrc/op_alpha.cpp:{unit_name}_launch"],
            "public_entry_mapping": {"python_api": "pkg.ops.alpha", "signature": signature},
            "source_evidence": [f"csrc/op_alpha.cpp:{signature}"],
        }
        entries.append({"name": unit_name, **common})
        row = dict(row_template)
        row.update({"row_id": unit_name, **common})
        rows.append(row)
        performance_entries.append(
            {
                "unit_identity": unit_name,
                "baseline_seconds": 0.02,
                "custom_seconds": 0.01,
                "speedup_vs_baseline": 2.0,
                "project_api_invoked": True,
                "baseline_device": "cuda",
                "custom_device": "npu",
            }
        )
    source_inventory["entries"] = entries
    payload["rows"] = rows
    payload["performance_report"] = {
        "complete": True,
        "unit_count": 2,
        "path": "migration_reports/performance.json",
        "project_api_invoked": True,
        "baseline_device": "cuda",
        "custom_device": "npu",
        "overall_baseline_seconds": 0.05,
        "overall_custom_seconds": 0.04,
        "overall_speedup_vs_baseline": 1.25,
        "overall_baseline_device": "cuda",
        "overall_custom_device": "npu",
        "overall_evidence": {
            "project_api_invoked": True,
            "custom_op_route_executed": True,
            "all_custom_op_units_replaced": True,
        },
        "entries": performance_entries,
    }

    _write_custom_op_manifest(tmp_path, ["op_alpha_float32", "op_alpha_float16"])
    _write_strict_opp_fixture(tmp_path)

    result = validate_custom_op_final_gate(payload, project_root=tmp_path)

    assert result == {"passed": True, "errors": [], "warnings": []}


def test_custom_op_final_gate_rejects_performance_report_unit_mismatch() -> None:
    payload = _valid_custom_op_final_gate()
    performance_report = cast(dict[str, object], payload["performance_report"])
    performance_report["entries"] = [
        {
            "unit_identity": "other_unit",
            "baseline_seconds": 0.02,
            "custom_seconds": 0.01,
            "speedup_vs_baseline": 2.0,
            "project_api_invoked": True,
        }
    ]

    result = validate_custom_op_final_gate(payload)

    assert result["passed"] is False
    assert any("performance_report must match manifest rows" in error for error in result["errors"])


def test_custom_op_final_gate_rejects_row_count_name_only_inventory() -> None:
    payload = _valid_custom_op_final_gate()
    payload["source_inventory"] = {
        "discovery_complete": True,
        "discovery_sources_checked": [
            "source",
            "bindings",
            "wrappers",
            "autograd",
            "aliases",
            "launch",
            "setup",
            "tests",
        ],
        "out_of_scope_source_groups": [],
        "entries": [{"name": "ScalarFwd2D"}],
    }

    result = validate_custom_op_final_gate(payload)

    assert result["passed"] is False
    assert any("native inventory fields" in error for error in result["errors"])


def test_custom_op_final_gate_rejects_missing_source_inventory_out_of_scope_groups() -> None:
    payload = _valid_custom_op_final_gate()
    source_inventory = cast(dict[str, object], payload["source_inventory"])
    _ = source_inventory.pop("out_of_scope_source_groups")

    result = validate_custom_op_final_gate(payload)

    assert result["passed"] is False
    assert any("out_of_scope_source_groups" in error for error in result["errors"])


def test_custom_op_final_gate_rejects_count_mismatch() -> None:
    payload = _valid_custom_op_final_gate()
    payload["closed_pass_entries"] = 0

    result = validate_custom_op_final_gate(payload)

    assert result["passed"] is False
    assert any("must match" in error for error in result["errors"])


def test_custom_op_final_gate_rejects_missing_row_evidence() -> None:
    payload = _valid_custom_op_final_gate()
    rows = cast(list[dict[str, object]], payload["rows"])
    rows[0]["adapter_evidence"] = {}

    result = validate_custom_op_final_gate(payload)

    assert result["passed"] is False
    assert any("adapter_evidence" in error for error in result["errors"])


def test_custom_op_final_gate_rejects_scalar_adapter_evidence() -> None:
    payload = _valid_custom_op_final_gate()
    rows = cast(list[dict[str, object]], payload["rows"])
    rows[0]["adapter_evidence"] = 1

    result = validate_custom_op_final_gate(payload)

    assert result["passed"] is False
    assert any("adapter_evidence" in error for error in result["errors"])


def test_custom_op_final_gate_rejects_non_passing_parity_numeric() -> None:
    payload = _valid_custom_op_final_gate()
    rows = cast(list[dict[str, object]], payload["rows"])
    rows[0]["parity_evidence"] = {"max_abs_error": 999.0, "tolerance": 1e-5}

    result = validate_custom_op_final_gate(payload)

    assert result["passed"] is False
    assert any("parity_evidence" in error for error in result["errors"])


def test_custom_op_final_gate_accepts_parity_within_tolerance() -> None:
    payload = _valid_custom_op_final_gate()
    rows = cast(list[dict[str, object]], payload["rows"])
    rows[0]["parity_evidence"] = {"max_abs_error": 1e-6, "tolerance": 1e-5}

    result = validate_custom_op_final_gate(payload)

    assert result["passed"] is False
    assert not any("parity_evidence" in error for error in result["errors"])


def test_custom_op_final_gate_rejects_row_count_mismatch() -> None:
    payload = _valid_custom_op_final_gate()
    payload["inventory_count"] = 2
    payload["manifest_entries"] = 2
    payload["closed_pass_entries"] = 2

    result = validate_custom_op_final_gate(payload)

    assert result["passed"] is False
    assert any("rows length must equal manifest_entries" in error for error in result["errors"])


@pytest.mark.parametrize(
    "evidence",
    [
        {"passed": False},
        {"present": False},
        {"status": "FAILED", "path": "opp/op_1"},
    ],
)
def test_custom_op_final_gate_rejects_explicitly_failed_evidence_dict(evidence: dict[str, object]) -> None:
    payload = _valid_custom_op_final_gate()
    rows = cast(list[dict[str, object]], payload["rows"])
    rows[0]["opp_custom_op_artifact_evidence"] = evidence

    result = validate_custom_op_final_gate(payload)

    assert result["passed"] is False
    assert any("opp_custom_op_artifact_evidence" in error for error in result["errors"])


def test_custom_op_final_gate_rejects_failed_no_fallback_evidence() -> None:
    payload = _valid_custom_op_final_gate()
    rows = cast(list[dict[str, object]], payload["rows"])
    rows[0]["no_fallback_no_zero_call_no_builtin_contamination"] = {"passed": False}

    result = validate_custom_op_final_gate(payload)

    assert result["passed"] is False
    assert any("no_fallback_no_zero_call_no_builtin_contamination" in error for error in result["errors"])


def test_custom_op_final_gate_rejects_passed_only_no_fallback_evidence() -> None:
    payload = _valid_custom_op_final_gate()
    rows = cast(list[dict[str, object]], payload["rows"])
    rows[0]["no_fallback_no_zero_call_no_builtin_contamination"] = {"passed": True}

    result = validate_custom_op_final_gate(payload)

    assert result["passed"] is False
    assert any("must explicitly set all" in error for error in result["errors"])


@pytest.mark.parametrize("status", ["PARTIAL", "MVP_ONLY", "SMOKE_ONLY"])
def test_custom_op_final_gate_rejects_partial_mvp_or_smoke_status(status: str) -> None:
    payload = _valid_custom_op_final_gate()
    payload["full_migration_status"] = status
    rows = cast(list[dict[str, object]], payload["rows"])
    rows[0]["status"] = status

    result = validate_custom_op_final_gate(payload)

    assert result["passed"] is False
    assert any(status in error for error in result["errors"])


def test_entry_script_validator_rejects_custom_op_contract_missing_critical_checks() -> None:
    payload = _valid_custom_op_contract()
    validation_obligations = cast(list[str], payload["validation_obligations"])
    payload["validation_obligations"] = [obligation for obligation in validation_obligations if obligation != "no_fallback"]

    result = validate_entry_script(payload)

    assert result["passed"] is False
    assert any("validation_obligations missing" in error for error in result["errors"])


def test_entry_script_validator_rejects_requirements_doc_discovery_source() -> None:
    payload = _valid_custom_op_contract()
    discovery_sources = cast(list[str], payload["operator_discovery_sources"])
    payload["operator_discovery_sources"] = [*discovery_sources, "requirements_doc"]

    result = validate_entry_script(payload)

    assert result["passed"] is False
    assert any("must be source-driven" in error for error in result["errors"])


def test_entry_script_validator_rejects_smoke_or_mvp_only_custom_op_contract() -> None:
    payload = _valid_custom_op_contract()
    payload["entry_script_path"] = "/tmp/project/smoke_test.py"
    payload["run_command"] = "python /tmp/project/smoke_test.py --mvp-only"
    payload["validation_obligations"] = ["mvp_smoke_only"]

    result = validate_entry_script(payload)

    assert result["passed"] is False
    assert any("smoke/MVP" in error for error in result["errors"])


def test_entry_script_validator_rejects_report_only_final_evidence_validator() -> None:
    payload = _valid_custom_op_contract()
    reports_dir = "/tmp/project/migration_reports"
    payload["entry_script_path"] = f"{reports_dir}/final_evidence_validate.py"
    payload["run_command"] = f"/tmp/project/.venv/bin/python {reports_dir}/final_evidence_validate.py"

    result = validate_entry_script(payload)

    assert result["passed"] is False
    assert any("report-only final evidence validator" in error for error in result["errors"])


def test_entry_script_validator_rejects_custom_op_benchmark_only_target() -> None:
    payload = _valid_custom_op_contract()
    payload["entry_script_path"] = "/tmp/project/benchmark_custom_ops.py"
    payload["run_command"] = "python /tmp/project/benchmark_custom_ops.py --benchmark-only"

    result = validate_entry_script(payload)

    assert result["passed"] is False
    assert any("benchmark-only" in error for error in result["errors"])


def test_entry_script_validator_rejects_missing_source_discovery_obligations() -> None:
    payload = _valid_custom_op_contract()
    payload["operator_discovery_sources"] = ["source", "bindings"]

    result = validate_entry_script(payload)

    assert result["passed"] is False
    assert any("operator_discovery_sources missing" in error for error in result["errors"])


def test_entry_script_validator_rejects_missing_report_and_check_obligations() -> None:
    payload = _valid_custom_op_contract()
    _ = payload.pop("required_report_paths")
    _ = payload.pop("required_checks")

    result = validate_entry_script(payload)

    assert result["passed"] is False
    assert any("required_report_paths must list" in error for error in result["errors"])
    assert any("required_checks must list" in error for error in result["errors"])


def test_entry_script_validator_rejects_missing_native_symbol_inventory_check() -> None:
    payload = _valid_custom_op_contract()
    checks = cast(list[str], payload["required_checks"])
    payload["required_checks"] = [check for check in checks if check != "native_operator_symbol_inventory"]

    result = validate_entry_script(payload)

    assert result["passed"] is False
    assert any("native_operator_symbol_inventory" in error for error in result["errors"])


def test_entry_script_validator_rejects_incomplete_inventory_schema() -> None:
    payload = _valid_custom_op_contract()
    payload["operator_inventory_schema"] = {"semantic_rows": "names only"}

    result = validate_entry_script(payload)

    assert result["passed"] is False
    assert any("operator_inventory_schema missing" in error for error in result["errors"])


def test_entry_static_validator_requires_passed_outputs_to_be_coherent() -> None:
    result = validate_entry_static(
        {
            "validation_passed": True,
            "issues": ["Line 9: input() blocks execution"],
            "fix_plan": "No issues found.",
        }
    )

    assert result["passed"] is False
    assert "validation_passed=true requires issues to be empty" in result["errors"]


def test_entry_static_validator_rejects_blank_passing_fix_plan() -> None:
    result = validate_entry_static(
        {
            "validation_passed": True,
            "issues": [],
            "fix_plan": " ",
        }
    )

    assert result["passed"] is False
    assert "fix_plan must be a non-empty string" in result["errors"]


def test_entry_static_validator_accepts_custom_op_booleans_when_all_true() -> None:
    result = validate_entry_static(
        {
            "validation_passed": True,
            "issues": [],
            "fix_plan": "Script satisfies the mandatory custom-op target.",
            "custom_op_static_required": True,
            "custom_op_requirements_checked": True,
            "script_source_driven_inventory": True,
            "script_emits_fine_grained_units": True,
            "script_maps_public_api_to_units": True,
            "script_discovers_full_inventory": True,
            "script_records_native_operator_symbols": True,
            "script_requires_strict_opp_producer_evidence": True,
            "script_rejects_non_opp_producer_success": True,
            "script_runs_project_api_custom_ops": True,
            "script_rejects_report_only_success": True,
            "script_requires_project_local_artifacts": True,
            "script_requires_project_root_artifact_existence": True,
            "script_requires_numeric_performance": True,
            "script_checks_no_fallback": True,
        }
    )

    assert result == {"passed": True, "errors": [], "warnings": []}


def test_entry_static_validator_rejects_failed_custom_op_boolean() -> None:
    result = validate_entry_static(
        {
            "validation_passed": True,
            "issues": [],
            "fix_plan": "Script was checked.",
            "custom_op_static_required": True,
            "custom_op_requirements_checked": True,
            "script_source_driven_inventory": True,
            "script_emits_fine_grained_units": True,
            "script_maps_public_api_to_units": True,
            "script_discovers_full_inventory": True,
            "script_records_native_operator_symbols": True,
            "script_requires_strict_opp_producer_evidence": True,
            "script_rejects_non_opp_producer_success": True,
            "script_runs_project_api_custom_ops": False,
            "script_rejects_report_only_success": True,
            "script_requires_project_local_artifacts": True,
            "script_requires_project_root_artifact_existence": True,
            "script_requires_numeric_performance": True,
            "script_checks_no_fallback": True,
        }
    )

    assert result["passed"] is False
    assert "script_runs_project_api_custom_ops must be true for custom-op static validation" in result["errors"]


def test_entry_static_validator_rejects_missing_native_symbol_inventory_boolean() -> None:
    result = validate_entry_static(
        {
            "validation_passed": True,
            "issues": [],
            "fix_plan": "Script was checked.",
            "custom_op_static_required": True,
            "custom_op_requirements_checked": True,
            "script_source_driven_inventory": True,
            "script_emits_fine_grained_units": True,
            "script_maps_public_api_to_units": True,
            "script_discovers_full_inventory": True,
            "script_runs_project_api_custom_ops": True,
            "script_requires_strict_opp_producer_evidence": True,
            "script_rejects_non_opp_producer_success": True,
            "script_rejects_report_only_success": True,
            "script_requires_project_local_artifacts": True,
            "script_requires_project_root_artifact_existence": True,
            "script_requires_numeric_performance": True,
            "script_checks_no_fallback": True,
        }
    )

    assert result["passed"] is False
    assert any("script_records_native_operator_symbols" in error for error in result["errors"])


def test_entry_static_validator_rejects_custom_marker_without_required_booleans() -> None:
    result = validate_entry_static(
        {
            "validation_passed": True,
            "issues": [],
            "fix_plan": "Script is headless-compliant.",
            "entry_script_kind": "custom_op_full_validation",
        }
    )

    assert result["passed"] is False
    assert any("custom-op static validation missing booleans" in error for error in result["errors"])


def test_entry_static_validator_rejects_custom_static_required_without_required_booleans() -> None:
    result = validate_entry_static(
        {
            "validation_passed": True,
            "issues": [],
            "fix_plan": "Script is headless-compliant.",
            "custom_op_static_required": True,
        }
    )

    assert result["passed"] is False
    assert any("custom-op static validation missing booleans" in error for error in result["errors"])


def test_entry_static_validator_requires_failing_outputs_to_name_issues() -> None:
    result = validate_entry_static(
        {
            "validation_passed": False,
            "issues": [],
            "fix_plan": "Add checks for every required migration report.",
        }
    )

    assert result["passed"] is False
    assert "validation_passed=false requires at least one issue" in result["errors"]


def test_entry_static_validator_preserves_failing_issue_errors() -> None:
    result = validate_entry_static(
        {
            "validation_passed": False,
            "issues": ["Line 12: smoke/MVP validation does not inspect migration_reports/ evidence"],
            "fix_plan": "Read all required reports and enforce every required custom-op check.",
        }
    )

    assert result == {
        "passed": False,
        "errors": ["Line 12: smoke/MVP validation does not inspect migration_reports/ evidence"],
        "warnings": [],
    }


@pytest.mark.parametrize(("validator_fn", "payload"), VALID_CASES)
def test_validators_accept_valid_data(validator_fn: ValidatorCallable, payload: dict[str, object]) -> None:
    result = validator_fn(payload)
    assert result == {"passed": True, "errors": [], "warnings": []}


@pytest.mark.parametrize(("validator_fn", "payload"), INVALID_CASES)
def test_validators_reject_invalid_data(validator_fn: ValidatorCallable, payload: dict[str, object]) -> None:
    result = validator_fn(payload)
    assert result["passed"] is False
    assert result["errors"]
    assert result["warnings"] == []


def test_validator_engine_normalizes_dict_results() -> None:
    engine = ValidatorEngine()
    engine.register_validator("env_detect", validate_env_detect)

    result = engine.validate(
        "env_detect",
        {"platform": "cuda", "npu_detected": False, "python_version": "3.11", "cann_version": "8.0.RC", "ascendc_available": True, "driver_version": "driver-1"},
    )

    assert result == ValidationResult(passed=True, errors=[], warnings=[])


def test_validator_engine_normalizes_boolean_results() -> None:
    engine = ValidatorEngine()
    
    def platform_validator(data: dict[str, object]) -> bool:
        return data.get("platform") in ("npu", "cuda")

    engine.register_validator("env_detect", platform_validator)

    result = engine.validate("env_detect", {})

    assert result.passed is False
    assert result.errors == ["Validator 'env_detect' reported failure."]


def test_validator_engine_handles_unregistered_validator() -> None:
    result = ValidatorEngine().validate("missing", {})

    assert result.passed is False
    assert result.errors == ["Validator 'missing' is not registered."]


@pytest.mark.parametrize(
    ("field_name", "bad_value"),
    [
        ("opp_custom_op_artifact_evidence", {"not_checked": True}),
        ("adapter_evidence", {"verified": False}),
        ("parity_evidence", {"status": "not checked"}),
        ("integration_e2e_evidence", {"failed": True}),
        ("same_run_runtime_coverage", {"custom_call_count": 0, "not_checked": True}),
        ("performance_evidence", {"speedup_vs_baseline": -0.2}),
        ("no_fallback_no_zero_call_no_builtin_contamination", {"passed": True, "fallback_detected": True}),
    ],
)
def test_custom_op_final_gate_rejects_nested_non_passing_evidence(field_name: str, bad_value: object) -> None:
    payload = _valid_custom_op_final_gate()
    rows = cast(list[dict[str, object]], payload["rows"])
    rows[0][field_name] = bad_value

    result = validate_custom_op_final_gate(payload)

    assert result["passed"] is False
    assert any(field_name in error for error in result["errors"])


def test_custom_op_final_gate_rejects_superficial_string_evidence() -> None:
    payload = _valid_custom_op_final_gate()
    rows = cast(list[dict[str, object]], payload["rows"])
    rows[0]["adapter_evidence"] = "looks good"

    result = validate_custom_op_final_gate(payload)

    assert result["passed"] is False
    assert any("adapter_evidence" in error for error in result["errors"])


def test_custom_op_final_gate_rejects_weak_numeric_no_fallback_evidence() -> None:
    payload = _valid_custom_op_final_gate()
    rows = cast(list[dict[str, object]], payload["rows"])
    rows[0]["no_fallback_no_zero_call_no_builtin_contamination"] = {"custom_call_count": 1}

    result = validate_custom_op_final_gate(payload)

    assert result["passed"] is False
    assert any("must explicitly set all" in error for error in result["errors"])


def test_custom_op_final_gate_rejects_deepwave_benchmark_only_without_source_inventory() -> None:
    payload = _valid_custom_op_final_gate()
    _ = payload.pop("source_inventory")
    rows = cast(list[dict[str, object]], payload["rows"])
    rows[0]["row_id"] = "ScalarFwd2D"
    rows[0]["integration_e2e_evidence"] = {"status": "PASS", "benchmark_only": True}
    rows[0]["same_run_runtime_coverage"] = {"custom_call_count": 1, "benchmark_only": True}
    rows[0]["performance_evidence"] = {"status": "PASS", "benchmark_only": True}

    result = validate_custom_op_final_gate(payload)

    assert result["passed"] is False
    assert any("source_inventory" in error for error in result["errors"])
    assert any("benchmark-only" in error for error in result["errors"])


def test_custom_op_final_gate_rejects_status_or_path_only_evidence() -> None:
    payload = _valid_custom_op_final_gate()
    rows = cast(list[dict[str, object]], payload["rows"])
    rows[0]["opp_custom_op_artifact_evidence"] = {"status": "PASS", "path": "opp/ScalarFwd2D"}

    result = validate_custom_op_final_gate(payload)

    assert result["passed"] is False
    assert any("opp_custom_op_artifact_evidence" in error for error in result["errors"])


def test_custom_op_final_gate_rejects_missing_numeric_performance_fields() -> None:
    payload = _valid_custom_op_final_gate()
    rows = cast(list[dict[str, object]], payload["rows"])
    rows[0]["performance_evidence"] = {"status": "PASS", "project_api_invoked": True}

    result = validate_custom_op_final_gate(payload)

    assert result["passed"] is False
    assert any("performance_evidence missing positive numeric fields" in error for error in result["errors"])


def test_custom_op_final_gate_rejects_source_inventory_mismatch() -> None:
    payload = _valid_custom_op_final_gate()
    payload["source_inventory"] = {
        "discovery_complete": True,
        "discovery_sources_checked": [
            "source",
            "bindings",
            "wrappers",
            "autograd",
            "aliases",
            "launch",
            "setup",
            "tests",
        ],
        "out_of_scope_source_groups": [],
        "entries": [
            {
                "name": "OtherOp",
                "native_operator_symbols": ["other_forward"],
                "kernel_functions": ["other_kernel"],
                "source_evidence": ["csrc/other.cpp"],
            }
        ],
    }

    result = validate_custom_op_final_gate(payload)

    assert result["passed"] is False
    assert any("source_inventory must match manifest rows" in error for error in result["errors"])


def test_custom_op_final_gate_rejects_source_inventory_without_discovery_metadata() -> None:
    payload = _valid_custom_op_final_gate()
    payload["source_inventory"] = [{"name": "ScalarFwd2D"}]

    result = validate_custom_op_final_gate(payload)

    assert result["passed"] is False
    assert any("discovery_complete" in error for error in result["errors"])


def test_custom_op_final_gate_rejects_requirements_doc_discovery_source() -> None:
    payload = _valid_custom_op_final_gate()
    source_inventory = cast(dict[str, object], payload["source_inventory"])
    discovery_sources = cast(list[str], source_inventory["discovery_sources_checked"])
    source_inventory["discovery_sources_checked"] = [*discovery_sources, "requirements_doc"]

    result = validate_custom_op_final_gate(payload)

    assert result["passed"] is False
    assert any("must be source-driven" in error for error in result["errors"])


def test_custom_op_final_gate_rejects_artifact_without_project_local_path_proof() -> None:
    payload = _valid_custom_op_final_gate()
    rows = cast(list[dict[str, object]], payload["rows"])
    rows[0]["opp_custom_op_artifact_evidence"] = {"project_local": True, "built": True, "path": "/tmp/outside/op"}

    result = validate_custom_op_final_gate(payload)

    assert result["passed"] is False
    assert any("project-local path proof" in error for error in result["errors"])


@pytest.mark.parametrize("unsafe_path", ["../outside/libop.so", "opp/../outside/libop.so", "file:///tmp/libop.so", "C:\\tmp\\libop.so"])
def test_custom_op_final_gate_rejects_unsafe_artifact_paths(unsafe_path: str) -> None:
    payload = _valid_custom_op_final_gate()
    rows = cast(list[dict[str, object]], payload["rows"])
    rows[0]["opp_custom_op_artifact_evidence"] = {
        "project_local": True,
        "built": True,
        "path": unsafe_path,
        "opp_custom_op_built": True,
    }

    result = validate_custom_op_final_gate(payload)

    assert result["passed"] is False
    assert any("project-local path proof" in error for error in result["errors"])
