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
    validate_serving_final_gate,
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
        "project_dir": project_dir,
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
            "candidate_public_api_routes": "candidate public API routes per row",
            "candidate_framework_integration_routes": "candidate framework integration routes per row",
            "route_evidence_fields": "final rows include public_api_route_evidence or framework_integration_route_evidence",
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
            "per_entry_public_api_or_framework_integration_route_evidence",
            "correlate_route_evidence_to_manifest_rows",
            "reject_direct_or_builtin_only_routes",
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
            "per_row_public_or_framework_route_evidence",
            "reject_direct_builtin_only_routes",
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
            "baseline_device": "cpu",
            "custom_device": "ascend_opp",
            "overall_baseline_seconds": 0.05,
            "overall_custom_seconds": 0.04,
            "overall_speedup_vs_baseline": 1.25,
            "overall_project_api_invoked": True,
            "overall_all_units_replaced": True,
            "overall_baseline_device": "cpu",
            "overall_custom_device": "ascend_opp",
            "entries": [
                {
                    "unit_identity": "ScalarFwd2D",
                    "baseline_seconds": 0.02,
                    "custom_seconds": 0.01,
                    "speedup_vs_baseline": 2.0,
                    "project_api_invoked": True,
                    "baseline_device": "cpu",
                    "custom_device": "ascend_opp",
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
                "public_api_route_evidence": {
                    "unit_identity": "ScalarFwd2D",
                    "route_type": "public_api",
                    "entrypoint": "deepwave.scalar",
                    "same_run": True,
                    "custom_call_count": 3,
                    "public_api_invoked": True,
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
                    "baseline_device": "cpu",
                    "custom_device": "ascend_opp",
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


def _write_generated_opp_unit(project_root: Path, snake_name: str, camel_name: str) -> None:
    header = project_root / "ascend_opp" / "custom_all" / "build_out" / "op_api" / "include" / f"aclnn_{snake_name}.h"
    kernel_dir = project_root / "ascend_opp" / "custom_all" / "build_out" / "_CPack_Packages" / "Linux" / "External" / "custom.run" / "packages" / "vendors" / "custom" / "op_impl" / "ai_core" / "tbe" / "kernel" / "ascend910b" / snake_name
    kernel_o = kernel_dir / f"{camel_name}_1234567890abcdef.o"
    kernel_json = kernel_dir / f"{camel_name}_1234567890abcdef.json"
    header.parent.mkdir(parents=True, exist_ok=True)
    kernel_dir.mkdir(parents=True, exist_ok=True)
    _ = header.write_text(f"aclnnStatus aclnn{camel_name}(void);\n", encoding="utf-8")
    _ = kernel_o.write_bytes(b"\x7fELF\x02\x01\x01\x00kernel_operator op_kernel")
    _ = kernel_json.write_text(json.dumps({"kernelName": f"{camel_name}_1234567890abcdef"}), encoding="utf-8")


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
        "native_operator_symbols": ["op_alpha_float32", "op_alpha_float16"],
        "kernel_launch_sites": ["csrc/op_alpha.cpp:op_alpha_float32_launch", "csrc/op_alpha.cpp:op_alpha_float16_launch"],
        "source_evidence": ["csrc/op_alpha.cpp:op_alpha_float32", "csrc/op_alpha.cpp:op_alpha_float16"],
        "negative_evidence": ["grep under src/ and tests/ found no additional operator families"],
        "dynamic_loading_checks": ["import torch.ops.op_alpha succeeded"],
        "build_load_checks": ["python setup.py build_ext --inplace completed"],
        "unresolved_source_groups": [],
        "out_of_scope_source_groups": [],
        "fine_grained_operator_unit_evidence": [
            {
                "unit_identity": "op_alpha:float32",
                "source_evidence": ["csrc/op_alpha.cpp:op_alpha_float32"],
                "candidate_public_api_routes": ["pkg.ops.alpha_float32"],
            },
            {
                "unit_identity": "op_alpha:float16",
                "source_evidence": ["csrc/op_alpha.cpp:op_alpha_float16"],
                "candidate_framework_integration_routes": ["pkg.layers.AlphaFloat16.forward"],
            },
        ],
    }


def _source_backed_variant_inventory_surface() -> dict[str, object]:
    surface = _valid_project_analysis_custom_op_surface()
    surface["operator_families"] = ["alpha", "beta"]
    surface["fine_grained_operator_units"] = ["alpha:forward_cuda", "beta:forward_cuda"]
    surface["discovered_operator_names"] = ["alpha_forward_cuda", "beta_forward_cuda"]
    surface["native_operator_symbols"] = ["alpha_${ndim}_${dtype}_forward_cuda", "beta_${ndim}_${dtype}_forward_cuda"]
    surface["kernel_launch_sites"] = ["src/alpha.cu:forward_kernel<<<...>>>", "src/beta.cu:forward_kernel<<<...>>>"]
    surface["source_evidence"] = [
        "src/backend.py:enumerates ndim 1, 2",
        "src/backend.py:enumerates dtype float and double",
        "src/backend.py:builds alpha and beta symbols with ${ndim} and ${dtype}",
    ]
    surface["dynamic_loading_checks"] = ["import torch.ops.alpha and torch.ops.beta succeed"]
    surface["build_load_checks"] = ["python setup.py build_ext --inplace completed"]
    surface["variant_axes_detected"] = True
    surface["variant_axes"] = {"ndim": ["1d", "2d"], "dtype": ["float", "double"], "device": ["cuda"]}
    surface["expanded_operator_variants"] = [
        {
            "unit_identity": "alpha:forward_cuda:ndim=1d:dtype=float:device=cuda",
            "base_unit_identity": "alpha:forward_cuda",
            "axis_values": {"ndim": "1d", "dtype": "float", "device": "cuda"},
            "source_evidence": ["src/backend.py:alpha template"],
            "candidate_public_api_routes": ["pkg.alpha.forward"],
        },
        {
            "unit_identity": "beta:forward_cuda:ndim=1d:dtype=float:device=cuda",
            "base_unit_identity": "beta:forward_cuda",
            "axis_values": {"ndim": "1d", "dtype": "float", "device": "cuda"},
            "source_evidence": ["src/backend.py:beta template"],
            "candidate_public_api_routes": ["pkg.beta.forward"],
        },
    ]
    surface["expanded_operator_instances_count"] = 2
    surface["fine_grained_operator_unit_evidence"] = [
        {
            "unit_identity": "alpha:forward_cuda",
            "source_evidence": ["src/backend.py:alpha template"],
            "candidate_public_api_routes": ["pkg.alpha.forward"],
        },
        {
            "unit_identity": "beta:forward_cuda",
            "source_evidence": ["src/backend.py:beta template"],
            "candidate_public_api_routes": ["pkg.beta.forward"],
        },
    ]
    return surface


def _axis_coverage_regression_surface() -> dict[str, object]:
    surface = _valid_project_analysis_custom_op_surface()
    units = [
        f"alpha:forward:ndim={ndim}:accuracy={accuracy}:dtype={dtype}:device=cuda"
        for ndim in ("1d", "2d", "3d")
        for accuracy in (2, 4, 6, 8)
        for dtype in ("float", "double")
    ]
    surface["operator_families"] = ["alpha"]
    surface["fine_grained_operator_units"] = units
    surface["discovered_operator_names"] = [unit.replace(":", "_").replace("=", "_") for unit in units]
    surface["native_operator_symbols"] = ["alpha_${ndim}_${accuracy}_${dtype}_forward_cuda"]
    surface["kernel_launch_sites"] = ["src/alpha.cu:forward_kernel<<<...>>>"]
    surface["source_evidence"] = ["src/backend_utils.py:1"]
    surface["variant_axes_detected"] = True
    surface["variant_axes"] = {"ndim": ["1d", "2d", "3d"], "accuracy": [2, 4, 6, 8], "dtype": ["float", "double"], "device": ["cuda"]}
    surface["expanded_operator_variants"] = [
        {
            "unit_identity": unit,
            "base_unit_identity": "alpha:forward_cuda",
            "axis_values": dict(part.split("=", 1) for part in unit.split(":")[2:]),
            "source_evidence": ["src/backend_utils.py:1"],
            "candidate_public_api_routes": ["pkg.alpha.forward"],
        }
        for unit in units
    ]
    surface["expanded_operator_instances_count"] = len(units)
    surface["fine_grained_operator_unit_evidence"] = [
        {
            "unit_identity": unit,
            "source_evidence": ["src/backend_utils.py:1"],
            "candidate_public_api_routes": ["pkg.alpha.forward"],
        }
        for unit in units
    ]

    return surface


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


def _valid_serving_surface(framework: str) -> dict[str, object]:
    return {
        "serving_framework": framework,
        "detection_complete": True,
        "launch_command": "python serve.py --model demo",
        "launch_evidence": ["serve.py:12 launches project serving runtime"],
        "project_demo_or_test_evidence": ["tests/test_api.py:8 validates serving endpoint"],
        "project_test_files": ["tests/test_api.py"],
        "readiness_probe": {"path": "/health", "expected_status": 200},
        "request_validation": {"path": "/v1/completions", "fixture": "tests/fixtures/request.json"},
        "expected_outputs": ["non-empty generated text"],
        "required_runtime_env": ["ASCEND_VISIBLE_DEVICES"],
        "unresolved_source_groups": [],
    }


@pytest.mark.parametrize(("route", "framework"), [("vllm_serving", "vllm"), ("sglang_serving", "sglang")])
def test_project_analysis_accepts_complete_serving_route_surface(route: str, framework: str) -> None:
    result = validate_project_analysis(
        {
            "project_dir": "/tmp/project",
            "dependencies": ["torch"],
            "cuda_detected": True,
            "entry_script": "serve.py",
            "migration_route": route,
            "serving_runtime_surface": _valid_serving_surface(framework),
        }
    )

    assert result == {"passed": True, "errors": [], "warnings": []}


def test_project_analysis_rejects_serving_route_missing_launch_or_demo_evidence() -> None:
    surface = _valid_serving_surface("vllm")
    surface["launch_evidence"] = []
    surface["project_demo_or_test_evidence"] = []

    result = validate_project_analysis(
        {
            "project_dir": "/tmp/project",
            "dependencies": ["torch"],
            "cuda_detected": True,
            "entry_script": "serve.py",
            "migration_route": "vllm_serving",
            "serving_runtime_surface": surface,
        }
    )

    assert result["passed"] is False
    assert any("launch_evidence" in error for error in result["errors"])
    assert any("project_demo_or_test_evidence" in error for error in result["errors"])


def test_project_analysis_rejects_serving_route_missing_readiness_request_outputs_or_runtime_env() -> None:
    surface = _valid_serving_surface("vllm")
    surface["readiness_probe"] = {}
    surface["request_validation"] = {}
    surface["expected_outputs"] = []
    surface["required_runtime_env"] = []

    result = validate_project_analysis(
        {
            "project_dir": "/tmp/project",
            "dependencies": ["torch"],
            "cuda_detected": True,
            "entry_script": "serve.py",
            "migration_route": "vllm_serving",
            "serving_runtime_surface": surface,
        }
    )

    assert result["passed"] is False
    assert any("readiness_probe" in error for error in result["errors"])
    assert any("request_validation" in error for error in result["errors"])
    assert any("expected_outputs" in error for error in result["errors"])
    assert any("required_runtime_env" in error for error in result["errors"])


def test_normalize_project_analysis_expanded_variants_synthesizes_complete_inventory() -> None:
    output: dict[str, object] = {"custom_op_surface": _source_backed_variant_inventory_surface()}

    from core.custom_op_variants import normalize_project_analysis_expanded_variants

    normalize_project_analysis_expanded_variants(output)

    surface = cast(dict[str, object], output["custom_op_surface"])
    variants = cast(list[dict[str, object]], surface["expanded_operator_variants"])
    unit_identities = {cast(str, variant["unit_identity"]) for variant in variants}

    assert len(variants) == 8
    assert surface["expanded_operator_instances_count"] == 8
    assert "alpha:forward_cuda:ndim=2d:dtype=double:device=cuda" in unit_identities
    assert "beta:forward_cuda:ndim=2d:dtype=double:device=cuda" in unit_identities


def test_normalize_project_analysis_augments_axis_values_from_source_template_rows() -> None:
    surface = _valid_project_analysis_custom_op_surface()
    surface["operator_families"] = ["solver"]
    surface["fine_grained_operator_units"] = ["solver:forward_cuda"]
    surface["discovered_operator_names"] = ["solver_forward_cuda"]
    surface["native_operator_symbols"] = ["solver_forward_cuda template accuracy={2,4,6,8}"]
    surface["kernel_launch_sites"] = ["src/solver.cu:SOLVER_FUNC(forward_cuda)"]
    surface["source_evidence"] = ["src/solver_codegen.py:builds solver forward_cuda with accuracy={2,4,6,8}"]
    surface["variant_axes_detected"] = True
    surface["variant_axes"] = {"accuracy": ["2"], "device": ["cuda"]}
    surface["expanded_operator_instances_count"] = 1
    surface["expanded_operator_variants"] = [
        {
            "unit_identity": "solver:forward_cuda:accuracy=2:device=cuda",
            "base_unit_identity": "solver:forward_cuda",
            "axis_values": {"accuracy": "2", "device": "cuda"},
            "source_evidence": ["src/solver.cu:SOLVER_FUNC(forward_cuda)"],
            "candidate_public_api_routes": ["pkg.solver.forward"],
        }
    ]
    surface["fine_grained_operator_unit_evidence"] = [
        {
            "unit_identity": "solver:forward_cuda",
            "source_evidence": ["src/solver_codegen.py:forward_cuda accuracy={2,4,6,8}"],
            "candidate_public_api_routes": ["pkg.solver.forward"],
        }
    ]
    output: dict[str, object] = {"custom_op_surface": surface}

    from core.custom_op_variants import normalize_project_analysis_expanded_variants

    normalize_project_analysis_expanded_variants(output)

    normalized = cast(dict[str, object], output["custom_op_surface"])
    variants = cast(list[dict[str, object]], normalized["expanded_operator_variants"])
    identities = {cast(str, variant["unit_identity"]) for variant in variants}
    normalized_axes = cast(dict[str, list[str]], normalized["variant_axes"])

    assert normalized_axes["accuracy"] == ["2", "4", "6", "8"]
    assert normalized["expanded_operator_instances_count"] == len(variants) == 4
    assert "solver:forward_cuda:accuracy=4:device=cuda" in identities
    assert "solver:forward_cuda:accuracy=6:device=cuda" in identities
    assert "solver:forward_cuda:accuracy=8:device=cuda" in identities


def test_normalize_project_analysis_augments_unbracketed_axis_lists_for_suffixed_operations() -> None:
    surface = _valid_project_analysis_custom_op_surface()
    surface["operator_families"] = ["adjoint"]
    surface["fine_grained_operator_units"] = ["adjoint:backward_extra_cuda"]
    surface["discovered_operator_names"] = ["adjoint_backward_extra_cuda"]
    surface["native_operator_symbols"] = ["adjoint_iso_<ndim>_<accuracy>_<dtype>_backward_extra_cuda"]
    surface["kernel_launch_sites"] = ["src/adjoint.cu:BACKWARD_EXTRA_FUNC(backward_extra)"]
    surface["source_evidence"] = [
        "src/backend.py:enumerates ndim 1,2,3; accuracy 2,4,6,8; dtype float,double for adjoint backward_extra functions",
        "src/backend.py:builds adjoint_iso_<ndim>_<accuracy>_<dtype>_backward_extra_cuda",
    ]
    surface["variant_axes_detected"] = True
    surface["variant_axes"] = {"ndim": ["1d", "2d", "3d"], "accuracy": ["2"], "dtype": ["float"], "device": ["cuda"]}
    surface["expanded_operator_instances_count"] = 1
    surface["expanded_operator_variants"] = [
        {
            "unit_identity": "adjoint:backward_extra_cuda:ndim=1d:accuracy=2:dtype=float:device=cuda",
            "base_unit_identity": "adjoint:backward_extra_cuda",
            "axis_values": {"ndim": "1d", "accuracy": "2", "dtype": "float", "device": "cuda"},
            "source_evidence": ["src/backend.py:adjoint backward_extra sample"],
            "candidate_public_api_routes": ["pkg.adjoint.backward_extra"],
        }
    ]
    surface["fine_grained_operator_unit_evidence"] = [
        {
            "unit_identity": "adjoint:backward_extra_cuda",
            "source_evidence": [
                "src/backend.py:enumerates ndim 1,2,3; accuracy 2,4,6,8; dtype float,double for adjoint backward_extra functions",
            ],
            "candidate_public_api_routes": ["pkg.adjoint.backward_extra"],
        }
    ]
    output: dict[str, object] = {"custom_op_surface": surface}

    from core.custom_op_variants import normalize_project_analysis_expanded_variants

    normalize_project_analysis_expanded_variants(output)

    normalized = cast(dict[str, object], output["custom_op_surface"])
    variants = cast(list[dict[str, object]], normalized["expanded_operator_variants"])
    identities = {cast(str, variant["unit_identity"]) for variant in variants}

    assert normalized["expanded_operator_instances_count"] == len(variants) == 24
    assert "adjoint:backward_extra_cuda:ndim=3d:accuracy=8:dtype=double:device=cuda" in identities


def test_project_analysis_accepts_complete_synthetic_expanded_variant_inventory() -> None:
    output: dict[str, object] = {"custom_op_surface": _source_backed_variant_inventory_surface()}

    from core.custom_op_variants import normalize_project_analysis_expanded_variants

    normalize_project_analysis_expanded_variants(output)

    result = validate_project_analysis(
        {
            "project_dir": "/tmp/project",
            "dependencies": ["torch"],
            "cuda_detected": True,
            "entry_script": "validate_custom_ops_full.py",
            "custom_op_surface": output["custom_op_surface"],
        }
    )

    assert result == {"passed": True, "errors": [], "warnings": []}


def test_normalize_project_analysis_canonicalizes_compact_cuda_func_units() -> None:
    surface = _valid_project_analysis_custom_op_surface()
    surface["operator_families"] = ["scalar"]
    surface["fine_grained_operator_units"] = ["scalar:forward", "scalar:backward"]
    surface["discovered_operator_names"] = ["scalar_forward", "scalar_backward"]
    surface["native_operator_symbols"] = ["scalar_${ndim}_${accuracy}_${dtype}_forward_cuda", "scalar_${ndim}_${accuracy}_${dtype}_backward_cuda"]
    surface["kernel_launch_sites"] = ["src/scalar.cu:FUNC(forward)", "src/scalar.cu:FUNC(backward)"]
    surface["source_evidence"] = [
        "src/backend.py:builds scalar symbols with ${ndim}, ${accuracy}, ${dtype}, forward_cuda/backward_cuda",
        "src/scalar.cu:FUNC(forward)",
        "src/scalar.cu:FUNC(backward)",
    ]
    surface["variant_axes_detected"] = True
    surface["variant_axes"] = {"ndim": ["1d", "2d"], "accuracy": [2, 4], "dtype": ["float", "double"], "device": ["cuda"]}
    surface["expanded_operator_instances_count"] = 16
    surface["expanded_operator_variants"] = [
        {
            "unit_identity": "scalar:forward:ndim=1d:accuracy=2:dtype=float:device=cuda",
            "base_unit_identity": "scalar:forward",
            "axis_values": {"ndim": "1d", "accuracy": "2", "dtype": "float", "device": "cuda"},
            "source_evidence": ["src/scalar.cu:FUNC(forward)"],
            "candidate_public_api_routes": ["pkg.scalar.forward"],
        },
        {
            "unit_identity": "scalar:backward:ndim=1d:accuracy=2:dtype=float:device=cuda",
            "base_unit_identity": "scalar:backward",
            "axis_values": {"ndim": "1d", "accuracy": "2", "dtype": "float", "device": "cuda"},
            "source_evidence": ["src/scalar.cu:FUNC(backward)"],
            "candidate_framework_integration_routes": ["ScalarForward.backward"],
        },
    ]
    surface["fine_grained_operator_unit_evidence"] = [
        {"unit_identity": "scalar:forward", "source_evidence": ["src/scalar.cu:FUNC(forward)"], "candidate_public_api_routes": ["pkg.scalar.forward"]},
        {"unit_identity": "scalar:backward", "source_evidence": ["src/scalar.cu:FUNC(backward)"], "candidate_framework_integration_routes": ["ScalarForward.backward"]},
    ]
    surface["discovery_complete"] = False
    surface["unresolved_source_groups"] = ["Full concrete generated-symbol inventory is source-required, but no build manifest proves which generated combinations are built"]
    output: dict[str, object] = {"custom_op_surface": surface}

    from core.custom_op_variants import normalize_project_analysis_expanded_variants

    normalize_project_analysis_expanded_variants(output)

    normalized = cast(dict[str, object], output["custom_op_surface"])
    variants = cast(list[dict[str, object]], normalized["expanded_operator_variants"])
    identities = {cast(str, variant["unit_identity"]) for variant in variants}

    assert normalized["fine_grained_operator_units"] == ["scalar:forward_cuda", "scalar:backward_cuda"]
    assert normalized["discovery_complete"] is True
    assert normalized["unresolved_source_groups"] == []
    assert cast(dict[str, object], normalized["variant_axes"])["device"] == ["cuda"]
    assert normalized["expanded_operator_instances_count"] == 16
    assert len(variants) == 16
    assert "scalar:forward_cuda:ndim=2d:accuracy=4:dtype=double:device=cuda" in identities
    assert "scalar:backward_cuda:ndim=2d:accuracy=4:dtype=double:device=cuda" in identities


def test_normalize_project_analysis_dedupes_compact_and_cuda_sibling_units() -> None:
    surface = _valid_project_analysis_custom_op_surface()
    surface["operator_families"] = ["wave"]
    surface["fine_grained_operator_units"] = ["wave:forward", "wave:forward_cuda", "wave:forward_cuda"]
    surface["discovered_operator_names"] = ["wave_forward", "wave_forward_cuda", "wave_forward_cuda_cuda"]
    surface["native_operator_symbols"] = ["wave_${ndim}_${dtype}_forward_cuda"]
    surface["kernel_launch_sites"] = ["src/wave.cu:FUNC(forward)"]
    surface["source_evidence"] = [
        "src/backend.py:builds wave forward symbols with ${ndim}, ${dtype}, and device cuda",
        "src/wave.cu:FUNC(forward)",
    ]
    surface["variant_axes_detected"] = True
    surface["variant_axes"] = {"ndim": ["1", "2"], "dtype": ["float", "double"], "device": ["cuda"]}
    surface["expanded_operator_instances_count"] = 8
    surface["expanded_operator_variants"] = [
        {
            "unit_identity": "wave:forward_cuda:ndim=1:dtype=float:device=cuda",
            "base_unit_identity": "wave:forward_cuda",
            "axis_values": {"ndim": "1", "dtype": "float", "device": "cuda"},
            "source_evidence": ["src/wave.cu:FUNC(forward)"],
            "candidate_public_api_routes": ["pkg.wave.forward"],
        },
        {
            "unit_identity": "wave:forward:ndim=1:dtype=float:device=cuda",
            "base_unit_identity": "wave:forward",
            "axis_values": {"ndim": "1", "dtype": "float", "device": "cuda"},
            "source_evidence": ["src/backend.py:get_backend_function wave forward"],
            "candidate_public_api_routes": ["pkg.wave.forward"],
        },
    ]
    surface["fine_grained_operator_unit_evidence"] = [
        {
            "unit_identity": "wave:forward",
            "source_evidence": ["src/backend.py:get_backend_function('wave', ndim, 'forward', dtype, device)"],
            "candidate_public_api_routes": ["pkg.wave.forward"],
        },
        {
            "unit_identity": "wave:forward_cuda",
            "source_evidence": ["src/wave.cu:FUNC(forward)"],
            "candidate_public_api_routes": ["pkg.wave.forward"],
        },
    ]
    output: dict[str, object] = {"custom_op_surface": surface}

    from core.custom_op_variants import normalize_project_analysis_expanded_variants

    normalize_project_analysis_expanded_variants(output)

    normalized = cast(dict[str, object], output["custom_op_surface"])
    variants = cast(list[dict[str, object]], normalized["expanded_operator_variants"])
    identities = [cast(str, variant["unit_identity"]) for variant in variants]

    assert normalized["fine_grained_operator_units"] == ["wave:forward_cuda"]
    assert normalized["expanded_operator_instances_count"] == 4
    assert len(identities) == len(set(identities)) == 4
    assert all("forward_cuda_cuda" not in value for value in cast(list[str], normalized["discovered_operator_names"]))
    assert "wave:forward_cuda:ndim=2:dtype=double:device=cuda" in identities


def test_normalize_project_analysis_drops_cpu_helpers_when_target_sibling_exists() -> None:
    surface = _valid_project_analysis_custom_op_surface()
    surface["operator_families"] = ["storage", "simple_compress"]
    surface["fine_grained_operator_units"] = [
        "storage:save_snapshot_cpu",
        "storage:save_snapshot_gpu",
        "simple_compress:compress_cpu",
        "simple_compress:compress_cuda",
    ]
    surface["discovered_operator_names"] = [
        "storage_save_snapshot_cpu",
        "storage_save_snapshot_gpu",
        "simple_compress_compress_cpu",
        "simple_compress_compress_cuda",
    ]
    surface["native_operator_symbols"] = [
        "storage_${ndim}_${dtype}_save_snapshot_gpu",
        "simple_compress_${ndim}_${dtype}_compress_cuda",
    ]
    surface["kernel_launch_sites"] = ["src/storage_utils.h:STORAGE_FUNC(save_snapshot_gpu)", "src/simple_compress.h:SC_FUNC(compress_cuda)"]
    surface["source_evidence"] = [
        "src/storage_utils.h:declares save_snapshot_cpu and save_snapshot_gpu",
        "src/simple_compress.h:declares compress_cpu and compress_cuda",
    ]
    surface["variant_axes_detected"] = True
    surface["variant_axes"] = {"ndim": ["1", "2", "3"], "dtype": ["float", "double"], "device": ["cpu", "cuda", "gpu"]}
    surface["expanded_operator_instances_count"] = 36
    surface["expanded_operator_variants"] = [
        {
            "unit_identity": f"storage:save_snapshot_cpu:ndim={ndim}:dtype={dtype}:device=cpu",
            "base_unit_identity": "storage:save_snapshot_cpu",
            "axis_values": {"ndim": ndim, "dtype": dtype, "device": "cpu"},
            "source_evidence": ["src/storage_utils.h:save_snapshot_cpu"],
            "candidate_framework_integration_routes": ["storage path"],
        }
        for ndim in ["1", "2", "3"]
        for dtype in ["float", "double"]
    ] + [
        {
            "unit_identity": f"storage:save_snapshot_gpu:ndim={ndim}:dtype={dtype}:device=gpu",
            "base_unit_identity": "storage:save_snapshot_gpu",
            "axis_values": {"ndim": ndim, "dtype": dtype, "device": "gpu"},
            "source_evidence": ["src/storage_utils.h:save_snapshot_gpu"],
            "candidate_framework_integration_routes": ["storage path"],
        }
        for ndim in ["1", "2", "3"]
        for dtype in ["float", "double"]
    ]
    surface["fine_grained_operator_unit_evidence"] = [
        {"unit_identity": unit, "source_evidence": [f"src/helpers.h:{unit}"], "candidate_framework_integration_routes": ["helper path"]}
        for unit in surface["fine_grained_operator_units"]
    ]
    output: dict[str, object] = {"custom_op_surface": surface}

    from core.custom_op_variants import normalize_project_analysis_expanded_variants

    normalize_project_analysis_expanded_variants(output)

    normalized = cast(dict[str, object], output["custom_op_surface"])
    variants = cast(list[dict[str, object]], normalized["expanded_operator_variants"])
    base_units = set(cast(list[str], normalized["fine_grained_operator_units"]))

    assert "storage:save_snapshot_cpu" not in base_units
    assert "simple_compress:compress_cpu" not in base_units
    assert "storage:save_snapshot_gpu" in base_units
    assert "simple_compress:compress_cuda" in base_units
    assert all("device=cpu" not in cast(str, variant["unit_identity"]) for variant in variants)


def test_normalize_project_analysis_canonicalizes_storage_utils_helper_family() -> None:
    surface = _valid_project_analysis_custom_op_surface()
    surface["operator_families"] = ["storage", "simple_compress"]
    surface["fine_grained_operator_units"] = [
        "storage_utils:save_snapshot_gpu",
        "storage_utils:load_snapshot_gpu",
        "simple_compress:compress_cuda",
        "simple_compress:decompress_cuda",
    ]
    surface["discovered_operator_names"] = [
        "storage_utils_save_snapshot_gpu",
        "storage_utils_load_snapshot_gpu",
        "simple_compress_compress_cuda",
        "simple_compress_decompress_cuda",
    ]
    surface["native_operator_symbols"] = [
        "storage_${ndim}_${dtype}_save_snapshot_gpu",
        "storage_${ndim}_${dtype}_load_snapshot_gpu",
        "simple_compress_${ndim}_${dtype}_compress_cuda",
        "simple_compress_${ndim}_${dtype}_decompress_cuda",
    ]
    surface["kernel_launch_sites"] = [
        "src/storage_utils.cu:STORAGE_FUNC(save_snapshot_gpu) calls simple_compress:compress_cuda",
        "src/storage_utils.cu:STORAGE_FUNC(load_snapshot_gpu) calls simple_compress:decompress_cuda",
        "src/simple_compress.cu:SC_FUNC(compress_cuda)",
        "src/simple_compress.cu:SC_FUNC(decompress_cuda)",
    ]
    surface["source_evidence"] = [
        "src/backend.py:builds storage/simple_compress symbols with ${ndim} and ${dtype}",
        "src/backend.py:enumerates ndim 1, 2, 3",
        "src/backend.py:enumerates dtype float and double",
        "src/storage_utils.cu:STORAGE_FUNC(save_snapshot_gpu)",
        "src/storage_utils.cu:STORAGE_FUNC(load_snapshot_gpu)",
        "src/simple_compress.cu:SC_FUNC(compress_cuda)",
        "src/simple_compress.cu:SC_FUNC(decompress_cuda)",
    ]
    surface["variant_axes_detected"] = True
    surface["variant_axes"] = {"ndim": ["1", "2", "3"], "dtype": ["float", "double"]}
    surface["expanded_operator_instances_count"] = 12
    surface["expanded_operator_variants"] = [
        {
            "unit_identity": "storage_utils:save_snapshot_gpu:ndim=1:dtype=float",
            "base_unit_identity": "storage_utils:save_snapshot_gpu",
            "axis_values": {"ndim": "1", "dtype": "float"},
            "source_evidence": ["src/storage_utils.cu:STORAGE_FUNC(save_snapshot_gpu)"],
            "candidate_framework_integration_routes": ["src/wrapper.py:snapshot save path"],
        },
        {
            "unit_identity": "simple_compress:compress_cuda:ndim=1:dtype=float",
            "base_unit_identity": "simple_compress:compress_cuda",
            "axis_values": {"ndim": "1", "dtype": "float"},
            "source_evidence": ["src/simple_compress.cu:SC_FUNC(compress_cuda)"],
            "candidate_framework_integration_routes": ["compression path"],
        },
    ]
    surface["fine_grained_operator_unit_evidence"] = [
        {
            "unit_identity": "storage_utils:save_snapshot_gpu",
            "source_evidence": ["src/storage_utils.cu:STORAGE_FUNC(save_snapshot_gpu)"],
            "candidate_framework_integration_routes": ["snapshot save path"],
        },
        {
            "unit_identity": "storage_utils:load_snapshot_gpu",
            "source_evidence": ["src/storage_utils.cu:STORAGE_FUNC(load_snapshot_gpu)"],
            "candidate_framework_integration_routes": ["src/wrapper.py:snapshot load path"],
        },
        {
            "unit_identity": "simple_compress:compress_cuda",
            "source_evidence": ["src/simple_compress.cu:SC_FUNC(compress_cuda)"],
            "candidate_framework_integration_routes": ["compression path"],
        },
        {
            "unit_identity": "simple_compress:decompress_cuda",
            "source_evidence": ["src/simple_compress.cu:SC_FUNC(decompress_cuda)"],
            "candidate_framework_integration_routes": ["decompression path"],
        },
    ]
    surface["discovery_complete"] = False
    surface["unresolved_source_groups"] = ["Generated symbol inventory has no build manifest proof"]
    output: dict[str, object] = {"custom_op_surface": surface}

    from core.custom_op_variants import normalize_project_analysis_expanded_variants

    normalize_project_analysis_expanded_variants(output)

    normalized = cast(dict[str, object], output["custom_op_surface"])
    variants = cast(list[dict[str, object]], normalized["expanded_operator_variants"])
    identities = {cast(str, variant["unit_identity"]) for variant in variants}

    assert normalized["fine_grained_operator_units"] == [
        "storage:save_snapshot_gpu",
        "storage:load_snapshot_gpu",
        "simple_compress:compress_cuda",
        "simple_compress:decompress_cuda",
    ]
    assert normalized["discovery_complete"] is True
    assert normalized["unresolved_source_groups"] == []
    assert normalized["expanded_operator_instances_count"] == 24
    assert len(variants) == 24
    assert "storage:load_snapshot_gpu:ndim=3:dtype=double" in identities
    assert "simple_compress:decompress_cuda:ndim=3:dtype=double" in identities


def test_normalize_project_analysis_expands_storage_helpers_from_path_only_unit_evidence(tmp_path: Path) -> None:
    source_root = tmp_path / "src"
    source_root.mkdir()
    _ = (source_root / "storage_utils.h").write_text(
        (
            "#if defined(DW_NDIM) && defined(DW_DTYPE)\n"
            "#define STORAGE_FUNC(name) storage_##name##_##DW_NDIM##d_##DW_DTYPE\n"
            "int STORAGE_FUNC(save_snapshot_gpu)(void);\n"
            "int STORAGE_FUNC(load_snapshot_gpu)(void);\n"
            "#endif\n"
        ),
        encoding="utf-8",
    )
    _ = (source_root / "storage_utils.cu").write_text(
        (
            "#include \"storage_utils.h\"\n"
            "#if defined(DW_NDIM) && defined(DW_DTYPE)\n"
            "int STORAGE_FUNC(save_snapshot_gpu)(void) { return 0; }\n"
            "int STORAGE_FUNC(load_snapshot_gpu)(void) { return 0; }\n"
            "#endif\n"
        ),
        encoding="utf-8",
    )
    _ = (source_root / "wrapper.py").write_text("accuracy = [2, 4]\n", encoding="utf-8")
    surface = _valid_project_analysis_custom_op_surface()
    surface["operator_families"] = ["storage"]
    surface["fine_grained_operator_units"] = ["storage:save_snapshot_gpu", "storage:load_snapshot_gpu"]
    surface["discovered_operator_names"] = ["save_snapshot_gpu", "load_snapshot_gpu"]
    surface["native_operator_symbols"] = ["save_snapshot_gpu", "load_snapshot_gpu"]
    surface["kernel_launch_sites"] = ["src/storage_utils.cu:13", "src/storage_utils.cu:65"]
    surface["source_evidence"] = ["src/storage_utils.cu", "src/storage_utils.h"]
    surface["variant_axes_detected"] = True
    surface["variant_axes"] = {"ndim": ["1", "2", "3"], "accuracy": ["2", "4"], "dtype": ["float", "double"], "device": ["cuda"]}
    surface["expanded_operator_instances_count"] = 0
    surface["expanded_operator_variants"] = []
    surface["fine_grained_operator_unit_evidence"] = [
        {
            "unit_identity": "storage:save_snapshot_gpu",
            "source_evidence": ["src/storage_utils.cu:13", "src/storage_utils.h declares extern C storage helpers"],
            "candidate_framework_integration_routes": ["snapshot save path"],
        },
        {
            "unit_identity": "storage:load_snapshot_gpu",
            "source_evidence": ["src/storage_utils.cu:65", "src/storage_utils.h declares extern C storage helpers"],
            "candidate_framework_integration_routes": ["snapshot load path"],
        },
    ]
    output: dict[str, object] = {"project_dir": str(tmp_path), "custom_op_surface": surface}

    from core.custom_op_variants import normalize_project_analysis_expanded_variants

    normalize_project_analysis_expanded_variants(output)

    normalized = cast(dict[str, object], output["custom_op_surface"])
    variants = cast(list[dict[str, object]], normalized["expanded_operator_variants"])
    identities = {cast(str, variant["unit_identity"]) for variant in variants}
    counts_by_base: dict[str, int] = {}
    for variant in variants:
        base = cast(str, variant["base_unit_identity"])
        counts_by_base[base] = counts_by_base.get(base, 0) + 1

    assert counts_by_base == {"storage:save_snapshot_gpu": 6, "storage:load_snapshot_gpu": 6}
    assert normalized["expanded_operator_instances_count"] == 12
    assert "storage:save_snapshot_gpu:ndim=3:dtype=double:device=gpu" in identities
    assert "storage:load_snapshot_gpu:ndim=3:dtype=double:device=gpu" in identities
    assert all("accuracy=" not in identity for identity in identities)
    assert all("device=cpu" not in identity for identity in identities)


def test_normalize_project_analysis_does_not_apply_unmentioned_accuracy_axis_to_helpers() -> None:
    surface = _valid_project_analysis_custom_op_surface()
    surface["operator_families"] = ["wave", "storage", "simple_compress"]
    surface["fine_grained_operator_units"] = [
        "wave:forward_cuda",
        "storage:load_snapshot_gpu",
        "simple_compress:decompress_cuda",
    ]
    surface["discovered_operator_names"] = [
        "wave_forward_cuda",
        "storage_load_snapshot_gpu",
        "simple_compress_decompress_cuda",
    ]
    surface["native_operator_symbols"] = [
        "wave_iso_${ndim}d_${accuracy}_${dtype}_forward_cuda",
        "storage_${ndim}d_${dtype}_load_snapshot_gpu",
        "simple_compress_${ndim}d_${dtype}_decompress_cuda",
    ]
    surface["kernel_launch_sites"] = [
        "src/wave.cu:FUNC(forward)",
        "src/storage_utils.cu:STORAGE_FUNC(load_snapshot_gpu)",
        "src/simple_compress.cu:SC_FUNC(decompress_cuda)",
    ]
    surface["source_evidence"] = [
        "src/backend.py:builds wave symbols with ${ndim}, ${accuracy}, ${dtype}, and device cuda",
        "src/backend.py:enumerates ndim 1, 2, 3",
        "src/backend.py:enumerates accuracy 2, 4, 6, 8",
        "src/backend.py:enumerates dtype float and double",
        "src/storage_utils.h:STORAGE_FUNC expands storage helpers with ${ndim} and ${dtype}",
        "src/simple_compress.h:SC_FUNC expands compression helpers with ${ndim} and ${dtype}",
    ]
    surface["variant_axes_detected"] = True
    surface["variant_axes"] = {"ndim": ["1", "2", "3"], "accuracy": ["2", "4", "6", "8"], "dtype": ["float", "double"], "device": ["cuda", "gpu"]}
    surface["expanded_operator_instances_count"] = 36
    surface["expanded_operator_variants"] = [
        {
            "unit_identity": "wave:forward_cuda:ndim=1:accuracy=2:dtype=float:device=cuda",
            "base_unit_identity": "wave:forward_cuda",
            "axis_values": {"ndim": "1", "accuracy": "2", "dtype": "float", "device": "cuda"},
            "source_evidence": ["src/wave.cu:FUNC(forward)"],
            "candidate_public_api_routes": ["pkg.wave.forward"],
        },
        {
            "unit_identity": "storage:load_snapshot_gpu:ndim=1:dtype=float",
            "base_unit_identity": "storage:load_snapshot_gpu",
            "axis_values": {"ndim": "1", "dtype": "float"},
            "source_evidence": ["src/storage_utils.h:STORAGE_FUNC(load_snapshot_gpu)"],
            "candidate_framework_integration_routes": ["snapshot load path"],
        },
        {
            "unit_identity": "simple_compress:decompress_cuda:ndim=1:dtype=float",
            "base_unit_identity": "simple_compress:decompress_cuda",
            "axis_values": {"ndim": "1", "dtype": "float"},
            "source_evidence": ["src/simple_compress.h:SC_FUNC(decompress_cuda)"],
            "candidate_framework_integration_routes": ["decompression path"],
        },
    ]
    surface["fine_grained_operator_unit_evidence"] = [
        {
            "unit_identity": "wave:forward_cuda",
            "source_evidence": ["src/backend.py:wave symbol uses ndim accuracy dtype", "src/wave.cu:FUNC(forward)"],
            "candidate_public_api_routes": ["pkg.wave.forward"],
        },
        {
            "unit_identity": "storage:load_snapshot_gpu",
            "source_evidence": ["src/storage_utils.h:STORAGE_FUNC(load_snapshot_gpu) uses ndim dtype"],
            "candidate_framework_integration_routes": ["snapshot load path"],
        },
        {
            "unit_identity": "simple_compress:decompress_cuda",
            "source_evidence": ["src/simple_compress.h:SC_FUNC(decompress_cuda) uses ndim dtype"],
            "candidate_framework_integration_routes": ["decompression path"],
        },
    ]
    output: dict[str, object] = {"custom_op_surface": surface}

    from core.custom_op_variants import normalize_project_analysis_expanded_variants

    normalize_project_analysis_expanded_variants(output)

    normalized = cast(dict[str, object], output["custom_op_surface"])
    variants = cast(list[dict[str, object]], normalized["expanded_operator_variants"])
    counts_by_base: dict[str, int] = {}
    for variant in variants:
        base = cast(str, variant["base_unit_identity"])
        counts_by_base[base] = counts_by_base.get(base, 0) + 1

    assert normalized["expanded_operator_instances_count"] == 36
    assert counts_by_base == {
        "wave:forward_cuda": 24,
        "storage:load_snapshot_gpu": 6,
        "simple_compress:decompress_cuda": 6,
    }
    assert all("accuracy" not in cast(dict[str, str], variant["axis_values"]) for variant in variants if variant["base_unit_identity"] != "wave:forward_cuda")


def test_normalize_project_analysis_uses_base_matched_rows_for_misordered_variant_evidence() -> None:
    surface = _valid_project_analysis_custom_op_surface()
    surface["operator_families"] = ["flux", "tensor_store", "block_compress"]
    surface["fine_grained_operator_units"] = [
        "flux:propagate_cuda",
        "tensor_store:cache_gpu",
        "block_compress:pack_cuda",
    ]
    surface["discovered_operator_names"] = [
        "tensor_store_cache_gpu",
        "flux_propagate_cuda",
        "block_compress_pack_cuda",
    ]
    surface["native_operator_symbols"] = [
        "tensor_store_${ndim}d_${dtype}_cache_gpu",
        "flux_${ndim}d_${accuracy}_${dtype}_propagate_cuda",
        "block_compress_${ndim}d_${dtype}_pack_cuda",
    ]
    surface["kernel_launch_sites"] = [
        "src/flux.cu:PROPAGATE_FUNC(propagate_cuda)",
        "src/tensor_store.cu:STORE_FUNC(cache_gpu)",
        "src/block_compress.cu:COMPRESS_FUNC(pack_cuda)",
    ]
    surface["source_evidence"] = [
        "src/tensor_store.h:STORE_FUNC expands tensor_store cache_gpu with ${ndim} and ${dtype}",
        "src/flux_codegen.py:builds flux propagate_cuda symbols with ${ndim}, ${accuracy}, and ${dtype}",
        "src/block_compress.h:COMPRESS_FUNC expands block_compress pack_cuda with ${ndim} and ${dtype}",
    ]
    surface["variant_axes_detected"] = True
    surface["variant_axes"] = {"ndim": ["1", "2", "3"], "accuracy": ["2", "4", "6", "8"], "dtype": ["float", "double"], "device": ["cuda", "gpu"]}
    surface["expanded_operator_instances_count"] = 0
    surface["expanded_operator_variants"] = []
    surface["fine_grained_operator_unit_evidence"] = [
        {"unit_identity": "block_compress:pack_cuda", "source_evidence": ["src/block_compress.h:COMPRESS_FUNC(pack_cuda)"], "candidate_framework_integration_routes": ["compress path"]},
        {"unit_identity": "tensor_store:cache_gpu", "source_evidence": ["src/tensor_store.h:STORE_FUNC(cache_gpu)"], "candidate_framework_integration_routes": ["cache path"]},
        {"unit_identity": "flux:propagate_cuda", "source_evidence": ["src/flux.cu:PROPAGATE_FUNC(propagate_cuda)"], "candidate_public_api_routes": ["pkg.flux.propagate"]},
    ]
    output: dict[str, object] = {"custom_op_surface": surface}

    from core.custom_op_variants import normalize_project_analysis_expanded_variants

    normalize_project_analysis_expanded_variants(output)

    normalized = cast(dict[str, object], output["custom_op_surface"])
    variants = cast(list[dict[str, object]], normalized["expanded_operator_variants"])
    axes_by_base: dict[str, set[str]] = {}
    counts_by_base: dict[str, int] = {}
    for variant in variants:
        base = cast(str, variant["base_unit_identity"])
        axes_by_base.setdefault(base, set()).update(cast(dict[str, str], variant["axis_values"]))
        counts_by_base[base] = counts_by_base.get(base, 0) + 1

    assert counts_by_base == {
        "flux:propagate_cuda": 24,
        "tensor_store:cache_gpu": 6,
        "block_compress:pack_cuda": 6,
    }
    assert axes_by_base == {
        "flux:propagate_cuda": {"ndim", "accuracy", "dtype", "device"},
        "tensor_store:cache_gpu": {"ndim", "dtype", "device"},
        "block_compress:pack_cuda": {"ndim", "dtype", "device"},
    }
    assert all(
        "accuracy" not in cast(dict[str, str], variant["axis_values"])
        for variant in variants
        if variant["base_unit_identity"] != "flux:propagate_cuda"
    )


def test_normalize_project_analysis_uses_family_template_evidence_for_paired_helpers() -> None:
    surface = _valid_project_analysis_custom_op_surface()
    surface["operator_families"] = ["tensor_cache"]
    surface["fine_grained_operator_units"] = [
        "tensor_cache:save_tile_gpu",
        "tensor_cache:load_tile_gpu",
    ]
    surface["discovered_operator_names"] = [
        "tensor_cache_save_tile_gpu",
        "tensor_cache_load_tile_gpu",
    ]
    surface["native_operator_symbols"] = [
        "tensor_cache_save_tile_gpu_*d_*",
        "tensor_cache_load_tile_gpu_*d_*",
    ]
    surface["source_evidence"] = [
        "src/tensor_cache.h:cache save/load gpu variants expand over ndim and dtype",
    ]
    surface["variant_axes_detected"] = True
    surface["variant_axes"] = {"ndim": ["1", "2", "3"], "dtype": ["float", "double"], "device": ["gpu"]}
    surface["expanded_operator_instances_count"] = 6
    surface["expanded_operator_variants"] = [
        {
            "unit_identity": f"tensor_cache:load_tile_gpu:ndim={ndim}:device=gpu",
            "base_unit_identity": "tensor_cache:load_tile_gpu",
            "axis_values": {"ndim": ndim, "device": "gpu"},
            "source_evidence": ["src/tensor_cache.h:TENSOR_CACHE_FUNC(load_tile_gpu)"],
            "candidate_framework_integration_routes": ["cache load path"],
        }
        for ndim in ["1", "2", "3"]
    ] + [
        {
            "unit_identity": f"tensor_cache:save_tile_gpu:ndim={ndim}:device=gpu",
            "base_unit_identity": "tensor_cache:save_tile_gpu",
            "axis_values": {"ndim": ndim, "device": "gpu"},
            "source_evidence": ["src/tensor_cache.h:TENSOR_CACHE_FUNC(save_tile_gpu)"],
            "candidate_framework_integration_routes": ["cache save path"],
        }
        for ndim in ["1", "2", "3"]
    ]
    surface["fine_grained_operator_unit_evidence"] = [
        {"unit_identity": "tensor_cache:save_tile_gpu", "source_evidence": ["src/tensor_cache.h:TENSOR_CACHE_FUNC(save_tile_gpu)"], "candidate_framework_integration_routes": ["cache save path"]},
        {"unit_identity": "tensor_cache:load_tile_gpu", "source_evidence": ["src/tensor_cache.h:TENSOR_CACHE_FUNC(load_tile_gpu)"], "candidate_framework_integration_routes": ["cache load path"]},
    ]
    output: dict[str, object] = {"custom_op_surface": surface}

    from core.custom_op_variants import normalize_project_analysis_expanded_variants

    normalize_project_analysis_expanded_variants(output)

    normalized = cast(dict[str, object], output["custom_op_surface"])
    variants = cast(list[dict[str, object]], normalized["expanded_operator_variants"])
    counts_by_base: dict[str, int] = {}
    for variant in variants:
        base = cast(str, variant["base_unit_identity"])
        counts_by_base[base] = counts_by_base.get(base, 0) + 1

    assert normalized["expanded_operator_instances_count"] == 12
    assert counts_by_base == {
        "tensor_cache:save_tile_gpu": 6,
        "tensor_cache:load_tile_gpu": 6,
    }
    assert any(
        cast(str, variant["unit_identity"]) == "tensor_cache:load_tile_gpu:ndim=3:dtype=double:device=gpu"
        for variant in variants
    )


def test_normalize_project_analysis_expands_source_axes_beyond_sampled_rows() -> None:
    surface = _valid_project_analysis_custom_op_surface()
    surface["operator_families"] = ["wave", "storage"]
    surface["fine_grained_operator_units"] = ["wave:forward_cuda", "storage:load_snapshot_gpu"]
    surface["discovered_operator_names"] = ["wave_forward_cuda", "storage_load_snapshot_gpu"]
    surface["native_operator_symbols"] = [
        "wave_iso_2d_4_float_forward_cuda",
        "storage_load_snapshot_2d_float",
    ]
    surface["kernel_launch_sites"] = ["src/wave.cu:FUNC(forward)", "src/storage_utils.h:STORAGE_FUNC(load_snapshot_gpu)"]
    surface["source_evidence"] = [
        "src/backend.py:builds wave symbols with ${ndim}, ${accuracy}, ${dtype}, and device cuda",
        "src/backend.py:enumerates ndim 1, 2, 3",
        "src/backend.py:enumerates accuracy 2, 4, 6, 8",
        "src/backend.py:enumerates dtype float and double",
        "src/storage_utils.h:STORAGE_FUNC expands storage helpers with DW_NDIM and DW_DTYPE",
    ]
    surface["variant_axes_detected"] = True
    surface["variant_axes"] = {"ndim": ["1", "2", "3"], "accuracy": ["2", "4", "6", "8"], "dtype": ["float", "double"], "device": ["cuda", "gpu"]}
    surface["expanded_operator_instances_count"] = 10
    surface["expanded_operator_variants"] = [
        {
            "unit_identity": f"wave:forward_cuda:accuracy={accuracy}:dtype={dtype}:device=cuda",
            "base_unit_identity": "wave:forward_cuda",
            "axis_values": {"accuracy": accuracy, "dtype": dtype, "device": "cuda"},
            "source_evidence": ["src/wave.cu:FUNC(forward)"],
            "candidate_public_api_routes": ["pkg.wave.forward"],
        }
        for accuracy in ["2", "4", "6", "8"]
        for dtype in ["float", "double"]
    ] + [
        {
            "unit_identity": f"storage:load_snapshot_gpu:dtype={dtype}:device=gpu",
            "base_unit_identity": "storage:load_snapshot_gpu",
            "axis_values": {"dtype": dtype, "device": "gpu"},
            "source_evidence": ["src/storage_utils.h:STORAGE_FUNC(load_snapshot_gpu)"],
            "candidate_framework_integration_routes": ["snapshot load path"],
        }
        for dtype in ["float", "double"]
    ]
    surface["fine_grained_operator_unit_evidence"] = [
        {
            "unit_identity": "wave:forward_cuda",
            "source_evidence": ["src/backend.py:wave symbol uses ndim accuracy dtype", "src/wave.cu:FUNC(forward)"],
            "candidate_public_api_routes": ["pkg.wave.forward"],
        },
        {
            "unit_identity": "storage:load_snapshot_gpu",
            "source_evidence": ["src/storage_utils.h:STORAGE_FUNC(load_snapshot_gpu) uses DW_NDIM and DW_DTYPE"],
            "candidate_framework_integration_routes": ["snapshot load path"],
        },
    ]
    output: dict[str, object] = {"custom_op_surface": surface}

    from core.custom_op_variants import normalize_project_analysis_expanded_variants

    normalize_project_analysis_expanded_variants(output)

    normalized = cast(dict[str, object], output["custom_op_surface"])
    variants = cast(list[dict[str, object]], normalized["expanded_operator_variants"])
    counts_by_base: dict[str, int] = {}
    for variant in variants:
        base = cast(str, variant["base_unit_identity"])
        counts_by_base[base] = counts_by_base.get(base, 0) + 1

    assert cast(int, normalized["expanded_operator_instances_count"]) > 10
    assert counts_by_base == {"wave:forward_cuda": 24, "storage:load_snapshot_gpu": 6}
    assert any(
        cast(str, variant["unit_identity"]) == "wave:forward_cuda:ndim=3:accuracy=8:dtype=double:device=cuda"
        for variant in variants
    )
    assert any(
        cast(str, variant["unit_identity"]) == "storage:load_snapshot_gpu:ndim=3:dtype=double:device=gpu"
        for variant in variants
    )


def test_normalize_project_analysis_does_not_leak_caller_axes_into_helper_sources(tmp_path: Path) -> None:
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    _ = (source_dir / "storage_utils.h").write_text(
        (
            "#if defined(DW_NDIM) && defined(DW_DTYPE)\n"
            "#define STORAGE_FUNC(name) storage_##name##_##DW_NDIM##d_##DW_DTYPE\n"
            "int STORAGE_FUNC(load_snapshot_gpu)(void);\n"
            "#endif\n"
        ),
        encoding="utf-8",
    )
    _ = (source_dir / "scalar.cu").write_text(
        "#define PROPAGATOR_SYMBOL(name) scalar_##DW_NDIM##d_##DW_ACCURACY##_##DW_DTYPE##_##name##_cuda\n"
        + "\n".join("// filler" for _ in range(80))
        + "\nvoid caller() { STORAGE_FUNC(load_snapshot_gpu)(); }\n",
        encoding="utf-8",
    )
    surface = _valid_project_analysis_custom_op_surface()
    surface["operator_families"] = ["storage"]
    surface["fine_grained_operator_units"] = ["storage:load_snapshot_gpu"]
    surface["discovered_operator_names"] = ["storage_load_snapshot_gpu"]
    surface["native_operator_symbols"] = ["storage_load_snapshot_2d_float"]
    surface["kernel_launch_sites"] = ["src/storage_utils.h:STORAGE_FUNC(load_snapshot_gpu)"]
    surface["source_evidence"] = [
        "src/storage_utils.h:3 declares STORAGE_FUNC(load_snapshot_gpu)",
        "src/scalar.cu:82 calls STORAGE_FUNC(load_snapshot_gpu)",
    ]
    surface["variant_axes_detected"] = True
    surface["variant_axes"] = {"ndim": ["1", "2", "3"], "accuracy": ["2", "4", "6", "8"], "dtype": ["float", "double"], "device": ["gpu"]}
    surface["expanded_operator_instances_count"] = 8
    surface["expanded_operator_variants"] = [
        {
            "unit_identity": f"storage:load_snapshot_gpu:ndim=1:accuracy={accuracy}:dtype={dtype}:device=gpu",
            "base_unit_identity": "storage:load_snapshot_gpu",
            "axis_values": {"ndim": "1", "accuracy": accuracy, "dtype": dtype, "device": "gpu"},
            "source_evidence": ["src/storage_utils.h:STORAGE_FUNC(load_snapshot_gpu)"],
            "candidate_framework_integration_routes": ["snapshot load path"],
        }
        for accuracy in ["2", "4", "6", "8"]
        for dtype in ["float", "double"]
    ]
    surface["fine_grained_operator_unit_evidence"] = [
        {
            "unit_identity": "storage:load_snapshot_gpu",
            "source_evidence": [
                "src/storage_utils.h:3 declares STORAGE_FUNC(load_snapshot_gpu)",
                "src/scalar.cu:82 calls STORAGE_FUNC(load_snapshot_gpu)",
            ],
            "candidate_framework_integration_routes": ["snapshot load path"],
        }
    ]
    output: dict[str, object] = {"project_dir": str(tmp_path), "custom_op_surface": surface}

    from core.custom_op_variants import normalize_project_analysis_expanded_variants

    normalize_project_analysis_expanded_variants(output)

    normalized = cast(dict[str, object], output["custom_op_surface"])
    variants = cast(list[dict[str, object]], normalized["expanded_operator_variants"])

    assert normalized["expanded_operator_instances_count"] == 6
    assert len(variants) == 6
    assert all("accuracy" not in cast(dict[str, str], variant["axis_values"]) for variant in variants)


def test_normalize_project_analysis_expands_compact_variant_axes_samples() -> None:
    surface = _valid_project_analysis_custom_op_surface()
    surface["operator_families"] = ["wave", "storage", "simple_compress"]
    surface["fine_grained_operator_units"] = [
        "wave:forward_cuda",
        "storage_snapshot:load_snapshot_gpu",
        "simple_compress:decompress_cuda",
    ]
    surface["discovered_operator_names"] = [
        "wave_forward_cuda",
        "storage_snapshot_load_snapshot_gpu",
        "simple_compress_decompress_cuda",
    ]
    surface["native_operator_symbols"] = [
        "wave_iso_${ndim}d_${accuracy}_${dtype}_forward_cuda",
        "storage_${ndim}d_${dtype}_load_snapshot_gpu",
        "simple_compress_${ndim}d_${dtype}_decompress_cuda",
    ]
    surface["source_evidence"] = [
        "src/backend.py:builds wave symbols with ${ndim}, ${accuracy}, ${dtype}, and device cuda",
        "src/backend.py:enumerates ndim 1, 2, 3",
        "src/backend.py:enumerates accuracy 2, 4, 6, 8",
        "src/backend.py:enumerates dtype float and double",
        "src/storage_utils.h:STORAGE_FUNC expands storage helpers with ${ndim} and ${dtype}",
        "src/simple_compress.h:SC_FUNC expands compression helpers with ${ndim} and ${dtype}",
    ]
    surface["variant_axes_detected"] = True
    surface["variant_axes"] = {"ndim": ["1", "2", "3"], "accuracy": ["2", "4", "6", "8"], "dtype": ["float", "double"], "device": ["cuda", "gpu"]}
    surface["expanded_operator_instances_count"] = 36
    surface["expanded_operator_variants"] = [
        {
            "unit_identity": "wave:forward_cuda:*24{ndim=[1,2,3],accuracy=[2,4,6,8],dtype=[float,double],device=cuda}",
            "base_unit_identity": "wave:forward_cuda",
            "variant_axes": {"ndim": ["1", "2", "3"], "accuracy": ["2", "4", "6", "8"], "dtype": ["float", "double"], "device": "cuda"},
            "source_evidence": ["src/wave.cu:FUNC(forward)"],
            "candidate_public_api_routes": ["pkg.wave.forward"],
        },
        {
            "unit_identity": "storage_snapshot:load_snapshot_gpu:*6{ndim=[1,2,3],dtype=[float,double],device=gpu}",
            "base_unit_identity": "storage_snapshot:load_snapshot_gpu",
            "variant_axes": {"ndim": ["1", "2", "3"], "dtype": ["float", "double"], "device": "gpu"},
            "source_evidence": ["src/storage_utils.h:STORAGE_FUNC(load_snapshot_gpu)"],
            "candidate_framework_integration_routes": ["snapshot load path"],
        },
        {
            "unit_identity": "simple_compress:decompress_cuda:*6{ndim=[1,2,3],dtype=[float,double],device=cuda}",
            "base_unit_identity": "simple_compress:decompress_cuda",
            "variant_axes": {"ndim": ["1", "2", "3"], "dtype": ["float", "double"], "device": "cuda"},
            "source_evidence": ["src/simple_compress.h:SC_FUNC(decompress_cuda)"],
            "candidate_framework_integration_routes": ["decompression path"],
        },
    ]
    surface["fine_grained_operator_unit_evidence"] = [
        {"unit_identity": "wave:forward_cuda", "source_evidence": ["src/backend.py:wave symbol uses ndim accuracy dtype", "src/wave.cu:FUNC(forward)"], "candidate_public_api_routes": ["pkg.wave.forward"]},
        {"unit_identity": "storage_snapshot:load_snapshot_gpu", "source_evidence": ["src/storage_utils.h:STORAGE_FUNC(load_snapshot_gpu) uses ndim dtype"], "candidate_framework_integration_routes": ["snapshot load path"]},
        {"unit_identity": "simple_compress:decompress_cuda", "source_evidence": ["src/simple_compress.h:SC_FUNC(decompress_cuda) uses ndim dtype"], "candidate_framework_integration_routes": ["decompression path"]},
    ]
    output: dict[str, object] = {"custom_op_surface": surface}

    from core.custom_op_variants import normalize_project_analysis_expanded_variants

    normalize_project_analysis_expanded_variants(output)

    normalized = cast(dict[str, object], output["custom_op_surface"])
    variants = cast(list[dict[str, object]], normalized["expanded_operator_variants"])
    identities = {cast(str, variant["unit_identity"]) for variant in variants}

    assert normalized["expanded_operator_instances_count"] == 36
    assert len(variants) == 36
    assert "wave:forward_cuda:ndim=3:accuracy=8:dtype=double:device=cuda" in identities
    assert "storage_snapshot:load_snapshot_gpu:ndim=3:dtype=double:device=gpu" in identities
    assert "simple_compress:decompress_cuda:ndim=3:dtype=double:device=cuda" in identities
    assert all("*" not in cast(str, variant["unit_identity"]) for variant in variants)


def test_normalize_project_analysis_expands_unsampled_helpers_from_source_files(tmp_path: Path) -> None:
    source_root = tmp_path / "src"
    source_root.mkdir()
    _ = (source_root / "simple_compress.h").write_text(
        (
            "#if defined(DW_NDIM) && defined(DW_DTYPE)\n"
            "#define SC_FUNC(name) simple_compress_##name##_##DW_NDIM##d_##DW_DTYPE\n"
            "int SC_FUNC(decompress_cuda)(void);\n"
            "#endif\n"
        ),
        encoding="utf-8",
    )
    _ = (source_root / "storage_utils.h").write_text(
        (
            "#if defined(DW_NDIM) && defined(DW_DTYPE)\n"
            "#define STORAGE_FUNC(name) storage_##name##_##DW_NDIM##d_##DW_DTYPE\n"
            "int STORAGE_FUNC(load_snapshot_gpu)(void);\n"
            "#endif\n"
        ),
        encoding="utf-8",
    )
    surface = _valid_project_analysis_custom_op_surface()
    surface["fine_grained_operator_units"] = [
        "wave:forward_cuda",
        "storage:load_snapshot_gpu",
        "simple_compress:decompress_cuda",
    ]
    surface["source_evidence"] = [
        "src/backend.py:builds wave symbols with ndim accuracy dtype device cuda",
        "src/backend.py:enumerates ndim 1, 2, 3",
        "src/backend.py:enumerates accuracy 2, 4, 6, 8",
        "src/backend.py:enumerates dtype float and double",
        "src/simple_compress.h:3 declares SC_FUNC(decompress_cuda)",
        "src/storage_utils.h:3 declares STORAGE_FUNC(load_snapshot_gpu)",
    ]
    surface["variant_axes_detected"] = True
    surface["variant_axes"] = {"ndim": ["1", "2", "3"], "accuracy": ["2", "4", "6", "8"], "dtype": ["float", "double"], "device": ["cuda", "gpu"]}
    surface["expanded_operator_instances_count"] = 24
    surface["expanded_operator_variants"] = [
        {
            "unit_identity": f"wave:forward_cuda:ndim={ndim}:accuracy={accuracy}:dtype={dtype}:device=cuda",
            "base_unit_identity": "wave:forward_cuda",
            "axis_values": {"ndim": ndim, "accuracy": accuracy, "dtype": dtype, "device": "cuda"},
            "source_evidence": ["src/wave.cu:FUNC(forward)"],
            "candidate_public_api_routes": ["pkg.wave.forward"],
        }
        for ndim in ["1", "2", "3"]
        for accuracy in ["2", "4", "6", "8"]
        for dtype in ["float", "double"]
    ]
    surface["fine_grained_operator_unit_evidence"] = [
        {"unit_identity": "wave:forward_cuda", "source_evidence": ["src/backend.py:wave symbol uses ndim accuracy dtype"], "candidate_public_api_routes": ["pkg.wave.forward"]},
        {"unit_identity": "storage:load_snapshot_gpu", "source_evidence": ["src/storage_utils.h:3 declares STORAGE_FUNC(load_snapshot_gpu)"], "candidate_framework_integration_routes": ["snapshot load path"]},
        {"unit_identity": "simple_compress:decompress_cuda", "source_evidence": ["src/simple_compress.h:3 declares SC_FUNC(decompress_cuda)"], "candidate_framework_integration_routes": ["decompression path"]},
    ]
    output: dict[str, object] = {"project_dir": str(tmp_path), "custom_op_surface": surface}

    from core.custom_op_variants import normalize_project_analysis_expanded_variants

    normalize_project_analysis_expanded_variants(output)

    normalized = cast(dict[str, object], output["custom_op_surface"])
    variants = cast(list[dict[str, object]], normalized["expanded_operator_variants"])
    counts_by_base: dict[str, int] = {}
    for variant in variants:
        base = cast(str, variant["base_unit_identity"])
        counts_by_base[base] = counts_by_base.get(base, 0) + 1

    assert normalized["expanded_operator_instances_count"] == 36
    assert counts_by_base == {
        "wave:forward_cuda": 24,
        "storage:load_snapshot_gpu": 6,
        "simple_compress:decompress_cuda": 6,
    }
    assert all("accuracy" not in cast(dict[str, str], variant["axis_values"]) for variant in variants if variant["base_unit_identity"] != "wave:forward_cuda")


def test_project_analysis_rejects_sampled_variant_inventory_when_source_backed_axes_imply_more_combinations() -> None:
    surface = _source_backed_variant_inventory_surface()
    variants = cast(list[dict[str, object]], surface["expanded_operator_variants"])
    surface["expanded_operator_variants"] = variants[:2]
    surface["expanded_operator_instances_count"] = 2

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
    assert any("sampled or incomplete relative to source-backed axes/templates/evidence" in error for error in result["errors"])


def test_project_analysis_rejects_partially_expanded_source_template_inventory() -> None:
    output: dict[str, object] = {"custom_op_surface": _source_backed_variant_inventory_surface()}

    from core.custom_op_variants import normalize_project_analysis_expanded_variants

    normalize_project_analysis_expanded_variants(output)
    surface = cast(dict[str, object], output["custom_op_surface"])
    variants = cast(list[dict[str, object]], surface["expanded_operator_variants"])
    partial_variants = [
        variant
        for variant in variants
        if cast(str, variant["base_unit_identity"]) != "alpha:forward_cuda"
        or cast(dict[str, str], variant["axis_values"])["ndim"] == "1d"
    ]
    surface["expanded_operator_variants"] = partial_variants
    surface["expanded_operator_instances_count"] = len(partial_variants)

    result = validate_project_analysis(
        {
            "project_dir": "/tmp/project",
            "dependencies": ["torch"],
            "cuda_detected": True,
            "entry_script": "validate_custom_ops_full.py",
            "custom_op_surface": surface,
        }
    )

    assert len(partial_variants) > 2
    assert result["passed"] is False
    assert any(
        "missing combinations" in error and "alpha:forward_cuda" in error and "ndim=2d" in error
        for error in result["errors"]
    )


def test_project_analysis_rejects_missing_source_template_combination_even_with_extra_rows() -> None:
    output: dict[str, object] = {"custom_op_surface": _source_backed_variant_inventory_surface()}

    from core.custom_op_variants import normalize_project_analysis_expanded_variants

    normalize_project_analysis_expanded_variants(output)
    surface = cast(dict[str, object], output["custom_op_surface"])
    variants = cast(list[dict[str, object]], surface["expanded_operator_variants"])
    expected_len = len(variants)
    missing_unit = "alpha:forward_cuda:ndim=2d:dtype=double:device=cuda"
    extra_variant = {
        "unit_identity": "unrelated:forward_cuda:ndim=1d:dtype=float:device=cuda",
        "base_unit_identity": "unrelated:forward_cuda",
        "axis_values": {"ndim": "1d", "dtype": "float", "device": "cuda"},
        "source_evidence": ["src/unrelated.cu:extra source-backed row"],
        "candidate_public_api_routes": ["pkg.unrelated.forward"],
    }
    surface["expanded_operator_variants"] = [
        variant for variant in variants if variant["unit_identity"] != missing_unit
    ] + [extra_variant]
    surface["expanded_operator_instances_count"] = len(cast(list[object], surface["expanded_operator_variants"]))

    result = validate_project_analysis(
        {
            "project_dir": "/tmp/project",
            "dependencies": ["torch"],
            "cuda_detected": True,
            "entry_script": "validate_custom_ops_full.py",
            "custom_op_surface": surface,
        }
    )

    assert surface["expanded_operator_instances_count"] == expected_len
    assert result["passed"] is False
    assert any(
        "missing combinations" in error
        and "alpha:forward_cuda" in error
        and "ndim=2d" in error
        and "dtype=double" in error
        for error in result["errors"]
    )


def test_project_analysis_rejects_missing_custom_op_surface_with_extension_indicators(tmp_path: Path) -> None:
    csrc = tmp_path / "csrc"
    csrc.mkdir()
    _ = (tmp_path / "setup.py").write_text(
        "from torch.utils.cpp_extension import CUDAExtension\n"
        + "ext_modules = [CUDAExtension('pointnet2_ops._ext', ['csrc/bindings.cpp', 'csrc/sampling_gpu.cu'])]\n",
        encoding="utf-8",
    )
    _ = (csrc / "bindings.cpp").write_text(
        "#include <pybind11/pybind11.h>\nPYBIND11_MODULE(TORCH_EXTENSION_NAME, m) { m.def(\"gather_points\", [](){}); }\n",
        encoding="utf-8",
    )
    _ = (csrc / "sampling_gpu.cu").write_text("__global__ void gather_points_kernel() {}\n", encoding="utf-8")

    result = validate_project_analysis(
        {
            "project_dir": str(tmp_path),
            "dependencies": ["torch"],
            "cuda_detected": True,
            "entry_script": "test_pointnet2.py",
        }
    )

    assert result["passed"] is False
    assert any("custom_op_surface must be present" in error and "custom-op build/binding evidence" in error for error in result["errors"])


def test_project_analysis_accepts_plain_cuda_project_without_custom_op_surface(tmp_path: Path) -> None:
    _ = (tmp_path / "model.py").write_text(
        "import torch\n"
        + "device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')\n"
        + "print(torch.ones(1, device=device))\n",
        encoding="utf-8",
    )

    result = validate_project_analysis(
        {
            "project_dir": str(tmp_path),
            "dependencies": ["torch"],
            "cuda_detected": True,
            "entry_script": "model.py",
        }
    )

    assert result == {"passed": True, "errors": [], "warnings": []}


def test_project_analysis_ignores_incidental_axis_mentions_in_source_context(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    _ = (src / "backend_utils.py").write_text(
        "constructs propagator_iso_ndim_accuracy_dtype_pass_device symbol names and enumerates ndim 1, 2, 3 and accuracy 2, 4, 6, 8 and dtype float and double and device cpu, cuda\n",
        encoding="utf-8",
    )

    surface = _axis_coverage_regression_surface()
    result = validate_project_analysis(
        {
            "project_dir": str(tmp_path),
            "dependencies": ["torch"],
            "cuda_detected": True,
            "entry_script": "validate_custom_ops_full.py",
            "custom_op_surface": surface,
        }
    )

    assert result == {"passed": True, "errors": [], "warnings": []}


def test_project_analysis_rejects_false_custom_op_with_active_expanded_variant_metadata() -> None:
    surface = _valid_project_analysis_custom_op_surface()
    surface["custom_op_detected"] = False
    surface["discovery_complete"] = False
    surface["variant_axes_detected"] = True
    surface["variant_axes"] = {"dtype": ["float32", "float16"]}
    surface["expanded_operator_variants"] = [
        {"unit_identity": "op_alpha:float32", "base_unit_identity": "op_alpha", "axis_values": {"dtype": "float32"}, "source_evidence": ["csrc/op_alpha.cpp"], "candidate_public_api_routes": ["pkg.ops.alpha_float32"]},
        {"unit_identity": "op_alpha:float16", "base_unit_identity": "op_alpha", "axis_values": {"dtype": "float16"}, "source_evidence": ["csrc/op_alpha.cpp"], "candidate_framework_integration_routes": ["pkg.layers.AlphaFloat16.forward"]},
    ]
    surface["expanded_operator_instances_count"] = 2

    result = validate_project_analysis(
        {
            "project_dir": "/tmp/project",
            "dependencies": ["torch"],
            "cuda_detected": True,
            "entry_script": "train.py",
            "custom_op_surface": surface,
        }
    )

    assert result["passed"] is False
    assert any("custom_op_detected must be true when active expanded variant metadata is present" in error for error in result["errors"])


def test_project_analysis_accepts_plain_false_custom_op_without_variant_metadata() -> None:
    surface = _valid_project_analysis_custom_op_surface()
    surface["custom_op_detected"] = False
    surface["discovery_complete"] = False

    result = validate_project_analysis(
        {
            "project_dir": "/tmp/project",
            "dependencies": ["torch"],
            "cuda_detected": False,
            "entry_script": "train.py",
            "custom_op_surface": surface,
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


def test_project_analysis_rejects_requirements_doc_discovery_source() -> None:
    surface = _valid_project_analysis_custom_op_surface()
    discovery_sources = cast(list[str], surface["discovery_sources_checked"])
    surface["discovery_sources_checked"] = [*discovery_sources, "requirements_doc"]
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
    assert any("requirements_doc" in error for error in result["errors"])


def test_project_analysis_rejects_requirements_doc_when_custom_op_detected_false() -> None:
    surface = _valid_project_analysis_custom_op_surface()
    surface["custom_op_detected"] = False
    surface["discovery_complete"] = False
    surface["discovery_sources_checked"] = ["requirements_doc"]
    result = validate_project_analysis(
        {
            "project_dir": "/tmp/project",
            "dependencies": ["torch"],
            "cuda_detected": True,
            "entry_script": "train.py",
            "custom_op_surface": surface,
        }
    )

    assert result["passed"] is False
    assert any("requirements_doc" in error for error in result["errors"])


def test_project_analysis_rejects_unit_evidence_without_route_candidates() -> None:
    surface = _valid_project_analysis_custom_op_surface()
    evidence = cast(list[dict[str, object]], surface["fine_grained_operator_unit_evidence"])
    _ = evidence[0].pop("candidate_public_api_routes")
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
    assert any("candidate_public_api_routes or candidate_framework_integration_routes" in error for error in result["errors"])


def test_project_analysis_rejects_source_discovered_unit_mentioned_only_in_evidence(tmp_path: Path) -> None:
    _write_deepwave_like_sources(tmp_path)
    surface = _valid_project_analysis_custom_op_surface()
    surface["operator_families"] = ["scalar", "storage", "simple_compress"]
    surface["fine_grained_operator_units"] = [
        "scalar:forward_cuda",
        "scalar:backward_cuda",
        "simple_compress:compress_cuda",
        "simple_compress:decompress_cuda",
    ]
    surface["discovered_operator_names"] = [
        "scalar_forward_cuda",
        "scalar_backward_cuda",
        "storage_save_snapshot_gpu",
        "storage_load_snapshot_gpu",
        "simple_compress_compress_cuda",
        "simple_compress_decompress_cuda",
    ]
    surface["native_operator_symbols"] = [
        "forward_cuda",
        "backward_cuda",
        "save_snapshot_gpu",
        "load_snapshot_gpu",
        "compress_cuda",
        "decompress_cuda",
    ]
    surface["kernel_launch_sites"] = [
        "deepwave_original_src/scalar.cu:forward_kernel<<<...>>>",
        "deepwave_original_src/scalar.cu:backward_kernel<<<...>>>",
        "deepwave_original_src/storage_utils.cu:save_snapshot_gpu calls compress_cuda",
        "deepwave_original_src/storage_utils.cu:load_snapshot_gpu calls decompress_cuda",
        "deepwave_original_src/simple_compress.cu:compress_kernel<<<...>>>",
        "deepwave_original_src/simple_compress.cu:decompress_kernel<<<...>>>",
    ]
    surface["source_evidence"] = [
        "deepwave_original_src/scalar.cu:FUNC(forward)",
        "deepwave_original_src/scalar.cu:FUNC(backward)",
        "deepwave_original_src/storage_utils.h:STORAGE_FUNC(save_snapshot_gpu)",
        "deepwave_original_src/storage_utils.cu:STORAGE_FUNC(load_snapshot_gpu)",
        "deepwave_original_src/simple_compress.cu:SC_FUNC(compress_cuda)",
        "deepwave_original_src/simple_compress.cu:SC_FUNC(decompress_cuda)",
    ]
    surface["fine_grained_operator_unit_evidence"] = [
        {"unit_identity": "scalar:forward_cuda", "source_evidence": ["deepwave_original_src/scalar.cu:FUNC(forward)"], "candidate_public_api_routes": ["deepwave.scalar"]},
        {"unit_identity": "scalar:backward_cuda", "source_evidence": ["deepwave_original_src/scalar.cu:FUNC(backward)"], "candidate_framework_integration_routes": ["ScalarForwardFunc.backward"]},
        {"unit_identity": "simple_compress:compress_cuda", "source_evidence": ["deepwave_original_src/simple_compress.cu:SC_FUNC(compress_cuda)"], "candidate_framework_integration_routes": ["storage compression path"]},
        {"unit_identity": "simple_compress:decompress_cuda", "source_evidence": ["deepwave_original_src/simple_compress.cu:SC_FUNC(decompress_cuda)"], "candidate_framework_integration_routes": ["storage decompression path"]},
    ]

    result = validate_project_analysis(_project_analysis_payload(tmp_path, surface))

    assert result["passed"] is False
    assert any("fine_grained_operator_units missing" in error and "storage:save_snapshot_gpu" in error for error in result["errors"])
    assert any("fine_grained_operator_unit_evidence missing" in error and "storage:load_snapshot_gpu" in error for error in result["errors"])


def test_project_analysis_discovers_plain_cuda_exports_in_cpp_sources(tmp_path: Path) -> None:
    source_root = tmp_path / "csrc"
    source_root.mkdir()
    _ = (source_root / "helpers.cpp").write_text(
        (
            'extern "C" int fused_helper_cuda(void* stream) { return 0; }\n'
            'int load_tiles_gpu(void* stream) { return 0; }\n'
        ),
        encoding="utf-8",
    )
    surface = _valid_project_analysis_custom_op_surface()
    surface["operator_families"] = ["helpers"]
    surface["fine_grained_operator_units"] = ["helpers:fused_helper_cuda", "helpers:load_tiles_gpu"]
    surface["discovered_operator_names"] = ["helpers_fused_helper_cuda", "helpers_load_tiles_gpu"]
    surface["native_operator_symbols"] = ["fused_helper_cuda", "load_tiles_gpu"]
    surface["kernel_launch_sites"] = ["csrc/helpers.cpp:fused_helper_cuda", "csrc/helpers.cpp:load_tiles_gpu"]
    surface["source_evidence"] = ["csrc/helpers.cpp:fused_helper_cuda", "csrc/helpers.cpp:load_tiles_gpu"]
    surface["fine_grained_operator_unit_evidence"] = [
        {"unit_identity": "helpers:fused_helper_cuda", "source_evidence": ["csrc/helpers.cpp:fused_helper_cuda"], "candidate_framework_integration_routes": ["native helper route"]},
        {"unit_identity": "helpers:load_tiles_gpu", "source_evidence": ["csrc/helpers.cpp:load_tiles_gpu"], "candidate_framework_integration_routes": ["native helper route"]},
    ]

    result = validate_project_analysis(_project_analysis_payload(tmp_path, surface))

    assert result == {"passed": True, "errors": [], "warnings": []}


def _write_deepwave_like_sources(project_root: Path) -> None:
    source_root = project_root / "deepwave_original_src"
    source_root.mkdir(parents=True)
    _ = (source_root / "scalar.cu").write_text(
        """
#define CAT_I(name, ndim, accuracy, dtype, device) scalar_##name##_##device
#define CAT(name, ndim, accuracy, dtype, device) CAT_I(name, ndim, accuracy, dtype, device)
#define FUNC(name) CAT(name, DW_NDIM, DW_ACCURACY, DW_DTYPE, DW_DEVICE)
#include "storage_utils.h"
extern "C"
int FUNC(forward)(void* stream) {
  forward_kernel<<<1, 1, 0, stream>>>();
  return STORAGE_FUNC(save_snapshot_gpu)(nullptr, nullptr, nullptr, nullptr, 0, false, 0, 0, 0, 0, 1, stream);
}
extern "C"
int FUNC(backward)(void* stream) {
  backward_kernel<<<1, 1, 0, stream>>>();
  return STORAGE_FUNC(load_snapshot_gpu)(nullptr, nullptr, nullptr, nullptr, 0, false, 0, 0, 0, 0, 1, stream);
}
""",
        encoding="utf-8",
    )
    _ = (source_root / "storage_utils.h").write_text(
        """
#define STORAGE_FUNC(name) storage_##name##_2d_float
extern "C" {
int STORAGE_FUNC(save_snapshot_gpu)(void const* store_1, void* store_2, void* store_3, FILE* fp, int mode, bool compress, int step, size_t uncomp, size_t comp, size_t shots, size_t nx, void* stream);
int STORAGE_FUNC(load_snapshot_gpu)(void* store_1, void* store_2, void* store_3, FILE* fp, int mode, bool compress, int step, size_t uncomp, size_t comp, size_t shots, size_t nx, void* stream);
}
""",
        encoding="utf-8",
    )
    _ = (source_root / "storage_utils.cu").write_text(
        """
#define STORAGE_FUNC(name) storage_##name##_2d_float
#define SC_FUNC(name) simple_compress_##name##_2d_float
extern "C" {
int STORAGE_FUNC(save_snapshot_gpu)(void const* store_1, void* store_2, void* store_3, FILE* fp, int mode, bool compress, int step, size_t uncomp, size_t comp, size_t shots, size_t nx, void* stream) {
  return SC_FUNC(compress_cuda)(store_1, store_2, shots, nx, stream);
}
int STORAGE_FUNC(load_snapshot_gpu)(void* store_1, void* store_2, void* store_3, FILE* fp, int mode, bool compress, int step, size_t uncomp, size_t comp, size_t shots, size_t nx, void* stream) {
  return SC_FUNC(decompress_cuda)(store_2, store_1, shots, nx, stream);
}
}
""",
        encoding="utf-8",
    )
    _ = (source_root / "simple_compress.cu").write_text(
        """
#define SC_FUNC(name) simple_compress_##name##_2d_float
extern "C" {
int SC_FUNC(compress_cuda)(void const* input, void* output, size_t shots, size_t nx, void* stream) {
  compress_kernel<<<1, 1, 0, stream>>>();
  return 0;
}
int SC_FUNC(decompress_cuda)(void const* input, void* output, size_t shots, size_t nx, void* stream) {
  decompress_kernel<<<1, 1, 0, stream>>>();
  return 0;
}
}
""",
        encoding="utf-8",
    )


def _project_analysis_payload(project_dir: Path, surface: dict[str, object]) -> dict[str, object]:
    return {
        "project_dir": str(project_dir),
        "dependencies": ["torch"],
        "cuda_detected": True,
        "entry_script": "test_data_and_scripts/run_full_fwi_npu.py",
        "custom_op_surface": surface,
    }


def test_project_analysis_rejects_deepwave_like_inventory_missing_storage_helpers(tmp_path: Path) -> None:
    _write_deepwave_like_sources(tmp_path)
    surface = _valid_project_analysis_custom_op_surface()
    surface["operator_families"] = ["scalar", "simple_compress"]
    surface["fine_grained_operator_units"] = [
        "scalar:forward_cuda",
        "scalar:backward_cuda",
        "simple_compress:compress_cuda",
        "simple_compress:decompress_cuda",
    ]
    surface["discovered_operator_names"] = [
        "scalar_forward_cuda",
        "scalar_backward_cuda",
        "simple_compress_compress_cuda",
        "simple_compress_decompress_cuda",
    ]
    surface["native_operator_symbols"] = [
        "forward_cuda",
        "backward_cuda",
        "compress_cuda",
        "decompress_cuda",
    ]
    surface["kernel_launch_sites"] = [
        "deepwave_original_src/scalar.cu:forward_kernel<<<...>>>",
        "deepwave_original_src/scalar.cu:backward_kernel<<<...>>>",
        "deepwave_original_src/simple_compress.cu:compress_kernel<<<...>>>",
        "deepwave_original_src/simple_compress.cu:decompress_kernel<<<...>>>",
    ]
    surface["source_evidence"] = [
        "deepwave_original_src/scalar.cu:FUNC(forward)",
        "deepwave_original_src/scalar.cu:FUNC(backward)",
        "deepwave_original_src/simple_compress.cu:SC_FUNC(compress_cuda)",
        "deepwave_original_src/simple_compress.cu:SC_FUNC(decompress_cuda)",
    ]
    surface["fine_grained_operator_unit_evidence"] = [
        {"unit_identity": "scalar:forward_cuda", "source_evidence": ["deepwave_original_src/scalar.cu:FUNC(forward)"], "candidate_public_api_routes": ["deepwave.scalar"]},
        {"unit_identity": "scalar:backward_cuda", "source_evidence": ["deepwave_original_src/scalar.cu:FUNC(backward)"], "candidate_framework_integration_routes": ["ScalarForwardFunc.backward"]},
        {"unit_identity": "simple_compress:compress_cuda", "source_evidence": ["deepwave_original_src/simple_compress.cu:SC_FUNC(compress_cuda)"], "candidate_framework_integration_routes": ["storage compression path"]},
        {"unit_identity": "simple_compress:decompress_cuda", "source_evidence": ["deepwave_original_src/simple_compress.cu:SC_FUNC(decompress_cuda)"], "candidate_framework_integration_routes": ["storage decompression path"]},
    ]

    result = validate_project_analysis(_project_analysis_payload(tmp_path, surface))

    assert result["passed"] is False
    assert any("storage:save_snapshot_gpu" in error for error in result["errors"])
    assert any("storage:load_snapshot_gpu" in error for error in result["errors"])


def test_project_analysis_accepts_deepwave_like_inventory_with_storage_helpers(tmp_path: Path) -> None:
    _write_deepwave_like_sources(tmp_path)
    surface = _valid_project_analysis_custom_op_surface()
    surface["operator_families"] = ["scalar", "storage", "simple_compress"]
    surface["fine_grained_operator_units"] = [
        "scalar:forward_cuda",
        "scalar:backward_cuda",
        "storage:save_snapshot_gpu",
        "storage:load_snapshot_gpu",
        "simple_compress:compress_cuda",
        "simple_compress:decompress_cuda",
    ]
    surface["discovered_operator_names"] = [
        "scalar_forward_cuda",
        "scalar_backward_cuda",
        "storage_save_snapshot_gpu",
        "storage_load_snapshot_gpu",
        "simple_compress_compress_cuda",
        "simple_compress_decompress_cuda",
    ]
    surface["native_operator_symbols"] = [
        "forward_cuda",
        "backward_cuda",
        "save_snapshot_gpu",
        "load_snapshot_gpu",
        "compress_cuda",
        "decompress_cuda",
    ]
    surface["kernel_launch_sites"] = [
        "deepwave_original_src/scalar.cu:forward_kernel<<<...>>>",
        "deepwave_original_src/scalar.cu:backward_kernel<<<...>>>",
        "deepwave_original_src/storage_utils.cu:save_snapshot_gpu calls compress_cuda",
        "deepwave_original_src/storage_utils.cu:load_snapshot_gpu calls decompress_cuda",
        "deepwave_original_src/simple_compress.cu:compress_kernel<<<...>>>",
        "deepwave_original_src/simple_compress.cu:decompress_kernel<<<...>>>",
    ]
    surface["source_evidence"] = [
        "deepwave_original_src/scalar.cu:FUNC(forward)",
        "deepwave_original_src/scalar.cu:FUNC(backward)",
        "deepwave_original_src/storage_utils.h:STORAGE_FUNC(save_snapshot_gpu)",
        "deepwave_original_src/storage_utils.cu:STORAGE_FUNC(load_snapshot_gpu)",
        "deepwave_original_src/simple_compress.cu:SC_FUNC(compress_cuda)",
        "deepwave_original_src/simple_compress.cu:SC_FUNC(decompress_cuda)",
    ]
    surface["fine_grained_operator_unit_evidence"] = [
        {"unit_identity": "scalar:forward_cuda", "source_evidence": ["deepwave_original_src/scalar.cu:FUNC(forward)"], "candidate_public_api_routes": ["deepwave.scalar"]},
        {"unit_identity": "scalar:backward_cuda", "source_evidence": ["deepwave_original_src/scalar.cu:FUNC(backward)"], "candidate_framework_integration_routes": ["ScalarForwardFunc.backward"]},
        {"unit_identity": "storage:save_snapshot_gpu", "source_evidence": ["deepwave_original_src/storage_utils.h:STORAGE_FUNC(save_snapshot_gpu)"], "candidate_framework_integration_routes": ["propagator snapshot save path"]},
        {"unit_identity": "storage:load_snapshot_gpu", "source_evidence": ["deepwave_original_src/storage_utils.cu:STORAGE_FUNC(load_snapshot_gpu)"], "candidate_framework_integration_routes": ["propagator snapshot load path"]},
        {"unit_identity": "simple_compress:compress_cuda", "source_evidence": ["deepwave_original_src/simple_compress.cu:SC_FUNC(compress_cuda)"], "candidate_framework_integration_routes": ["storage compression path"]},
        {"unit_identity": "simple_compress:decompress_cuda", "source_evidence": ["deepwave_original_src/simple_compress.cu:SC_FUNC(decompress_cuda)"], "candidate_framework_integration_routes": ["storage decompression path"]},
    ]

    result = validate_project_analysis(_project_analysis_payload(tmp_path, surface))

    assert result == {"passed": True, "errors": [], "warnings": []}


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


def _valid_serving_contract(tmp_path: Path, route: str, framework: str, entry_kind: str) -> dict[str, object]:
    script_path = tmp_path / "validate_serving.py"
    _ = script_path.write_text("print('serving validation')\n", encoding="utf-8")
    return {
        "project_dir": str(tmp_path),
        "entry_script_path": str(script_path),
        "run_command": f"python {script_path}",
        "entry_script_kind": entry_kind,
        "migration_route": route,
        "serving_framework": framework,
        "launch_command": "python -m vllm.entrypoints.openai.api_server --model demo"
        if framework == "vllm"
        else "python -m sglang.launch_server --model-path demo",
        "readiness_probe": {"url": "http://127.0.0.1:8000/health", "expected_status": 200},
        "request_validation": {"url": "http://127.0.0.1:8000/v1/completions", "fixture": "tests/request.json"},
        "project_test_files": ["tests/test_serving_api.py"],
        "expected_outputs": ["response contains generated text"],
        "required_runtime_env": ["ASCEND_VISIBLE_DEVICES"],
        "required_checks": [
            "project_demo_or_test_execution",
            "serving_api_request_validation",
            "readiness_probe_passed",
            "npu_execution_evidence",
            "no_cuda_fallback",
            "no_cpu_fallback",
            "fresh_serving_report",
            "route_framework_match",
        ],
        "serving_reports_dir": "migration_reports/serving",
        "required_report_paths": ["migration_reports/serving/serving_final_gate.json"],
        "serving_validation_obligations": [
            "actual_project_demo_test_or_api_validation",
            "npu_execution_evidence",
            "reject_import_only_or_smoke_only",
            "reject_cuda_or_cpu_fallback",
            "fresh_report_paths",
            "route_framework_match",
        ],
    }


@pytest.mark.parametrize(
    ("route", "framework", "entry_kind"),
    [("vllm_serving", "vllm", "vllm_serving_validation"), ("sglang_serving", "sglang", "sglang_serving_validation")],
)
def test_entry_script_validator_accepts_serving_contracts(tmp_path: Path, route: str, framework: str, entry_kind: str) -> None:
    result = validate_entry_script(_valid_serving_contract(tmp_path, route, framework, entry_kind))

    assert result == {"passed": True, "errors": [], "warnings": []}


def test_entry_script_validator_rejects_unsafe_serving_launch_command(tmp_path: Path) -> None:
    contract = _valid_serving_contract(tmp_path, "vllm_serving", "vllm", "vllm_serving_validation")
    contract["launch_command"] = "python serve.py; touch /tmp/pwned"

    result = validate_entry_script(contract)

    assert result["passed"] is False
    assert any("launch_command" in error and "single non-interactive process" in error for error in result["errors"])


@pytest.mark.parametrize("entry_kind", ["vllm_serving_validation", "sglang_serving_validation"])
def test_entry_static_accepts_serving_entry_kinds(entry_kind: str) -> None:
    result = validate_entry_static(
        {"validation_passed": True, "issues": [], "fix_plan": "serving static checks pass", "entry_script_kind": entry_kind}
    )

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


def test_entry_script_validator_requires_custom_op_route_evidence_contract_fields(tmp_path: Path) -> None:
    script_path = tmp_path / "validate_custom_ops_full.py"
    _ = script_path.write_text("print('custom-op validation')\n", encoding="utf-8")
    payload = _valid_custom_op_contract(str(script_path))
    schema = cast(dict[str, object], payload["operator_inventory_schema"])
    _ = schema.pop("route_evidence_fields")
    checks = cast(list[str], payload["required_checks"])
    checks.remove("per_entry_public_api_or_framework_integration_route_evidence")
    obligations = cast(list[str], payload["validation_obligations"])
    obligations.remove("per_row_public_or_framework_route_evidence")

    result = validate_entry_script(payload)

    assert result["passed"] is False
    assert any("route_evidence_fields" in error for error in result["errors"])
    assert any("per_entry_public_api_or_framework_integration_route_evidence" in error for error in result["errors"])
    assert any("per_row_public_or_framework_route_evidence" in error for error in result["errors"])


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


def test_entry_script_validator_rejects_spoofed_reports_dir_project_root(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    external_dir = tmp_path / "external"
    external_dir.mkdir()
    external_script = external_dir / "validate_custom_ops_full.py"
    _ = external_script.write_text("print('outside project')\n", encoding="utf-8")

    payload = _valid_custom_op_contract(str(external_script), str(external_dir))
    payload["project_dir"] = str(project_dir)

    result = validate_entry_script(payload)

    assert result["passed"] is False
    assert any("trusted migration_reports" in error for error in result["errors"])
    assert any("existing file for custom-op contracts" in error for error in result["errors"])


def test_entry_script_validator_rejects_run_command_script_outside_project(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    script_path = project_dir / "validate_custom_ops_full.py"
    _ = script_path.write_text("print('custom-op validation')\n", encoding="utf-8")
    outside_script = tmp_path / "outside.py"
    _ = outside_script.write_text("print('outside')\n", encoding="utf-8")

    payload = _valid_custom_op_contract(str(script_path), str(project_dir))
    payload["run_command"] = f"{project_dir / '.venv' / 'bin' / 'python'} {outside_script}"

    result = validate_entry_script(payload)

    assert result["passed"] is False
    assert any("run_command script operands must stay under the trusted project directory" in error for error in result["errors"])


def test_custom_op_final_gate_rejects_full_pass_without_project_root() -> None:
    result = validate_custom_op_final_gate(_valid_custom_op_final_gate())

    assert result["passed"] is False
    assert any("project_root is required" in error for error in result["errors"])


def _valid_serving_final_gate(route: str = "vllm_serving", framework: str = "vllm") -> dict[str, object]:
    return {
        "migration_route": route,
        "serving_framework": framework,
        "full_migration_status": "FULL_PASS",
        "project_test_files": ["tests/test_serving_api.py"],
        "expected_outputs": ["generated text returned"],
        "required_checks": [
            "project_demo_or_test_execution",
            "serving_api_request_validation",
            "readiness_probe_passed",
            "npu_execution_evidence",
            "no_cuda_fallback",
            "no_cpu_fallback",
            "fresh_serving_report",
            "route_framework_match",
        ],
        "readiness_probe": {"passed": True, "status_code": 200},
        "request_validation": {"passed": True, "project_fixture": "tests/request.json"},
        "npu_execution_evidence": {"passed": True, "device": "npu:0", "torch_npu_observed": True},
        "project_demo_or_test_executed": True,
        "serving_api_validated": True,
        "npu_execution_observed": True,
        "cuda_fallback_detected": False,
        "cpu_fallback_detected": False,
        "import_only": False,
        "smoke_only": False,
    }


@pytest.mark.parametrize(("route", "framework"), [("vllm_serving", "vllm"), ("sglang_serving", "sglang")])
def test_serving_final_gate_accepts_strict_full_pass(route: str, framework: str) -> None:
    result = validate_serving_final_gate(_valid_serving_final_gate(route, framework), expected_route=route)

    assert result == {"passed": True, "errors": [], "warnings": []}


def test_serving_final_gate_rejects_invalid_smoke_only_success() -> None:
    payload = _valid_serving_final_gate()
    payload["smoke_only"] = True

    result = validate_serving_final_gate(payload, expected_route="vllm_serving")

    assert result["passed"] is False
    assert any("smoke_only" in error for error in result["errors"])


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


def test_custom_op_final_gate_accepts_framework_integration_route_evidence(tmp_path: Path) -> None:
    payload = _valid_custom_op_final_gate()
    _write_custom_op_manifest(tmp_path)
    _write_strict_opp_fixture(tmp_path)
    rows = cast(list[dict[str, object]], payload["rows"])
    _ = rows[0].pop("public_api_route_evidence")
    rows[0]["framework_integration_route_evidence"] = {
        "unit_identity": "ScalarFwd2D",
        "route_type": "framework_integration",
        "entrypoint": "ScalarForward.apply",
        "same_run": True,
        "custom_call_count": 3,
        "framework_integration_invoked": True,
        "native_custom_op_route_executed": True,
    }

    result = validate_custom_op_final_gate(payload, project_root=tmp_path)

    assert result == {"passed": True, "errors": [], "warnings": []}


def test_custom_op_final_gate_accepts_framework_integration_route_evidence_list(tmp_path: Path) -> None:
    payload = _valid_custom_op_final_gate()
    _write_custom_op_manifest(tmp_path)
    _write_strict_opp_fixture(tmp_path)
    rows = cast(list[dict[str, object]], payload["rows"])
    _ = rows[0].pop("public_api_route_evidence")
    rows[0]["framework_integration_route_evidence"] = [
        {
            "unit_identity": "ScalarFwd2D",
            "route_type": "framework_integration",
            "entrypoint": "ScalarForward.apply",
            "same_run": True,
            "custom_call_count": 3,
            "framework_integration_invoked": True,
            "native_custom_op_route_executed": True,
        },
        {
            "unit_identity": "ScalarFwd2D",
            "route_type": "framework_integration",
            "entrypoint": "ScalarForward.backward",
            "same_run": True,
            "custom_call_count": 2,
            "framework_entry_invoked": True,
            "opp_kernel_executed": True,
        },
    ]

    result = validate_custom_op_final_gate(payload, project_root=tmp_path)

    assert result == {"passed": True, "errors": [], "warnings": []}


def test_custom_op_final_gate_accepts_public_api_route_evidence_list(tmp_path: Path) -> None:
    payload = _valid_custom_op_final_gate()
    _write_custom_op_manifest(tmp_path)
    _write_strict_opp_fixture(tmp_path)
    rows = cast(list[dict[str, object]], payload["rows"])
    rows[0]["public_api_route_evidence"] = [
        {
            "unit_identity": "ScalarFwd2D",
            "route_type": "public_api",
            "entrypoint": "deepwave.scalar",
            "same_run": True,
            "custom_call_count": 3,
            "public_api_invoked": True,
            "native_custom_op_route_executed": True,
        },
        {
            "unit_identity": "ScalarFwd2D",
            "route_type": "public_api",
            "entrypoint": "deepwave.scalar_backward",
            "same_run": True,
            "custom_call_count": 2,
            "project_api_invoked": True,
            "opp_kernel_executed": True,
        },
    ]

    result = validate_custom_op_final_gate(payload, project_root=tmp_path)

    assert result == {"passed": True, "errors": [], "warnings": []}


def test_custom_op_final_gate_rejects_empty_framework_integration_route_evidence_list(tmp_path: Path) -> None:
    payload = _valid_custom_op_final_gate()
    _write_custom_op_manifest(tmp_path)
    _write_strict_opp_fixture(tmp_path)
    rows = cast(list[dict[str, object]], payload["rows"])
    _ = rows[0].pop("public_api_route_evidence")
    rows[0]["framework_integration_route_evidence"] = []

    result = validate_custom_op_final_gate(payload, project_root=tmp_path)

    assert result["passed"] is False
    assert any("framework_integration_route_evidence must be a non-empty object list" in error for error in result["errors"])


def test_custom_op_final_gate_rejects_empty_public_api_route_evidence_list(tmp_path: Path) -> None:
    payload = _valid_custom_op_final_gate()
    _write_custom_op_manifest(tmp_path)
    _write_strict_opp_fixture(tmp_path)
    rows = cast(list[dict[str, object]], payload["rows"])
    rows[0]["public_api_route_evidence"] = []

    result = validate_custom_op_final_gate(payload, project_root=tmp_path)

    assert result["passed"] is False
    assert any("public_api_route_evidence must be a non-empty object list" in error for error in result["errors"])


def test_custom_op_final_gate_rejects_non_object_framework_integration_route_evidence_item(tmp_path: Path) -> None:
    payload = _valid_custom_op_final_gate()
    _write_custom_op_manifest(tmp_path)
    _write_strict_opp_fixture(tmp_path)
    rows = cast(list[dict[str, object]], payload["rows"])
    _ = rows[0].pop("public_api_route_evidence")
    rows[0]["framework_integration_route_evidence"] = [
        {
            "unit_identity": "ScalarFwd2D",
            "route_type": "framework_integration",
            "entrypoint": "ScalarForward.apply",
            "same_run": True,
            "custom_call_count": 3,
            "framework_integration_invoked": True,
            "native_custom_op_route_executed": True,
        },
        "ScalarForward.backward",
    ]

    result = validate_custom_op_final_gate(payload, project_root=tmp_path)

    assert result["passed"] is False
    assert any("framework_integration_route_evidence[1] must be an object" in error for error in result["errors"])


def test_custom_op_final_gate_rejects_non_object_public_api_route_evidence_item(tmp_path: Path) -> None:
    payload = _valid_custom_op_final_gate()
    _write_custom_op_manifest(tmp_path)
    _write_strict_opp_fixture(tmp_path)
    rows = cast(list[dict[str, object]], payload["rows"])
    rows[0]["public_api_route_evidence"] = [
        {
            "unit_identity": "ScalarFwd2D",
            "route_type": "public_api",
            "entrypoint": "deepwave.scalar",
            "same_run": True,
            "custom_call_count": 3,
            "public_api_invoked": True,
            "native_custom_op_route_executed": True,
        },
        "deepwave.scalar_backward",
    ]

    result = validate_custom_op_final_gate(payload, project_root=tmp_path)

    assert result["passed"] is False
    assert any("public_api_route_evidence[1] must be an object" in error for error in result["errors"])


def test_custom_op_final_gate_rejects_invalid_framework_integration_route_evidence_list_item(tmp_path: Path) -> None:
    payload = _valid_custom_op_final_gate()
    _write_custom_op_manifest(tmp_path)
    _write_strict_opp_fixture(tmp_path)
    rows = cast(list[dict[str, object]], payload["rows"])
    _ = rows[0].pop("public_api_route_evidence")
    rows[0]["framework_integration_route_evidence"] = [
        {
            "unit_identity": "ScalarFwd2D",
            "route_type": "framework_integration",
            "entrypoint": "ScalarForward.apply",
            "same_run": True,
            "custom_call_count": 3,
            "framework_integration_invoked": True,
            "native_custom_op_route_executed": True,
        },
        {
            "unit_identity": "ScalarFwd2D",
            "route_type": "framework_integration",
            "entrypoint": "ScalarForward.backward",
            "same_run": False,
            "custom_call_count": 2,
            "framework_entry_invoked": True,
            "native_custom_op_route_executed": True,
        },
    ]

    result = validate_custom_op_final_gate(payload, project_root=tmp_path)

    assert result["passed"] is False
    assert any("framework_integration_route_evidence[1] must prove same_run=true" in error for error in result["errors"])


def test_custom_op_final_gate_rejects_invalid_public_api_route_evidence_list_item(tmp_path: Path) -> None:
    payload = _valid_custom_op_final_gate()
    _write_custom_op_manifest(tmp_path)
    _write_strict_opp_fixture(tmp_path)
    rows = cast(list[dict[str, object]], payload["rows"])
    rows[0]["public_api_route_evidence"] = [
        {
            "unit_identity": "ScalarFwd2D",
            "route_type": "public_api",
            "entrypoint": "deepwave.scalar",
            "same_run": True,
            "custom_call_count": 3,
            "public_api_invoked": True,
            "native_custom_op_route_executed": True,
        },
        {
            "unit_identity": "ScalarFwd2D",
            "route_type": "public_api",
            "entrypoint": "deepwave.scalar_backward",
            "same_run": False,
            "custom_call_count": 2,
            "public_api_invoked": True,
            "native_custom_op_route_executed": True,
        },
    ]

    result = validate_custom_op_final_gate(payload, project_root=tmp_path)

    assert result["passed"] is False
    assert any("public_api_route_evidence[1] must prove same_run=true" in error for error in result["errors"])


def test_custom_op_final_gate_rejects_valid_public_route_with_invalid_framework_route(tmp_path: Path) -> None:
    payload = _valid_custom_op_final_gate()
    _write_custom_op_manifest(tmp_path)
    _write_strict_opp_fixture(tmp_path)
    rows = cast(list[dict[str, object]], payload["rows"])
    rows[0]["framework_integration_route_evidence"] = [
        {
            "unit_identity": "ScalarFwd2D",
            "route_type": "framework_integration",
            "entrypoint": "ScalarForward.apply",
            "same_run": False,
            "custom_call_count": 3,
            "framework_entry_invoked": True,
            "native_custom_op_route_executed": True,
        }
    ]

    result = validate_custom_op_final_gate(payload, project_root=tmp_path)

    assert result["passed"] is False
    assert any("framework_integration_route_evidence[0] must prove same_run=true" in error for error in result["errors"])


def test_custom_op_final_gate_rejects_missing_route_evidence(tmp_path: Path) -> None:
    payload = _valid_custom_op_final_gate()
    _write_custom_op_manifest(tmp_path)
    _write_strict_opp_fixture(tmp_path)
    rows = cast(list[dict[str, object]], payload["rows"])
    _ = rows[0].pop("public_api_route_evidence")

    result = validate_custom_op_final_gate(payload, project_root=tmp_path)

    assert result["passed"] is False
    assert any("public_api_route_evidence or framework_integration_route_evidence" in error for error in result["errors"])


def test_custom_op_final_gate_rejects_zero_call_route_evidence(tmp_path: Path) -> None:
    payload = _valid_custom_op_final_gate()
    _write_custom_op_manifest(tmp_path)
    _write_strict_opp_fixture(tmp_path)
    rows = cast(list[dict[str, object]], payload["rows"])
    route = cast(dict[str, object], rows[0]["public_api_route_evidence"])
    route["custom_call_count"] = 0

    result = validate_custom_op_final_gate(payload, project_root=tmp_path)

    assert result["passed"] is False
    assert any("public_api_route_evidence must include custom call count > 0" in error for error in result["errors"])


def test_custom_op_final_gate_rejects_public_route_without_public_entry_invocation(tmp_path: Path) -> None:
    payload = _valid_custom_op_final_gate()
    _write_custom_op_manifest(tmp_path)
    _write_strict_opp_fixture(tmp_path)
    rows = cast(list[dict[str, object]], payload["rows"])
    route = cast(dict[str, object], rows[0]["public_api_route_evidence"])
    _ = route.pop("public_api_invoked")
    route["custom_op_route_executed"] = True

    result = validate_custom_op_final_gate(payload, project_root=tmp_path)

    assert result["passed"] is False
    assert any("public_api_route_evidence must prove public/project API entry invocation" in error for error in result["errors"])


def test_custom_op_final_gate_rejects_framework_route_without_framework_entry_invocation(tmp_path: Path) -> None:
    payload = _valid_custom_op_final_gate()
    _write_custom_op_manifest(tmp_path)
    _write_strict_opp_fixture(tmp_path)
    rows = cast(list[dict[str, object]], payload["rows"])
    _ = rows[0].pop("public_api_route_evidence")
    rows[0]["framework_integration_route_evidence"] = {
        "unit_identity": "ScalarFwd2D",
        "route_type": "framework_integration",
        "entrypoint": "ScalarForward.apply",
        "same_run": True,
        "custom_call_count": 3,
        "custom_op_route_executed": True,
        "native_custom_op_route_executed": True,
    }

    result = validate_custom_op_final_gate(payload, project_root=tmp_path)

    assert result["passed"] is False
    assert any("framework_integration_route_evidence must prove framework integration entry invocation" in error for error in result["errors"])


def test_custom_op_final_gate_rejects_direct_or_builtin_only_route_evidence(tmp_path: Path) -> None:
    payload = _valid_custom_op_final_gate()
    _write_custom_op_manifest(tmp_path)
    _write_strict_opp_fixture(tmp_path)
    rows = cast(list[dict[str, object]], payload["rows"])
    route = cast(dict[str, object], rows[0]["public_api_route_evidence"])
    route["direct_only"] = True
    route["builtin_only"] = True

    result = validate_custom_op_final_gate(payload, project_root=tmp_path)

    assert result["passed"] is False
    assert any("direct-only, builtin-only" in error for error in result["errors"])


def test_custom_op_final_gate_rejects_route_evidence_row_mismatch(tmp_path: Path) -> None:
    payload = _valid_custom_op_final_gate()
    _write_custom_op_manifest(tmp_path)
    _write_strict_opp_fixture(tmp_path)
    rows = cast(list[dict[str, object]], payload["rows"])
    route = cast(dict[str, object], rows[0]["public_api_route_evidence"])
    route["unit_identity"] = "ScalarBwd2D"

    result = validate_custom_op_final_gate(payload, project_root=tmp_path)

    assert result["passed"] is False
    assert any("identity must match the manifest row identity" in error for error in result["errors"])


def test_non_custom_final_validation_does_not_require_route_evidence() -> None:
    result = validate_validation_final({"success": True, "iteration_count": 0, "errors": []})

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
    performance_report["baseline_device"] = "cpu"
    performance_report["custom_device"] = "ascend_opp"
    performance_report["overall_baseline_device"] = "torch.cpu"
    performance_report["overall_custom_device"] = "ascend_opp"
    entries = cast(list[dict[str, object]], performance_report["entries"])
    entries[0]["baseline_device"] = "cpu"
    entries[0]["custom_device"] = "ascend_opp_custom_op"
    rows = cast(list[dict[str, object]], payload["rows"])
    performance_evidence = cast(dict[str, object], rows[0]["performance_evidence"])
    performance_evidence["baseline_device"] = "torch.cpu"
    performance_evidence["custom_device"] = "ascend_opp"

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



def test_custom_op_final_gate_rejects_same_npu_self_baseline_placeholder(tmp_path: Path) -> None:
    payload = _valid_custom_op_final_gate()
    _write_custom_op_manifest(tmp_path)
    _write_strict_opp_fixture(tmp_path)
    performance_report = cast(dict[str, object], payload["performance_report"])
    performance_report.update({
        "baseline_device": "npu",
        "custom_device": "npu",
        "overall_baseline_device": "torch_npu.npu",
        "overall_custom_device": "torch_npu.npu",
        "overall_baseline_seconds": 0.04,
        "overall_custom_seconds": 0.04,
        "overall_speedup_vs_baseline": 1.0,
        "same_npu_baseline": True,
    })
    entries = cast(list[dict[str, object]], performance_report["entries"])
    entries[0].update({
        "baseline_device": "npu",
        "custom_device": "npu",
        "baseline_seconds": 0.01,
        "custom_seconds": 0.01,
        "speedup_vs_baseline": 1.0,
        "same_route_baseline": True,
    })
    rows = cast(list[dict[str, object]], payload["rows"])
    performance_evidence = cast(dict[str, object], rows[0]["performance_evidence"])
    performance_evidence.update({
        "baseline_device": "npu",
        "custom_device": "npu",
        "baseline_seconds": 0.01,
        "custom_seconds": 0.01,
        "speedup_vs_baseline": 1.0,
        "self_baseline": True,
    })

    result = validate_custom_op_final_gate(payload, project_root=tmp_path)

    assert result["passed"] is False
    assert any("CPU baseline" in error and "Ascend OPP/custom-op" in error for error in result["errors"])
    assert any("same-NPU" in error or "self-baseline" in error for error in result["errors"])


def test_custom_op_final_gate_rejects_speedup_formula_mismatch(tmp_path: Path) -> None:
    payload = _valid_custom_op_final_gate()
    _write_custom_op_manifest(tmp_path)
    _write_strict_opp_fixture(tmp_path)
    performance_report = cast(dict[str, object], payload["performance_report"])
    performance_report["overall_baseline_seconds"] = 0.09
    performance_report["overall_custom_seconds"] = 0.03
    performance_report["overall_speedup_vs_baseline"] = 1.0
    entries = cast(list[dict[str, object]], performance_report["entries"])
    entries[0]["baseline_seconds"] = 0.09
    entries[0]["custom_seconds"] = 0.03
    entries[0]["speedup_vs_baseline"] = 1.0
    rows = cast(list[dict[str, object]], payload["rows"])
    performance_evidence = cast(dict[str, object], rows[0]["performance_evidence"])
    performance_evidence["baseline_seconds"] = 0.09
    performance_evidence["custom_seconds"] = 0.03
    performance_evidence["speedup_vs_baseline"] = 1.0

    result = validate_custom_op_final_gate(payload, project_root=tmp_path)

    assert result["passed"] is False
    assert any("baseline_seconds / custom_seconds" in error for error in result["errors"])

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
        route = dict(cast(dict[str, object], row["public_api_route_evidence"]))
        route.update({"unit_identity": unit_name, "entrypoint": "pkg.ops.alpha"})
        row["public_api_route_evidence"] = route
        rows.append(row)
        performance_entries.append(
            {
                "unit_identity": unit_name,
                "baseline_seconds": 0.02,
                "custom_seconds": 0.01,
                "speedup_vs_baseline": 2.0,
                "project_api_invoked": True,
                "baseline_device": "cpu",
                "custom_device": "ascend_opp",
            }
        )
    source_inventory["entries"] = entries
    payload["rows"] = rows
    payload["performance_report"] = {
        "complete": True,
        "unit_count": 2,
        "path": "migration_reports/performance.json",
        "project_api_invoked": True,
        "baseline_device": "cpu",
        "custom_device": "ascend_opp",
        "overall_baseline_seconds": 0.05,
        "overall_custom_seconds": 0.04,
        "overall_speedup_vs_baseline": 1.25,
        "overall_baseline_device": "cpu",
        "overall_custom_device": "ascend_opp",
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


def test_entry_script_validator_allows_reject_smoke_checks_in_full_contract(tmp_path: Path) -> None:
    script_path = tmp_path / "validate_custom_ops_full.py"
    _ = script_path.write_text("print('custom-op validation')\n", encoding="utf-8")
    payload = _valid_custom_op_contract(str(script_path))
    checks = cast(list[str], payload["required_checks"])
    checks.append("reject_smoke_mvp_partial_success")
    obligations = cast(list[str], payload["validation_obligations"])
    obligations.remove("numeric_performance")
    obligations.append("numeric_performance_for_each_row")
    obligations.append("reject_empty_or_partially_invalid_route_evidence_lists")

    result = validate_entry_script(payload)

    assert result == {"passed": True, "errors": [], "warnings": []}


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
            "script_requires_per_row_route_evidence": True,
            "script_correlates_route_evidence_to_manifest_rows": True,
            "script_rejects_direct_or_builtin_only_routes": True,
            "script_rejects_report_only_success": True,
            "script_requires_project_local_artifacts": True,
            "script_requires_project_root_artifact_existence": True,
            "script_requires_numeric_performance": True,
            "script_checks_no_fallback": True,
        }
    )

    assert result == {"passed": True, "errors": [], "warnings": []}


def test_entry_static_validator_rejects_short_project_e2e_subprocess_timeout(tmp_path: Path) -> None:
    script_path = tmp_path / "validate_custom_ops_full.py"
    _ = script_path.write_text(
        """
import subprocess
import sys

def run_project_api_e2e():
    return subprocess.run([sys.executable, 'test_e2e_fwi.py'], timeout=600, check=False)
""".strip(),
        encoding="utf-8",
    )

    result = validate_entry_static(
        {
            "validation_passed": True,
            "issues": [],
            "fix_plan": "Script satisfies the mandatory custom-op target.",
            "entry_script_path": str(script_path),
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
            "script_requires_per_row_route_evidence": True,
            "script_correlates_route_evidence_to_manifest_rows": True,
            "script_rejects_direct_or_builtin_only_routes": True,
            "script_rejects_report_only_success": True,
            "script_requires_project_local_artifacts": True,
            "script_requires_project_root_artifact_existence": True,
            "script_requires_numeric_performance": True,
            "script_checks_no_fallback": True,
        }
    )

    assert result["passed"] is False
    assert any("short internal subprocess timeout=600" in error for error in result["errors"])


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
            "script_requires_per_row_route_evidence": True,
            "script_correlates_route_evidence_to_manifest_rows": True,
            "script_rejects_direct_or_builtin_only_routes": True,
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
            "script_requires_per_row_route_evidence": True,
            "script_correlates_route_evidence_to_manifest_rows": True,
            "script_rejects_direct_or_builtin_only_routes": True,
            "script_rejects_report_only_success": True,
            "script_requires_project_local_artifacts": True,
            "script_requires_project_root_artifact_existence": True,
            "script_requires_numeric_performance": True,
            "script_checks_no_fallback": True,
        }
    )

    assert result["passed"] is False
    assert any("script_records_native_operator_symbols" in error for error in result["errors"])


def test_entry_static_validator_requires_route_evidence_booleans() -> None:
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
            "script_runs_project_api_custom_ops": True,
            "script_rejects_report_only_success": True,
            "script_requires_project_local_artifacts": True,
            "script_requires_project_root_artifact_existence": True,
            "script_requires_numeric_performance": True,
            "script_checks_no_fallback": True,
        }
    )

    assert result["passed"] is False
    assert any("script_requires_per_row_route_evidence" in error for error in result["errors"])
    assert any("script_correlates_route_evidence_to_manifest_rows" in error for error in result["errors"])
    assert any("script_rejects_direct_or_builtin_only_routes" in error for error in result["errors"])


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


def _deepwave_variant_unit_identities() -> list[str]:
    units: list[str] = []
    for base, accuracies in (("scalar_forward", [2, 4, 6, 8]), ("elastic_forward", [2, 4])):
        for ndim in (1, 2, 3):
            for accuracy in accuracies:
                for dtype in ("float", "double"):
                    units.append(f"{base}:ndim={ndim}:accuracy={accuracy}:dtype={dtype}")
    return units


def _deepwave_variant_surface() -> dict[str, object]:
    units = _deepwave_variant_unit_identities()
    surface = _valid_project_analysis_custom_op_surface()
    surface["operator_families"] = ["scalar", "elastic"]
    surface["fine_grained_operator_units"] = units
    surface["discovered_operator_names"] = [unit.replace(":", "_").replace("=", "_") for unit in units]
    surface["native_operator_symbols"] = [unit.split(":", 1)[0] for unit in units]
    surface["kernel_launch_sites"] = [f"deepwave_original_src/{unit.split(':', 1)[0]}.cu:{unit}" for unit in units]
    surface["source_evidence"] = [f"deepwave_original_src/{unit.split(':', 1)[0]}.cu:{unit}" for unit in units]
    surface["variant_axes_detected"] = True
    surface["variant_axes"] = {"ndim": [1, 2, 3], "accuracy": [2, 4, 6, 8], "dtype": ["float", "double"]}
    surface["expanded_operator_instances_count"] = len(units)
    variants: list[dict[str, object]] = []
    for unit in units:
        parts = dict(part.split("=", 1) for part in unit.split(":")[1:])
        base = unit.split(":", 1)[0]
        variants.append({
            "unit_identity": unit,
            "base_unit_identity": base,
            "axis_values": parts,
            "source_evidence": [f"deepwave_original_src/{base}.cu:{unit}"],
            "candidate_public_api_routes": [f"deepwave.{base}"],
            "candidate_framework_integration_routes": [f"deepwave.{base}.autograd"],
        })
    surface["expanded_operator_variants"] = variants
    surface["fine_grained_operator_unit_evidence"] = [
        {
            "unit_identity": unit,
            "source_evidence": [f"deepwave_original_src/{unit.split(':', 1)[0]}.cu:{unit}"],
            "candidate_public_api_routes": [f"deepwave.{unit.split(':', 1)[0]}"],
        }
        for unit in units
    ]
    return surface


def _heterogeneous_deepwave_variant_surface() -> dict[str, object]:
    wave_units = [
        f"wave_forward:ndim={ndim}:accuracy={accuracy}:dtype={dtype}:device=cuda"
        for ndim in (1, 2, 3)
        for accuracy in (2, 4, 6, 8)
        for dtype in ("float", "double")
    ]
    storage_units = [
        f"storage_compress:ndim={ndim}:dtype={dtype}"
        for ndim in (1, 2, 3)
        for dtype in ("float", "double")
    ]
    units = [*wave_units, *storage_units]
    surface = _valid_project_analysis_custom_op_surface()
    surface["operator_families"] = ["wave", "storage"]
    surface["fine_grained_operator_units"] = ["wave_forward", "storage_compress"]
    surface["discovered_operator_names"] = [unit.replace(":", "_").replace("=", "_") for unit in units]
    surface["native_operator_symbols"] = [
        "wave_${ndim}_${accuracy}_${dtype}_${device}_forward",
        "storage_compress_${ndim}_${dtype}",
    ]
    surface["kernel_launch_sites"] = ["src/wave.cu:wave_kernel<<<...>>>", "src/storage.cu:storage_kernel<<<...>>>"]
    surface["source_evidence"] = [
        "src/backend.py:enumerates ndim 1, 2, 3",
        "src/backend.py:enumerates accuracy 2, 4, 6, 8 for wave_forward",
        "src/backend.py:enumerates dtype float and double",
        "src/backend.py:builds wave symbols with ${device}",
        "src/storage.py:storage_compress uses ndim and dtype only",
    ]
    surface["variant_axes_detected"] = True
    surface["variant_axes"] = {"ndim": [1, 2, 3], "accuracy": [2, 4, 6, 8], "dtype": ["float", "double"], "device": ["cuda"]}
    variants: list[dict[str, object]] = []
    for unit in wave_units:
        parts = dict(part.split("=", 1) for part in unit.split(":")[1:])
        variants.append({
            "unit_identity": unit,
            "base_unit_identity": "wave_forward",
            "axis_values": parts,
            "source_evidence": [f"src/backend.py:generated wave variant {unit}"],
            "candidate_public_api_routes": ["pkg.wave.forward"],
        })
    for unit in storage_units:
        parts = dict(part.split("=", 1) for part in unit.split(":")[1:])
        variants.append({
            "unit_identity": unit,
            "base_unit_identity": "storage_compress",
            "axis_values": parts,
            "source_evidence": [f"src/storage.py:generated storage variant {unit}"],
            "candidate_public_api_routes": ["pkg.storage.compress"],
        })
    surface["expanded_operator_variants"] = variants
    surface["expanded_operator_instances_count"] = len(variants)
    surface["fine_grained_operator_unit_evidence"] = [
        {
            "unit_identity": "wave_forward",
            "source_evidence": ["src/wave.cu:exports wave_forward"],
            "candidate_public_api_routes": ["pkg.wave.forward"],
        },
        {
            "unit_identity": "storage_compress",
            "source_evidence": ["src/storage.cu:exports storage_compress"],
            "candidate_public_api_routes": ["pkg.storage.compress"],
        },
    ]
    return surface


def _pointnet2_public_units() -> list[str]:
    return [
        "gather_points",
        "gather_points_grad",
        "furthest_point_sampling",
        "three_nn",
        "three_interpolate",
        "three_interpolate_grad",
        "ball_query",
        "group_points",
        "group_points_grad",
    ]


def _pointnet2_boundary_surface() -> dict[str, object]:
    units = _pointnet2_public_units()
    surface = _valid_project_analysis_custom_op_surface()
    surface["operator_families"] = ["sampling", "grouping", "interpolation"]
    surface["fine_grained_operator_units"] = units
    surface["discovered_operator_names"] = units
    surface["native_operator_symbols"] = units
    surface["kernel_launch_sites"] = [
        "src/sampling_gpu.cu:furthest_point_sampling_kernel<<<...>>>",
        "src/sampling_gpu.cu:gather_points_kernel_wrapper launches gather_points_kernel<<<...>>>",
        "src/grouping_gpu.cu:ball_query_kernel<<<...>>>",
        "src/grouping_gpu.cu:group_points_kernel_wrapper launches group_points_kernel<<<...>>>",
        "src/interpolate_gpu.cu:three_nn_kernel<<<...>>>",
        "src/interpolate_gpu.cu:three_interpolate_kernel<<<...>>>",
    ]
    surface["source_evidence"] = [
        "src/sampling.cpp:PYBIND11_MODULE exposes gather_points, gather_points_grad, furthest_point_sampling",
        "src/grouping.cpp:PYBIND11_MODULE exposes ball_query, group_points, group_points_grad",
        "src/interpolate.cpp:PYBIND11_MODULE exposes three_nn, three_interpolate, three_interpolate_grad",
        "src/sampling_gpu.cu:opt_n_threads and CUDA_CHECK_ERRORS used by sampling wrappers",
        "src/grouping_gpu.cu:CHECK_CUDA and block-size launch heuristics used by grouping wrappers",
    ]
    surface["negative_evidence"] = ["source search found no additional public/native boundary operators"]
    surface["variant_axes_detected"] = False
    surface["variant_axes"] = {}
    surface["expanded_operator_variants"] = []
    surface["expanded_operator_instances_count"] = 0
    surface["fine_grained_operator_unit_evidence"] = [
        {
            "unit_identity": unit,
            "source_evidence": [
                f"src/bindings.cpp:public/native boundary export {unit}",
                f"src/{unit}.cu:internal kernels/wrappers/check macros recorded as evidence for {unit}",
            ],
            "candidate_public_api_routes": [f"pointnet2_ops.{unit}"],
        }
        for unit in units
    ]
    return surface


def _variant_final_gate_payload(units: list[str]) -> dict[str, object]:
    payload = _valid_custom_op_final_gate()
    template_row = cast(list[dict[str, object]], payload["rows"])[0]
    source_entries = cast(list[dict[str, object]], cast(dict[str, object], payload["source_inventory"])["entries"])
    template_source = source_entries[0]
    template_perf = cast(list[dict[str, object]], cast(dict[str, object], payload["performance_report"])["entries"])[0]
    rows: list[dict[str, object]] = []
    sources: list[dict[str, object]] = []
    perf_entries: list[dict[str, object]] = []
    coverage_entries: list[dict[str, object]] = []
    for unit in units:
        row = dict(template_row)
        row["row_id"] = unit
        row["unit_identity"] = unit
        row["variant_or_signature"] = unit
        route = dict(cast(dict[str, object], template_row["public_api_route_evidence"]))
        route["unit_identity"] = unit
        row["public_api_route_evidence"] = route
        coverage = dict(cast(dict[str, object], template_row["same_run_runtime_coverage"]))
        coverage["unit_identity"] = unit
        row["same_run_runtime_coverage"] = coverage
        rows.append(row)
        source = dict(template_source)
        source["name"] = unit
        source["unit_identity"] = unit
        source["variant_or_signature"] = unit
        sources.append(source)
        perf = dict(template_perf)
        perf["unit_identity"] = unit
        perf_entries.append(perf)
        coverage_entries.append(coverage)
    payload["inventory_count"] = len(units)
    payload["manifest_entries"] = len(units)
    payload["closed_pass_entries"] = len(units)
    payload["rows"] = rows
    cast(dict[str, object], payload["source_inventory"])["entries"] = sources
    performance = cast(dict[str, object], payload["performance_report"])
    performance["unit_count"] = len(units)
    performance["entries"] = perf_entries
    payload["runtime_coverage_report"] = {
        "complete": True,
        "unit_count": len(units),
        "path": "migration_reports/runtime_coverage.json",
        "same_run": True,
        "project_api_invoked": True,
        "native_custom_op_route_executed": True,
        "entries": coverage_entries,
    }
    payload["expanded_variant_inventory"] = {
        "variant_axes_detected": True,
        "unit_identities": units,
        "expanded_operator_instances_count": len(units),
    }
    return payload


def test_project_analysis_accepts_deepwave_like_expanded_variant_metadata() -> None:
    result = validate_project_analysis(
        {
            "project_dir": "/tmp/project",
            "dependencies": ["torch"],
            "cuda_detected": True,
            "entry_script": "validate_custom_ops_full.py",
            "custom_op_surface": _deepwave_variant_surface(),
        }
    )

    assert result == {"passed": True, "errors": [], "warnings": []}


def test_project_analysis_accepts_heterogeneous_expanded_variant_axis_subsets() -> None:
    result = validate_project_analysis(
        {
            "project_dir": "/tmp/project",
            "dependencies": ["torch"],
            "cuda_detected": True,
            "entry_script": "validate_custom_ops_full.py",
            "custom_op_surface": _heterogeneous_deepwave_variant_surface(),
        }
    )

    assert result == {"passed": True, "errors": [], "warnings": []}


def test_project_analysis_rejects_candidate6_like_underexpanded_heterogeneous_inventory() -> None:
    surface = _heterogeneous_deepwave_variant_surface()
    variants: list[dict[str, object]] = []
    for index in range(9):
        variants.append({
            "unit_identity": f"wave_forward_{index}:ndim=1:accuracy=2:dtype=float:device=cuda",
            "base_unit_identity": f"wave_forward_{index}",
            "axis_values": {"ndim": "1", "accuracy": "2", "dtype": "float", "device": "cuda"},
            "source_evidence": [f"src/backend.py:sample wave variant {index}"],
            "candidate_public_api_routes": ["pkg.wave.forward"],
        })
    for index in range(4):
        variants.append({
            "unit_identity": f"storage_compress_{index}:ndim=1:dtype=float",
            "base_unit_identity": f"storage_compress_{index}",
            "axis_values": {"ndim": "1", "dtype": "float"},
            "source_evidence": [f"src/storage.py:sample storage variant {index}"],
            "candidate_public_api_routes": ["pkg.storage.compress"],
        })
    surface["expanded_operator_variants"] = variants
    surface["expanded_operator_instances_count"] = len(variants) + 1
    surface["fine_grained_operator_unit_evidence"] = [
        {
            "unit_identity": cast(str, variant["base_unit_identity"]),
            "source_evidence": cast(list[str], variant["source_evidence"]),
            "candidate_public_api_routes": cast(list[str], variant["candidate_public_api_routes"]),
        }
        for variant in variants
    ]

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
    assert any("expanded_operator_instances_count must equal expanded_operator_variants length" in error for error in result["errors"])
    assert any("axis_values.ndim missing source-enumerated axis values" in error and "2d" in error and "3d" in error for error in result["errors"])
    assert any("axis_values.accuracy missing source-enumerated axis values" in error and "6" in error and "8" in error for error in result["errors"])
    assert any("axis_values.dtype missing source-enumerated axis values" in error and "double" in error for error in result["errors"])
    assert not any("axis_values must match variant_axes" in error for error in result["errors"])


def test_project_analysis_rejects_sampled_per_base_generated_symbol_combinations(tmp_path: Path) -> None:
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    _ = (source_dir / "backend_utils.py").write_text(
        """def get_backend_function(propagator, ndim, accuracy, dtype, pass_name):
    return getattr(dll, f'{propagator}_iso_{ndim}d_{accuracy}_{dtype}_{pass_name}_cuda')
for current_ndim in [1, 2, 3]:
    for current_accuracy in [2, 4, 6, 8]:
        for current_dtype in ['float', 'double']:
            _assign_argtypes('alpha', current_ndim, current_accuracy, current_dtype, 'forward')
            _assign_argtypes('beta', current_ndim, current_accuracy, current_dtype, 'forward')
""",
        encoding="utf-8",
    )
    alpha_units = [
        f"alpha:forward_cuda:ndim={ndim}:accuracy={accuracy}:dtype={dtype}:device=cuda"
        for ndim in ("1d", "2d", "3d")
        for accuracy in ("2", "4", "6", "8")
        for dtype in ("float", "double")
    ]
    beta_units = [
        "beta:forward_cuda:ndim=1d:accuracy=2:dtype=float:device=cuda",
        "beta:forward_cuda:ndim=2d:accuracy=4:dtype=float:device=cuda",
        "beta:forward_cuda:ndim=3d:accuracy=8:dtype=double:device=cuda",
    ]
    surface = _valid_project_analysis_custom_op_surface()
    surface["searched_source_roots"] = ["src"]
    surface["searched_source_paths"] = ["src/backend_utils.py"]
    surface["operator_families"] = ["alpha", "beta"]
    surface["fine_grained_operator_units"] = ["alpha:forward_cuda", "beta:forward_cuda"]
    surface["discovered_operator_names"] = [
        unit.replace(":", "_").replace("=", "_") for unit in [*alpha_units, *beta_units]
    ]
    surface["native_operator_symbols"] = [
        "alpha_iso_${ndim}d_${accuracy}_${dtype}_forward_cuda",
        "beta_iso_${ndim}d_${accuracy}_${dtype}_forward_cuda",
    ]
    surface["kernel_launch_sites"] = ["src/alpha.cu:forward_kernel<<<...>>>", "src/beta.cu:forward_kernel<<<...>>>"]
    surface["source_evidence"] = ["src/backend_utils.py:1 generated backend symbol names"]
    surface["variant_axes_detected"] = True
    surface["variant_axes"] = {"ndim": ["1d", "2d", "3d"], "accuracy": ["2", "4", "6", "8"], "dtype": ["float", "double"], "device": ["cuda"]}

    variants: list[dict[str, object]] = []
    for unit in [*alpha_units, *beta_units]:
        base = unit.split(":ndim=", 1)[0]
        variants.append({
            "unit_identity": unit,
            "base_unit_identity": base,
            "axis_values": dict(part.split("=", 1) for part in unit.split(":") if "=" in part),
            "source_evidence": ["src/backend_utils.py:1 generated backend symbol names"],
            "candidate_public_api_routes": [f"pkg.{base.split(':', 1)[0]}.forward"],
        })
    surface["expanded_operator_variants"] = variants
    surface["expanded_operator_instances_count"] = len(variants)
    surface["fine_grained_operator_unit_evidence"] = [
        {
            "unit_identity": unit,
            "source_evidence": ["src/backend_utils.py:1 generated backend symbol names"],
            "candidate_public_api_routes": [f"pkg.{unit.split(':', 1)[0]}.forward"],
        }
        for unit in surface["fine_grained_operator_units"]
    ]

    result = validate_project_analysis(
        {
            "project_dir": str(tmp_path),
            "dependencies": ["torch"],
            "cuda_detected": True,
            "entry_script": "validate_custom_ops_full.py",
            "custom_op_surface": surface,
        }
    )

    assert result["passed"] is False
    assert any(
        "per-base axis combinations for beta:forward_cuda" in error and "accuracy=6" in error
        for error in result["errors"]
    )
    assert not any("per-base axis combinations for alpha:forward_cuda" in error for error in result["errors"])


def test_project_analysis_rejects_sampled_generic_template_axis_combinations() -> None:
    surface = _valid_project_analysis_custom_op_surface()
    surface["operator_families"] = ["solver"]
    surface["fine_grained_operator_units"] = ["solver:apply_cuda"]
    surface["discovered_operator_names"] = ["solver_${boundary_condition}_${mode}_apply_cuda"]
    surface["native_operator_symbols"] = ["solver_${boundary_condition}_${mode}_apply_cuda"]
    surface["kernel_launch_sites"] = ["src/solver.cu:apply_kernel<<<...>>>"]
    surface["source_evidence"] = ["src/register.py:generated symbols use ${boundary_condition} and ${mode}"]
    surface["variant_axes_detected"] = True
    surface["variant_axes"] = {"boundary_condition": ["absorbing", "periodic"], "mode": ["fast", "accurate"], "device": ["cuda"]}
    surface["expanded_operator_variants"] = [
        {
            "unit_identity": "solver:apply_cuda:boundary_condition=absorbing:mode=fast:device=cuda",
            "base_unit_identity": "solver:apply_cuda",
            "axis_values": {"boundary_condition": "absorbing", "mode": "fast", "device": "cuda"},
            "source_evidence": ["src/register.py:generated symbols use ${boundary_condition} and ${mode}"],
            "candidate_public_api_routes": ["pkg.solver.apply"],
        },
        {
            "unit_identity": "solver:apply_cuda:boundary_condition=periodic:mode=accurate:device=cuda",
            "base_unit_identity": "solver:apply_cuda",
            "axis_values": {"boundary_condition": "periodic", "mode": "accurate", "device": "cuda"},
            "source_evidence": ["src/register.py:generated symbols use ${boundary_condition} and ${mode}"],
            "candidate_public_api_routes": ["pkg.solver.apply"],
        },
    ]
    surface["expanded_operator_instances_count"] = 2
    surface["fine_grained_operator_unit_evidence"] = [
        {
            "unit_identity": "solver:apply_cuda",
            "source_evidence": ["src/register.py:generated symbols use ${boundary_condition} and ${mode}"],
            "candidate_public_api_routes": ["pkg.solver.apply"],
        }
    ]

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
    assert any(
        "per-base axis combinations for solver:apply_cuda" in error
        and "boundary_condition=absorbing" in error
        and "mode=accurate" in error
        for error in result["errors"]
    )


def test_project_analysis_accepts_pointnet2_like_boundary_units_with_internal_launch_evidence() -> None:
    result = validate_project_analysis(
        {
            "project_dir": "/tmp/project",
            "dependencies": ["torch"],
            "cuda_detected": True,
            "entry_script": "test.py",
            "custom_op_surface": _pointnet2_boundary_surface(),
        }
    )

    assert result == {"passed": True, "errors": [], "warnings": []}


def test_project_analysis_rejects_semantic_generated_axes_without_expanded_variants() -> None:
    surface = _valid_project_analysis_custom_op_surface()
    units = ["scalar:forward_cuda", "scalar:backward_cuda", "simple_compress:compress_cuda"]
    surface["operator_families"] = ["scalar", "simple_compress"]
    surface["fine_grained_operator_units"] = units
    surface["discovered_operator_names"] = [unit.replace(":", "_") for unit in units]
    surface["native_operator_symbols"] = [
        "scalar_iso_${ndim}d_${accuracy}_${dtype}_forward_cuda",
        "scalar_iso_${ndim}d_${accuracy}_${dtype}_backward_cuda",
        "simple_compress_compress_${ndim}d_${dtype}",
    ]
    surface["kernel_launch_sites"] = [
        "src/scalar.cu:forward_kernel<<<...>>>",
        "src/scalar.cu:backward_kernel<<<...>>>",
        "src/simple_compress.cu:compress_kernel<<<...>>>",
    ]
    surface["source_evidence"] = [
        "src/backend_utils.py:builds symbols as {propagator}_iso_{ndim}d_{accuracy}_{dtype}_{pass}_{device}",
        "src/backend_utils.py:enumerates ndim 1, 2, 3",
        "src/backend_utils.py:enumerates accuracy 2, 4, 6, 8",
        "src/backend_utils.py:enumerates dtype float and double",
        "src/scalar.cu:exports FUNC(forward)",
    ]
    surface["variant_axes_detected"] = False
    surface["variant_axes"] = {}
    surface["expanded_operator_variants"] = []
    surface["expanded_operator_instances_count"] = 0
    surface["fine_grained_operator_unit_evidence"] = [
        {
            "unit_identity": unit,
            "source_evidence": ["src/backend_utils.py:generated backend symbols include ${ndim}, ${accuracy}, ${dtype}"],
            "candidate_public_api_routes": ["pkg.ops." + unit.split(":", 1)[0]],
        }
        for unit in units
    ]

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
    assert any("source-required semantic generated axes" in error for error in result["errors"])
    assert any("variant_axes_detected=true" in error for error in result["errors"])


def test_project_analysis_rejects_source_path_required_variants_when_metadata_false(tmp_path: Path) -> None:
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    _ = (source_dir / "backend_utils.py").write_text(
        """def get_backend_function(propagator, ndim, accuracy, dtype, device):
    dtype_str = 'float' if dtype == 'float32' else 'double'
    return getattr(dll, f'{propagator}_iso_{ndim}d_{accuracy}_{dtype_str}_forward_cuda')
for current_ndim in [1, 2, 3]:
    for current_accuracy in [2, 4, 6, 8]:
        for current_dtype in ['float', 'double']:
            register_backend(current_ndim, current_accuracy, current_dtype)
""",
        encoding="utf-8",
    )
    surface = _valid_project_analysis_custom_op_surface()
    surface["operator_families"] = ["wave"]
    surface["searched_source_roots"] = ["src"]
    surface["searched_source_paths"] = ["src/backend_utils.py"]
    surface["fine_grained_operator_units"] = ["wave:forward_cuda", "wave:backward_cuda"]
    surface["discovered_operator_names"] = ["wave_forward_cuda", "wave_backward_cuda"]
    surface["native_operator_symbols"] = ["wave:forward_cuda", "wave:backward_cuda"]
    surface["kernel_launch_sites"] = ["src/wave.cu:forward_kernel<<<...>>>"]
    surface["source_evidence"] = ["src/backend_utils.py:1 get_backend_function constructs backend symbol names"]
    surface["variant_axes_detected"] = False
    surface["variant_axes"] = {}
    surface["expanded_operator_variants"] = []
    surface["expanded_operator_instances_count"] = 0
    surface["fine_grained_operator_unit_evidence"] = [
        {
            "unit_identity": "wave:forward_cuda",
            "source_evidence": ["src/backend_utils.py:1 get_backend_function constructs backend symbol names"],
            "candidate_public_api_routes": ["pkg.wave.forward"],
        },
        {
            "unit_identity": "wave:backward_cuda",
            "source_evidence": ["src/backend_utils.py:1 get_backend_function constructs backend symbol names"],
            "candidate_public_api_routes": ["pkg.wave.forward"],
        },
    ]

    result = validate_project_analysis(
        {
            "project_dir": str(tmp_path),
            "dependencies": ["torch"],
            "cuda_detected": True,
            "entry_script": "test.py",
            "custom_op_surface": surface,
        }
    )

    assert result["passed"] is False
    assert any("source-required semantic generated axes" in error for error in result["errors"])
    assert any("accuracy" in error and "dtype" in error and "ndim" in error for error in result["errors"])
    assert any("variant_axes_detected=true" in error for error in result["errors"])


def test_project_analysis_ignores_unreferenced_search_path_semantic_words(tmp_path: Path) -> None:
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    _ = (source_dir / "common.py").write_text(
        """def validate_shape(ndims):
    if len(padding) != 2 * ndims:
        raise RuntimeError('Expected a list with 4 entries for this dimension')
""",
        encoding="utf-8",
    )
    surface = _valid_project_analysis_custom_op_surface()
    surface["operator_families"] = ["plain"]
    surface["searched_source_roots"] = ["src"]
    surface["searched_source_paths"] = ["src/common.py"]
    surface["fine_grained_operator_units"] = ["plain:forward_cuda"]
    surface["discovered_operator_names"] = ["plain_forward_cuda"]
    surface["native_operator_symbols"] = ["plain:forward_cuda"]
    surface["kernel_launch_sites"] = ["src/plain.cu:forward_kernel<<<...>>>"]
    surface["source_evidence"] = ["src/plain.cu:10 forward CUDA boundary"]
    surface["variant_axes_detected"] = False
    surface["variant_axes"] = {}
    surface["expanded_operator_variants"] = []
    surface["expanded_operator_instances_count"] = 0
    surface["fine_grained_operator_unit_evidence"] = [
        {
            "unit_identity": "plain:forward_cuda",
            "source_evidence": ["src/plain.cu:10 forward CUDA boundary"],
            "candidate_public_api_routes": ["pkg.plain.forward"],
        }
    ]

    result = validate_project_analysis(
        {
            "project_dir": str(tmp_path),
            "dependencies": ["torch"],
            "cuda_detected": True,
            "entry_script": "test.py",
            "custom_op_surface": surface,
        }
    )

    assert result == {"passed": True, "errors": [], "warnings": []}


def test_project_analysis_rejects_underexpanded_source_enumerated_axis_values() -> None:
    surface = _valid_project_analysis_custom_op_surface()
    unit = "scalar:forward:ndim=1d:accuracy=2:dtype=float:device=cuda"
    surface["operator_families"] = ["scalar"]
    surface["fine_grained_operator_units"] = [unit]
    surface["discovered_operator_names"] = ["scalar_forward_1d_2_float_cuda"]
    surface["native_operator_symbols"] = ["scalar_iso_${ndim}d_${accuracy}_${dtype}_forward_${device}"]
    surface["kernel_launch_sites"] = ["src/scalar.cu:forward_kernel<<<...>>>"]
    surface["source_evidence"] = [
        "src/backend_utils.py:builds generated symbol template propagator_iso_${ndim}d_${accuracy}_${dtype}_${pass}_${device}",
        "src/backend_utils.py:enumerates ndim 1, 2, 3",
        "src/backend_utils.py:enumerates accuracy 2, 4, 6, 8",
        "src/backend_utils.py:enumerates dtype float and double",
    ]
    surface["variant_axes_detected"] = True
    surface["variant_axes"] = {"ndim": ["1d"], "accuracy": ["2"], "dtype": ["float"], "device": ["cuda"]}
    surface["expanded_operator_variants"] = [
        {
            "unit_identity": unit,
            "base_unit_identity": "scalar:forward_cuda",
            "axis_values": {"ndim": "1d", "accuracy": "2", "dtype": "float", "device": "cuda"},
            "source_evidence": ["src/backend_utils.py:generated symbol ndim=1d accuracy=2 dtype=float device=cuda"],
            "candidate_public_api_routes": ["deepwave.scalar.scalar"],
        }
    ]
    surface["expanded_operator_instances_count"] = 1
    surface["fine_grained_operator_unit_evidence"] = [
        {
            "unit_identity": unit,
            "source_evidence": ["src/backend_utils.py:enumerates ndim 1, 2, 3; accuracy 2, 4, 6, 8; dtype float and double"],
            "candidate_public_api_routes": ["deepwave.scalar.scalar"],
        }
    ]

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
    assert any("variant_axes.ndim missing source-enumerated axis values" in error and "2d" in error and "3d" in error for error in result["errors"])
    assert any("variant_axes.accuracy missing source-enumerated axis values" in error and "4" in error and "8" in error for error in result["errors"])
    assert any("axis_values.dtype missing source-enumerated axis values" in error and "double" in error for error in result["errors"])


def test_project_analysis_accepts_fully_covered_source_enumerated_axis_values() -> None:
    surface = _valid_project_analysis_custom_op_surface()
    units = [
        f"alpha:forward:ndim={ndim}:dtype={dtype}"
        for ndim in ("1d", "2d")
        for dtype in ("float", "double")
    ]
    surface["operator_families"] = ["alpha"]
    surface["fine_grained_operator_units"] = units
    surface["discovered_operator_names"] = [unit.replace(":", "_").replace("=", "_") for unit in units]
    surface["native_operator_symbols"] = ["alpha_${ndim}_${dtype}_forward_cuda"]
    surface["kernel_launch_sites"] = ["src/alpha.cu:forward_kernel<<<...>>>"]
    surface["source_evidence"] = [
        "src/backend.py:generated symbols use ${ndim} and ${dtype}",
        "src/backend.py:enumerates ndim 1, 2",
        "src/backend.py:enumerates dtype float and double",
    ]
    surface["variant_axes_detected"] = True
    surface["variant_axes"] = {"ndim": ["1d", "2d"], "dtype": ["float", "double"]}
    surface["expanded_operator_variants"] = [
        {
            "unit_identity": unit,
            "base_unit_identity": "alpha:forward_cuda",
            "axis_values": dict(part.split("=", 1) for part in unit.split(":")[2:]),
            "source_evidence": ["src/backend.py:enumerates source-required ndim/dtype variants"],
            "candidate_public_api_routes": ["pkg.alpha.forward"],
        }
        for unit in units
    ]
    surface["expanded_operator_instances_count"] = len(units)
    surface["fine_grained_operator_unit_evidence"] = [
        {
            "unit_identity": unit,
            "source_evidence": ["src/backend.py:enumerates ndim 1, 2 and dtype float and double"],
            "candidate_public_api_routes": ["pkg.alpha.forward"],
        }
        for unit in units
    ]

    result = validate_project_analysis(
        {
            "project_dir": "/tmp/project",
            "dependencies": ["torch"],
            "cuda_detected": True,
            "entry_script": "validate_custom_ops_full.py",
            "custom_op_surface": surface,
        }
    )

    assert result == {"passed": True, "errors": [], "warnings": []}


def test_project_analysis_ignores_prose_words_after_dtype_enumeration() -> None:
    surface = _valid_project_analysis_custom_op_surface()
    units = [
        f"alpha:forward:ndim=1d:dtype={dtype}"
        for dtype in ("float", "double")
    ]
    surface["operator_families"] = ["alpha"]
    surface["fine_grained_operator_units"] = units
    surface["discovered_operator_names"] = [unit.replace(":", "_").replace("=", "_") for unit in units]
    surface["native_operator_symbols"] = ["alpha_${dtype}_forward_cuda"]
    surface["kernel_launch_sites"] = ["src/alpha.cu:forward_kernel<<<...>>>"]
    surface["source_evidence"] = [
        "src/backend.py:enumerates dtype values float and double for generated backend symbols",
    ]
    surface["variant_axes_detected"] = True
    surface["variant_axes"] = {"dtype": ["float", "double"]}
    surface["expanded_operator_variants"] = [
        {
            "unit_identity": unit,
            "base_unit_identity": "alpha:forward_cuda",
            "axis_values": {"dtype": unit.rsplit("=", 1)[1]},
            "source_evidence": ["src/backend.py:enumerates dtype values float and double for generated backend symbols"],
            "candidate_public_api_routes": ["pkg.alpha.forward"],
        }
        for unit in units
    ]
    surface["expanded_operator_instances_count"] = len(units)
    surface["fine_grained_operator_unit_evidence"] = [
        {
            "unit_identity": unit,
            "source_evidence": ["src/backend.py:enumerates dtype values float and double for generated backend symbols"],
            "candidate_public_api_routes": ["pkg.alpha.forward"],
        }
        for unit in units
    ]

    result = validate_project_analysis(
        {
            "project_dir": "/tmp/project",
            "dependencies": ["torch"],
            "cuda_detected": True,
            "entry_script": "validate_custom_ops_full.py",
            "custom_op_surface": surface,
        }
    )

    assert result == {"passed": True, "errors": [], "warnings": []}


def test_project_analysis_accepts_numeric_ndim_against_source_enumerated_ndim_values() -> None:
    surface = _valid_project_analysis_custom_op_surface()
    units = [f"alpha:forward:ndim={ndim}:dtype=float:device=cuda" for ndim in ("1", "2", "3")]
    surface["operator_families"] = ["alpha"]
    surface["fine_grained_operator_units"] = ["alpha:forward_cuda"]
    surface["discovered_operator_names"] = ["alpha_forward_cuda"]
    surface["native_operator_symbols"] = ["alpha_${ndim}_${dtype}_forward_cuda"]
    surface["kernel_launch_sites"] = ["src/alpha.cu:forward_kernel<<<...>>>"]
    surface["source_evidence"] = [
        "src/backend.py:generated symbols use ${ndim} and ${dtype}",
        "src/backend.py:enumerates ndim 1, 2, 3",
    ]
    surface["variant_axes_detected"] = True
    surface["variant_axes"] = {"ndim": ["1", "2", "3"], "dtype": ["float"], "device": ["cuda"]}
    surface["expanded_operator_variants"] = [
        {
            "unit_identity": unit,
            "base_unit_identity": "alpha:forward_cuda",
            "axis_values": {"ndim": unit.split("ndim=", 1)[1].split(":", 1)[0], "dtype": "float", "device": "cuda"},
            "source_evidence": ["src/backend.py:source-required ndim variant"],
            "candidate_public_api_routes": ["pkg.alpha.forward"],
        }
        for unit in units
    ]
    surface["expanded_operator_instances_count"] = len(units)
    surface["fine_grained_operator_unit_evidence"] = [
        {
            "unit_identity": "alpha:forward_cuda",
            "source_evidence": ["src/alpha.cu:exports alpha forward boundary"],
            "candidate_public_api_routes": ["pkg.alpha.forward"],
        }
    ]

    result = validate_project_analysis(
        {
            "project_dir": "/tmp/project",
            "dependencies": ["torch"],
            "cuda_detected": True,
            "entry_script": "validate_custom_ops_full.py",
            "custom_op_surface": surface,
        }
    )

    assert result == {"passed": True, "errors": [], "warnings": []}


def test_project_analysis_does_not_require_cpu_device_for_cuda_only_variants() -> None:
    surface = _valid_project_analysis_custom_op_surface()
    units = ["alpha:forward:ndim=1:device=cuda", "alpha:forward:ndim=2:device=cuda"]
    surface["operator_families"] = ["alpha"]
    surface["fine_grained_operator_units"] = ["alpha:forward_cuda"]
    surface["discovered_operator_names"] = ["alpha_forward_cuda"]
    surface["native_operator_symbols"] = ["alpha_${ndim}_forward_cuda"]
    surface["kernel_launch_sites"] = ["src/alpha.cu:forward_kernel<<<...>>>"]
    surface["source_evidence"] = [
        "src/backend.py:constructs CPU/CUDA backend function names",
        "src/backend.py:enumerates ndim 1, 2",
        "src/backend.py:enumerates device cpu, cuda",
    ]
    surface["variant_axes_detected"] = True
    surface["variant_axes"] = {"ndim": ["1", "2"], "device": ["cuda"]}
    surface["expanded_operator_variants"] = [
        {
            "unit_identity": unit,
            "base_unit_identity": "alpha:forward_cuda",
            "axis_values": {"ndim": unit.split("ndim=", 1)[1].split(":", 1)[0], "device": "cuda"},
            "source_evidence": ["src/alpha.cu:CUDA native boundary variant"],
            "candidate_public_api_routes": ["pkg.alpha.forward"],
        }
        for unit in units
    ]
    surface["expanded_operator_instances_count"] = len(units)
    surface["fine_grained_operator_unit_evidence"] = [
        {
            "unit_identity": "alpha:forward_cuda",
            "source_evidence": ["src/alpha.cu:exports alpha CUDA boundary"],
            "candidate_public_api_routes": ["pkg.alpha.forward"],
        }
    ]

    result = validate_project_analysis(
        {
            "project_dir": "/tmp/project",
            "dependencies": ["torch"],
            "cuda_detected": True,
            "entry_script": "validate_custom_ops_full.py",
            "custom_op_surface": surface,
        }
    )

    assert result == {"passed": True, "errors": [], "warnings": []}


def test_project_analysis_rejects_loader_tokens_as_target_device_variants() -> None:
    surface = _axis_coverage_regression_surface()
    variants = cast(list[dict[str, object]], surface["expanded_operator_variants"])
    for index, variant in enumerate(variants):
        axis_values = cast(dict[str, object], variant["axis_values"])
        if index % 3 == 1:
            axis_values["device"] = "ctypes"
            variant["unit_identity"] = cast(str, variant["unit_identity"]).rsplit("=cuda", 1)[0] + "=ctypes"
        elif index % 3 == 2:
            axis_values["device"] = "symbols"
            variant["unit_identity"] = cast(str, variant["unit_identity"]).rsplit("=cuda", 1)[0] + "=symbols"
    surface["variant_axes"] = {"ndim": ["1d", "2d", "3d"], "accuracy": [2, 4, 6, 8], "dtype": ["float", "double"], "device": ["cuda", "ctypes", "symbols"]}
    surface["fine_grained_operator_units"] = [cast(str, variant["unit_identity"]) for variant in variants]
    surface["discovered_operator_names"] = [cast(str, variant["unit_identity"]).replace(":", "_").replace("=", "_") for variant in variants]
    surface["native_operator_symbols"] = [
        "alpha_iso_${ndim}_${accuracy}_${dtype}_forward_cuda",
        "alpha_iso_${ndim}_${accuracy}_${dtype}_backward_cuda",
    ]
    surface["source_evidence"] = [
        "src/backend.py:constructs generated ctypes symbol names",
        "src/backend.py:enumerates device cpu, cuda backend suffixes",
        "src/backend.py:enumerates ndim 1, 2, 3 accuracy 2, 4, 6, 8 dtype float and double",
    ]
    surface["fine_grained_operator_unit_evidence"] = [
        {
            "unit_identity": cast(str, variant["unit_identity"]),
            "source_evidence": cast(list[str], variant["source_evidence"]),
            "candidate_public_api_routes": cast(list[str], variant["candidate_public_api_routes"]),
        }
        for variant in variants
    ]

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
    assert any("variant_axes.device" in error and "ctypes" in error and "symbols" in error for error in result["errors"])
    assert any("axis_values" in error and "device=ctypes" in error for error in result["errors"])
    assert any("axis_values" in error and "device=symbols" in error for error in result["errors"])


def test_project_analysis_rejects_cpu_as_target_variant_axis_value() -> None:
    surface = _axis_coverage_regression_surface()
    surface["variant_axes"] = {"ndim": ["1d", "2d", "3d"], "accuracy": [2, 4, 6, 8], "dtype": ["float", "double"], "device": ["cpu", "cuda"]}

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
    assert any("variant_axes.device" in error and "cpu" in error for error in result["errors"])


def test_project_analysis_rejects_cpu_as_expanded_variant_axis_value() -> None:
    surface = _axis_coverage_regression_surface()
    variants = cast(list[dict[str, object]], surface["expanded_operator_variants"])
    first_variant = variants[0]
    cast(dict[str, object], first_variant["axis_values"])["device"] = "cpu"
    first_variant["unit_identity"] = cast(str, first_variant["unit_identity"]).rsplit("=cuda", 1)[0] + "=cpu"
    surface["fine_grained_operator_units"] = [cast(str, variant["unit_identity"]) for variant in variants]
    surface["discovered_operator_names"] = [cast(str, variant["unit_identity"]).replace(":", "_").replace("=", "_") for variant in variants]
    surface["fine_grained_operator_unit_evidence"] = [
        {
            "unit_identity": cast(str, variant["unit_identity"]),
            "source_evidence": cast(list[str], variant["source_evidence"]),
            "candidate_public_api_routes": cast(list[str], variant["candidate_public_api_routes"]),
        }
        for variant in variants
    ]

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
    assert any("axis_values" in error and "device=cpu" in error for error in result["errors"])


@pytest.mark.parametrize("axis_name", ["backend", "reference", "baseline", "comparison"])
def test_project_analysis_rejects_reference_and_baseline_values_as_target_axis_values(axis_name: str) -> None:
    surface = _axis_coverage_regression_surface()
    variants = cast(list[dict[str, object]], surface["expanded_operator_variants"])
    for variant in variants:
        cast(dict[str, object], variant["axis_values"])[axis_name] = "reference"
        variant["unit_identity"] = cast(str, variant["unit_identity"]) + f":{axis_name}=reference"
    surface["variant_axes"] = {
        "ndim": ["1d", "2d", "3d"],
        "accuracy": [2, 4, 6, 8],
        "dtype": ["float", "double"],
        "device": ["cuda"],
        axis_name: ["reference"],
    }
    surface["fine_grained_operator_units"] = [cast(str, variant["unit_identity"]) for variant in variants]
    surface["discovered_operator_names"] = [cast(str, variant["unit_identity"]).replace(":", "_").replace("=", "_") for variant in variants]
    surface["fine_grained_operator_unit_evidence"] = [
        {
            "unit_identity": cast(str, variant["unit_identity"]),
            "source_evidence": cast(list[str], variant["source_evidence"]),
            "candidate_public_api_routes": cast(list[str], variant["candidate_public_api_routes"]),
        }
        for variant in variants
    ]

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
    assert any(f"variant_axes.{axis_name}" in error and "reference" in error for error in result["errors"])


def test_project_analysis_rejects_missing_target_device_when_native_symbol_is_device_parametric() -> None:
    surface = _valid_project_analysis_custom_op_surface()
    unit = "alpha:forward:device=cuda"
    surface["operator_families"] = ["alpha"]
    surface["fine_grained_operator_units"] = ["alpha:forward"]
    surface["discovered_operator_names"] = ["alpha_forward_cuda"]
    surface["native_operator_symbols"] = ["alpha_${device}_forward"]
    surface["kernel_launch_sites"] = ["src/alpha.cu:forward_kernel<<<...>>>"]
    surface["source_evidence"] = [
        "src/backend.py:builds generated alpha_${device}_forward symbols",
        "src/backend.py:enumerates device cuda, gpu",
    ]
    surface["variant_axes_detected"] = True
    surface["variant_axes"] = {"device": ["cuda"]}
    surface["expanded_operator_variants"] = [
        {
            "unit_identity": unit,
            "base_unit_identity": "alpha:forward",
            "axis_values": {"device": "cuda"},
            "source_evidence": ["src/backend.py:source-required device variant"],
            "candidate_public_api_routes": ["pkg.alpha.forward"],
        }
    ]
    surface["expanded_operator_instances_count"] = 1
    surface["fine_grained_operator_unit_evidence"] = [
        {
            "unit_identity": "alpha:forward",
            "source_evidence": ["src/backend.py:source-required device variant"],
            "candidate_public_api_routes": ["pkg.alpha.forward"],
        }
    ]

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
    assert any("variant_axes.device missing source-enumerated axis values" in error and "gpu" in error for error in result["errors"])
    assert any("axis_values.device missing source-enumerated axis values" in error and "gpu" in error for error in result["errors"])


def test_project_analysis_accepts_non_variant_custom_op_with_cpu_reference_evidence() -> None:
    surface = _valid_project_analysis_custom_op_surface()
    surface["source_evidence"] = [
        "src/alpha.cpp:exports alpha CUDA boundary and has CPU reference baseline loader via ctypes symbols",
    ]
    evidence = cast(list[dict[str, object]], surface["fine_grained_operator_unit_evidence"])
    evidence[0]["source_evidence"] = ["src/alpha.cpp:CPU reference baseline compared with CUDA custom op"]

    result = validate_project_analysis(
        {
            "project_dir": "/tmp/project",
            "dependencies": ["torch"],
            "cuda_detected": True,
            "entry_script": "validate_custom_ops_full.py",
            "custom_op_surface": surface,
        }
    )

    assert result == {"passed": True, "errors": [], "warnings": []}


def test_project_analysis_rejects_underexpanded_numeric_ndim_source_values() -> None:
    surface = _valid_project_analysis_custom_op_surface()
    unit = "alpha:forward:ndim=1:dtype=float"
    surface["operator_families"] = ["alpha"]
    surface["fine_grained_operator_units"] = [unit]
    surface["discovered_operator_names"] = ["alpha_forward_1_float"]
    surface["native_operator_symbols"] = ["alpha_${ndim}_${dtype}_forward_cuda"]
    surface["kernel_launch_sites"] = ["src/alpha.cu:forward_kernel<<<...>>>"]
    surface["source_evidence"] = [
        "src/backend.py:enumerates ndim 1, 2, 3",
        "src/backend.py:enumerates dtype float and double",
    ]
    surface["variant_axes_detected"] = True
    surface["variant_axes"] = {"ndim": ["1"], "dtype": ["float"]}
    surface["expanded_operator_variants"] = [
        {
            "unit_identity": unit,
            "base_unit_identity": "alpha:forward_cuda",
            "axis_values": {"ndim": "1", "dtype": "float"},
            "source_evidence": ["src/backend.py:sample variant only"],
            "candidate_public_api_routes": ["pkg.alpha.forward"],
        }
    ]
    surface["expanded_operator_instances_count"] = 1
    surface["fine_grained_operator_unit_evidence"] = [
        {
            "unit_identity": unit,
            "source_evidence": ["src/backend.py:sample variant only"],
            "candidate_public_api_routes": ["pkg.alpha.forward"],
        }
    ]

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
    assert any("variant_axes.ndim missing source-enumerated axis values" in error and "2d" in error and "3d" in error for error in result["errors"])
    assert any("axis_values.dtype missing source-enumerated axis values" in error and "double" in error for error in result["errors"])


def test_project_analysis_accepts_expanded_variant_source_evidence_without_duplicate_unit_evidence() -> None:
    surface = _deepwave_variant_surface()
    base_units = sorted({cast(str, variant["base_unit_identity"]) for variant in cast(list[dict[str, object]], surface["expanded_operator_variants"])})
    surface["fine_grained_operator_units"] = base_units
    surface["fine_grained_operator_unit_evidence"] = [
        {
            "unit_identity": unit,
            "source_evidence": [f"deepwave_original_src/{unit.split(':', 1)[0]}.cu:base export {unit}"],
            "candidate_public_api_routes": [f"deepwave.{unit.split(':', 1)[0]}"],
        }
        for unit in base_units
    ]

    result = validate_project_analysis(
        {
            "project_dir": "/tmp/project",
            "dependencies": ["torch"],
            "cuda_detected": True,
            "entry_script": "validate_custom_ops_full.py",
            "custom_op_surface": surface,
        }
    )

    assert result == {"passed": True, "errors": [], "warnings": []}


def test_project_analysis_accepts_expanded_variants_as_authoritative_inventory() -> None:
    surface = _valid_project_analysis_custom_op_surface()
    units = ["alpha:forward:ndim=1d", "alpha:forward:ndim=2d"]
    surface["operator_families"] = ["alpha"]
    surface["fine_grained_operator_units"] = ["alpha:forward_cuda"]
    surface["discovered_operator_names"] = ["alpha_forward_cuda"]
    surface["native_operator_symbols"] = ["alpha_${ndim}_forward_cuda"]
    surface["kernel_launch_sites"] = ["csrc/alpha.cu:forward_kernel<<<...>>>"]
    surface["source_evidence"] = ["csrc/backend.py:enumerates ndim 1, 2"]
    surface["variant_axes_detected"] = True
    surface["variant_axes"] = {"ndim": ["1d", "2d"]}
    surface["expanded_operator_variants"] = [
        {
            "unit_identity": unit,
            "base_unit_identity": "alpha:forward_cuda",
            "axis_values": {"ndim": unit.rsplit("=", 1)[1]},
            "source_evidence": [f"csrc/backend.py:{unit}"],
            "candidate_public_api_routes": ["pkg.alpha.forward"],
        }
        for unit in units
    ]
    surface["expanded_operator_instances_count"] = len(units)
    surface["fine_grained_operator_unit_evidence"] = [
        {
            "unit_identity": "alpha:forward_cuda",
            "source_evidence": ["csrc/alpha.cu:exports alpha forward boundary"],
            "candidate_public_api_routes": ["pkg.alpha.forward"],
        }
    ]

    result = validate_project_analysis(
        {
            "project_dir": "/tmp/project",
            "dependencies": ["torch"],
            "cuda_detected": True,
            "entry_script": "validate_custom_ops_full.py",
            "custom_op_surface": surface,
        }
    )

    assert result == {"passed": True, "errors": [], "warnings": []}


def test_project_analysis_rejects_expanded_variant_without_source_evidence_anywhere() -> None:
    surface = _deepwave_variant_surface()
    variants = cast(list[dict[str, object]], surface["expanded_operator_variants"])
    missing_identity = cast(str, variants[0]["unit_identity"])
    variants[0]["source_evidence"] = []
    base_units = sorted({cast(str, variant["base_unit_identity"]) for variant in variants})
    surface["fine_grained_operator_units"] = [*base_units, *cast(list[str], surface["fine_grained_operator_units"])]
    surface["fine_grained_operator_unit_evidence"] = [
        {
            "unit_identity": unit,
            "source_evidence": [f"deepwave_original_src/{unit.split(':', 1)[0]}.cu:base export {unit}"],
            "candidate_public_api_routes": [f"deepwave.{unit.split(':', 1)[0]}"],
        }
        for unit in base_units
    ]

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
    assert any("expanded_operator_variants[0].source_evidence must contain source proof" in error for error in result["errors"])
    assert any("missing unit evidence" in error and missing_identity in error for error in result["errors"])


def test_project_analysis_rejects_deepwave_candidate2_artifact_underexpanded_variants() -> None:
    artifact_path = (
        PROJECT_ROOT.parent
        / "output_projects/04_Deepwave_20260520_214541/.sm-artifacts/e2e-v2-f76f69a39090/validated/phase_1_project_analysis_canonical.json"
    )
    payload = cast(dict[str, object], json.loads(artifact_path.read_text(encoding="utf-8")))

    result = validate_project_analysis(payload)

    assert result["passed"] is False
    assert any("missing source-enumerated axis values" in error for error in result["errors"])


def test_project_analysis_rejects_deepwave_like_collapsed_variant_identities() -> None:
    surface = _deepwave_variant_surface()
    collapsed_units = [
        "deepwave_scalar:forward_cuda:{ndim=1d|2d|3d,accuracy=2|4|6|8,dtype=float|double}",
        "deepwave_scalar:backward_cuda:{ndim=1d|2d|3d,accuracy=2|4|6|8,dtype=float|double}",
    ]
    surface["fine_grained_operator_units"] = collapsed_units
    surface["discovered_operator_names"] = [unit.replace(":", "_") for unit in collapsed_units]
    surface["native_operator_symbols"] = ["forward_cuda", "backward_cuda"]
    surface["variant_axes"] = {"ndim": ["1d", "2d", "3d"], "accuracy": [2, 4, 6, 8], "dtype": ["float", "double"]}
    surface["expanded_operator_instances_count"] = len(collapsed_units)
    surface["expanded_operator_variants"] = [
        {
            "unit_identity": unit,
            "base_unit_identity": unit.split(":{", 1)[0],
            "axis_values": {"ndim": "1d|2d|3d", "accuracy": "2|4|6|8", "dtype": "float|double"},
            "source_evidence": ["deepwave_original_src/scalar.cu:collapsed variant macro evidence"],
            "candidate_public_api_routes": ["deepwave.scalar"],
        }
        for unit in collapsed_units
    ]
    surface["fine_grained_operator_unit_evidence"] = [
        {
            "unit_identity": unit,
            "source_evidence": ["deepwave_original_src/scalar.cu:collapsed brace/pipe identity"],
            "candidate_public_api_routes": ["deepwave.scalar"],
        }
        for unit in collapsed_units
    ]

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
    assert any("brace/pipe collapsed" in error for error in result["errors"])
    assert any("axis_values" in error and "atomic" in error for error in result["errors"])


def test_project_analysis_rejects_combined_variant_axis_values() -> None:
    surface = _deepwave_variant_surface()
    surface["variant_axes"] = {"ndim": ["ndim=1d|2d|3d"], "dtype": ["float|double"], "accuracy": ["2,4"]}

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
    assert any("variant_axes" in error and "atomic scalar" in error for error in result["errors"])


def test_project_analysis_rejects_unexpanded_variant_inventory_count() -> None:
    surface = _deepwave_variant_surface()
    surface["fine_grained_operator_units"] = ["scalar_forward:ndim=1:accuracy=2:dtype=float", "elastic_forward:ndim=1:accuracy=2:dtype=float"]
    surface["expanded_operator_instances_count"] = 2
    surface["expanded_operator_variants"] = [
        {
            "unit_identity": "scalar_forward:ndim=1:accuracy=2:dtype=float",
            "base_unit_identity": "scalar_forward",
            "axis_values": {"ndim": 1, "accuracy": 2, "dtype": "float"},
            "source_evidence": ["deepwave_original_src/scalar.cu:scalar_forward ndim=1 accuracy=2 dtype=float"],
            "candidate_public_api_routes": ["deepwave.scalar_forward"],
        },
        {
            "unit_identity": "elastic_forward:ndim=1:accuracy=2:dtype=float",
            "base_unit_identity": "elastic_forward",
            "axis_values": {"ndim": 1, "accuracy": 2, "dtype": "float"},
            "source_evidence": ["deepwave_original_src/elastic.cu:elastic_forward ndim=1 accuracy=2 dtype=float"],
            "candidate_public_api_routes": ["deepwave.elastic_forward"],
        },
    ]
    surface["fine_grained_operator_unit_evidence"] = [
        {
            "unit_identity": cast(str, variant["unit_identity"]),
            "source_evidence": cast(list[str], variant["source_evidence"]),
            "candidate_public_api_routes": cast(list[str], variant["candidate_public_api_routes"]),
        }
        for variant in cast(list[dict[str, object]], surface["expanded_operator_variants"])
    ]

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
    assert any("variant count must expand beyond distinct base unit count" in error for error in result["errors"])


def test_project_analysis_accepts_heterogeneous_variant_bases_without_inferred_cartesian_closure() -> None:
    surface = _heterogeneous_deepwave_variant_surface()
    variants = cast(list[dict[str, object]], surface["expanded_operator_variants"])

    assert any(
        variant.get("base_unit_identity") == "storage_compress"
        and "accuracy" not in cast(dict[str, object], variant["axis_values"])
        and "device" not in cast(dict[str, object], variant["axis_values"])
        for variant in variants
    )

    result = validate_project_analysis(
        {
            "project_dir": "/tmp/project",
            "dependencies": ["torch"],
            "cuda_detected": True,
            "entry_script": "validate_custom_ops_full.py",
            "custom_op_surface": surface,
        }
    )

    assert result == {"passed": True, "errors": [], "warnings": []}


def test_project_analysis_rejects_missing_source_enumerated_variant_axis_values_without_cartesian_assumption() -> None:
    surface = _deepwave_variant_surface()
    variants = cast(list[dict[str, object]], surface["expanded_operator_variants"])
    kept_variants = [
        variant
        for variant in variants
        if cast(dict[str, object], variant["axis_values"]).get("accuracy") != "8"
    ]
    units = [cast(str, variant["unit_identity"]) for variant in kept_variants]
    surface["source_evidence"] = [
        "src/backend_utils.py:enumerates ndim 1, 2, 3",
        "src/backend_utils.py:enumerates accuracy 2, 4, 6, 8",
        "src/backend_utils.py:enumerates dtype float and double",
    ]
    surface["variant_axes"] = {"ndim": [1, 2, 3], "accuracy": [2, 4, 6], "dtype": ["float", "double"]}
    surface["expanded_operator_variants"] = kept_variants
    surface["expanded_operator_instances_count"] = len(kept_variants)
    surface["fine_grained_operator_units"] = units
    surface["fine_grained_operator_unit_evidence"] = [
        {
            "unit_identity": cast(str, variant["unit_identity"]),
            "source_evidence": cast(list[str], variant["source_evidence"]),
            "candidate_public_api_routes": cast(list[str], variant["candidate_public_api_routes"]),
        }
        for variant in kept_variants
    ]

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
    assert any("variant_axes.accuracy missing source-enumerated axis values" in error and "8" in error for error in result["errors"])
    assert any("axis_values.accuracy missing source-enumerated axis values" in error and "8" in error for error in result["errors"])
    assert not any("every concrete per-base axis combination" in error for error in result["errors"])


def test_project_analysis_rejects_external_benchmark_units_without_project_local_source() -> None:
    surface = _deepwave_variant_surface()
    external_units = [
        "external_ascendc_benchmark_ops:scalar_fwd_2d",
        "external_ascendc_benchmark_ops:add_sources_2d",
    ]
    units = cast(list[str], surface["fine_grained_operator_units"])
    surface["fine_grained_operator_units"] = [*units, *external_units]
    names = cast(list[str], surface["discovered_operator_names"])
    surface["discovered_operator_names"] = [*names, *[unit.replace(":", "_") for unit in external_units]]
    symbols = cast(list[str], surface["native_operator_symbols"])
    surface["native_operator_symbols"] = [*symbols, "scalar_fwd_2d", "add_sources_2d"]
    evidence = cast(list[dict[str, object]], surface["fine_grained_operator_unit_evidence"])
    surface["fine_grained_operator_unit_evidence"] = [
        *evidence,
        *[
            {
                "unit_identity": unit,
                "source_evidence": ["external/ascendc_benchmark_ops/op_kernel/benchmark.cpp"],
                "candidate_framework_integration_routes": ["external benchmark harness"],
            }
            for unit in external_units
        ],
    ]

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
    assert any("external/out-of-scope benchmark units" in error for error in result["errors"])


def test_project_analysis_rejects_pointnet2_like_implementation_detail_unit_rows_without_variants() -> None:
    surface = _pointnet2_boundary_surface()
    bad_units = [
        "gather_points_kernel_wrapper",
        "furthest_point_sampling_kernel:block_size=512",
        "__update",
        "opt_n_threads",
        "CUDA_CHECK_ERRORS",
        "CHECK_CUDA",
    ]
    units = cast(list[str], surface["fine_grained_operator_units"])
    surface["fine_grained_operator_units"] = [*units, *bad_units]
    names = cast(list[str], surface["discovered_operator_names"])
    surface["discovered_operator_names"] = [*names, *bad_units]
    symbols = cast(list[str], surface["native_operator_symbols"])
    surface["native_operator_symbols"] = [*symbols, *bad_units]
    evidence = cast(list[dict[str, object]], surface["fine_grained_operator_unit_evidence"])
    surface["fine_grained_operator_unit_evidence"] = [
        *evidence,
        *[
            {
                "unit_identity": unit,
                "source_evidence": [f"src/internal.cu:{unit}"],
                "candidate_framework_integration_routes": ["internal launch path"],
            }
            for unit in bad_units
        ],
    ]

    result = validate_project_analysis(
        {
            "project_dir": "/tmp/project",
            "dependencies": ["torch"],
            "cuda_detected": True,
            "entry_script": "test.py",
            "custom_op_surface": surface,
        }
    )

    assert result["passed"] is False
    assert any("raw kernels" in error and "fine_grained_operator_units" in error for error in result["errors"])
    assert any("fine_grained_operator_unit_evidence" in error for error in result["errors"])


def test_project_analysis_rejects_pointnet2_like_implementation_detail_variants() -> None:
    surface = _valid_project_analysis_custom_op_surface()
    public_units = [
        "gather_points",
        "gather_points_grad",
        "furthest_point_sampling",
        "three_nn",
        "three_interpolate",
        "three_interpolate_grad",
        "ball_query",
        "group_points",
        "group_points_grad",
    ]
    surface["operator_families"] = ["pointnet2_ops"]
    surface["fine_grained_operator_units"] = public_units
    surface["discovered_operator_names"] = public_units
    surface["native_operator_symbols"] = public_units
    surface["kernel_launch_sites"] = [f"src/{unit}.cu:{unit}_kernel<<<...>>>" for unit in public_units]
    surface["source_evidence"] = [f"src/{unit}.cu:{unit}" for unit in public_units]
    surface["variant_axes_detected"] = True
    surface["variant_axes"] = {"block_size": [256, 512]}
    surface["expanded_operator_variants"] = [
        {
            "unit_identity": "furthest_point_sampling:block_size=256",
            "base_unit_identity": "furthest_point_sampling",
            "axis_values": {"block_size": 256},
            "source_evidence": ["src/sampling_gpu.cu:block size template specialization for launch tuning"],
            "candidate_public_api_routes": ["pointnet2_ops.furthest_point_sampling"],
        },
        {
            "unit_identity": "furthest_point_sampling:block_size=512",
            "base_unit_identity": "furthest_point_sampling",
            "axis_values": {"block_size": 512},
            "source_evidence": ["src/sampling_gpu.cu:grid/thread heuristic chooses block size at runtime"],
            "candidate_public_api_routes": ["pointnet2_ops.furthest_point_sampling"],
        },
    ]
    surface["expanded_operator_instances_count"] = 2
    surface["fine_grained_operator_unit_evidence"] = [
        {"unit_identity": unit, "source_evidence": [f"src/{unit}.cu:{unit}"], "candidate_public_api_routes": [f"pointnet2_ops.{unit}"]}
        for unit in public_units
    ]

    result = validate_project_analysis(
        {
            "project_dir": "/tmp/project",
            "dependencies": ["torch"],
            "cuda_detected": True,
            "entry_script": "test.py",
            "custom_op_surface": surface,
        }
    )

    assert result["passed"] is False
    assert any("implementation details" in error or "block-size" in error for error in result["errors"])


def test_project_analysis_rejects_expanded_variant_count_mismatch() -> None:
    surface = _deepwave_variant_surface()
    surface["expanded_operator_instances_count"] = 1

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
    assert any("expanded_operator_instances_count" in error for error in result["errors"])


def test_entry_script_validator_requires_variant_checks_only_when_variant_contract_present(tmp_path: Path) -> None:
    script_path = tmp_path / "validate_custom_ops_full.py"
    _ = script_path.write_text("print('custom-op validation')\n", encoding="utf-8")
    payload = _valid_custom_op_contract(str(script_path))
    payload["expanded_variant_inventory"] = {"variant_axes_detected": True, "unit_identities": ["op:ndim=1"], "expanded_operator_instances_count": 1}
    payload["variant_axis_coverage"] = {"all_axes_covered": True, "axes": {"ndim": [1]}}
    payload["per_variant_performance_report"] = {"required": True, "one_entry_per_expanded_variant": True}

    result = validate_entry_script(payload)

    assert result["passed"] is False
    assert any("expanded_variant_inventory" in error for error in result["errors"])


def test_entry_static_requires_variant_booleans_only_when_active() -> None:
    base: dict[str, object] = {
        "validation_passed": True,
        "issues": [],
        "fix_plan": "No issues found.",
        "entry_script_kind": "custom_op_full_validation",
    }
    for field in (
        "custom_op_requirements_checked",
        "script_source_driven_inventory",
        "script_emits_fine_grained_units",
        "script_maps_public_api_to_units",
        "script_discovers_full_inventory",
        "script_records_native_operator_symbols",
        "script_requires_strict_opp_producer_evidence",
        "script_rejects_non_opp_producer_success",
        "script_runs_project_api_custom_ops",
        "script_requires_per_row_route_evidence",
        "script_correlates_route_evidence_to_manifest_rows",
        "script_rejects_direct_or_builtin_only_routes",
        "script_rejects_report_only_success",
        "script_requires_project_local_artifacts",
        "script_requires_project_root_artifact_existence",
        "script_requires_numeric_performance",
        "script_checks_no_fallback",
    ):
        base[field] = True

    non_variant = validate_entry_static(base)
    variant_missing = validate_entry_static({**base, "expanded_variant_static_required": True})
    variant_present = validate_entry_static({
        **base,
        "expanded_variant_static_required": True,
        "script_discovers_expanded_variant_inventory": True,
        "script_checks_variant_axis_coverage": True,
        "script_requires_per_variant_performance": True,
    })

    assert non_variant == {"passed": True, "errors": [], "warnings": []}
    assert variant_missing["passed"] is False
    assert any("expanded-variant static validation missing" in error for error in variant_missing["errors"])
    assert variant_present == {"passed": True, "errors": [], "warnings": []}


def test_custom_op_final_gate_enforces_expanded_variant_exact_closure(tmp_path: Path) -> None:
    units = _deepwave_variant_unit_identities()[:4]
    payload = _variant_final_gate_payload(units)
    _write_custom_op_manifest(tmp_path, units)
    _write_strict_opp_fixture(tmp_path)

    result = validate_custom_op_final_gate(payload, project_root=tmp_path)

    assert result == {"passed": True, "errors": [], "warnings": []}


def test_custom_op_final_gate_rejects_collapsed_expanded_variant_rows(tmp_path: Path) -> None:
    units = _deepwave_variant_unit_identities()[:2]
    payload = _variant_final_gate_payload(units)
    collapsed = "scalar_forward:{ndim=1|2}:accuracy=2:dtype=float"
    rows = cast(list[dict[str, object]], payload["rows"])
    rows[0]["row_id"] = collapsed
    rows[0]["unit_identity"] = collapsed
    _write_custom_op_manifest(tmp_path, units)
    _write_strict_opp_fixture(tmp_path)

    result = validate_custom_op_final_gate(payload, project_root=tmp_path)

    assert result["passed"] is False
    assert any("collapsed expanded-variant identities" in error for error in result["errors"])


def test_custom_op_final_gate_rejects_missing_variant_performance_entry(tmp_path: Path) -> None:
    units = _deepwave_variant_unit_identities()[:3]
    payload = _variant_final_gate_payload(units)
    performance = cast(dict[str, object], payload["performance_report"])
    performance["entries"] = cast(list[dict[str, object]], performance["entries"])[:-1]
    _write_custom_op_manifest(tmp_path, units)
    _write_strict_opp_fixture(tmp_path)

    result = validate_custom_op_final_gate(payload, project_root=tmp_path)

    assert result["passed"] is False
    assert any("performance_report must exactly match expanded variant unit identities" in error for error in result["errors"])


def test_custom_op_final_gate_rejects_missing_variant_runtime_coverage_entry(tmp_path: Path) -> None:
    units = _deepwave_variant_unit_identities()[:3]
    payload = _variant_final_gate_payload(units)
    runtime_report = cast(dict[str, object], payload["runtime_coverage_report"])
    runtime_report["entries"] = cast(list[dict[str, object]], runtime_report["entries"])[:-1]
    _write_custom_op_manifest(tmp_path, units)
    _write_strict_opp_fixture(tmp_path)

    result = validate_custom_op_final_gate(payload, project_root=tmp_path)

    assert result["passed"] is False
    assert any("runtime_coverage_report must exactly match expanded variant unit identities" in error for error in result["errors"])


def test_custom_op_final_gate_rejects_missing_variant_runtime_coverage_report(tmp_path: Path) -> None:
    units = _deepwave_variant_unit_identities()[:2]
    payload = _variant_final_gate_payload(units)
    _ = payload.pop("runtime_coverage_report")
    _write_custom_op_manifest(tmp_path, units)
    _write_strict_opp_fixture(tmp_path)

    result = validate_custom_op_final_gate(payload, project_root=tmp_path)

    assert result["passed"] is False
    assert any("runtime_coverage_report must be an object" in error for error in result["errors"])


def test_custom_op_final_gate_rejects_generated_opp_inventory_undercount(tmp_path: Path) -> None:
    payload = _valid_custom_op_final_gate()
    _write_custom_op_manifest(tmp_path, ["ScalarFwd2D"])
    _write_strict_opp_fixture(tmp_path)
    _write_generated_opp_unit(tmp_path, "scalar_fwd2_d", "ScalarFwd2D")
    _write_generated_opp_unit(tmp_path, "acoustic_fwd3_d", "AcousticFwd3D")

    result = validate_custom_op_final_gate(payload, project_root=tmp_path)

    assert result["passed"] is False
    assert any("generated OPP inventory contains project-local generated operators not covered" in error and "acoustic_fwd3_d" in error for error in result["errors"])
    assert any("counts must cover all generated OPP operator entries" in error for error in result["errors"])


def test_custom_op_final_gate_accepts_generated_opp_inventory_matched_by_evidence_tokens(tmp_path: Path) -> None:
    units = ["ScalarFwd2D", "acoustic_iso:forward_cuda:ndim=3d:accuracy=8:dtype=double:device=cuda"]
    payload = _variant_final_gate_payload(units)
    _ = payload.pop("expanded_variant_inventory", None)
    rows = cast(list[dict[str, object]], payload["rows"])
    second_artifact = dict(cast(dict[str, object], rows[1]["opp_custom_op_artifact_evidence"]))
    second_artifact["path"] = "opp/ScalarFwd2D/libscalar_fwd_2d.so"
    second_artifact["project_relative_path"] = "opp/ScalarFwd2D/libscalar_fwd_2d.so"
    second_artifact["artifact_path"] = "opp/ScalarFwd2D/libscalar_fwd_2d.so"
    second_artifact["runtime_loaded_artifact_path"] = "opp/ScalarFwd2D/libscalar_fwd_2d.so"
    second_artifact["generated_header_path"] = "ascend_opp/custom_all/build_out/op_api/include/aclnn_acoustic_fwd3_d.h"
    second_artifact["kernel_meta_path"] = "ascend_opp/custom_all/build_out/_CPack_Packages/Linux/External/custom.run/packages/vendors/custom/op_impl/ai_core/tbe/kernel/ascend910b/acoustic_fwd3_d/AcousticFwd3D_1234567890abcdef.o"
    rows[1]["opp_custom_op_artifact_evidence"] = second_artifact
    performance = cast(dict[str, object], payload["performance_report"])
    performance["unit_count"] = 2
    _write_custom_op_manifest(tmp_path, units)
    _write_strict_opp_fixture(tmp_path)
    _write_generated_opp_unit(tmp_path, "scalar_fwd2_d", "ScalarFwd2D")
    _write_generated_opp_unit(tmp_path, "acoustic_fwd3_d", "AcousticFwd3D")

    result = validate_custom_op_final_gate(payload, project_root=tmp_path)

    assert result == {"passed": True, "errors": [], "warnings": []}
