# Phase 3 - Entry Script Confirmation

You are executing `{phase_name}` for `{project_dir}`.

## Context
This is a CUDA -> Ascend NPU migration workflow. The selected command will become the Phase 5 validation surface after Phase 4 migration and repair. Original CUDA scripts may fail on NPU at this stage; do not avoid CUDA-dependent paths because they fail before migration.

## Goal
- Identify the TRUE entry script/command that validates the project's full real-world migration target.
- Prefer documented project commands and existing launchers over generated scripts.
- Create a script only when no usable command exists, and then exercise all core project features.
- Do not choose a smoke, MVP, import-only, direct-only, or partial command when the project has a broader migration target.

## Migration Constraints (from Phase 1.5)
{constraint_summary}

These constraints are binding. Consider them when selecting the entry script.

## Custom-Op Mandatory Rules
If the project includes CUDA/C++ custom operators, select or create a non-interactive full validation script that discovers the inventory directly from source files, bindings, wrappers, autograd, aliases, launchers, setup scripts, and tests. The script must enumerate every source-discovered inventory unit before validation, execute coverage and performance checks for every unit, measure one overall/end-to-end speedup after all discovered custom-op units have been replaced and routed through the project/public API, and emit one final inventory row per fine-grained source-discovered operator unit, not per coarse family. Each unit must record unit identity, family/variant/signature, native symbols, kernel launch sites, public entry mapping, source evidence, and out-of-scope source groups. Its `opp_custom_op_artifact_evidence` must prove strict Ascend C/CANN OPP custom operator artifacts as the only acceptable producer target: op_host source, op_kernel/AscendC source, CMakeLists.txt/build.sh or equivalent OPP build script, CANN/OPP build-install log, install/provenance evidence, generated header/op_info/kernel_meta/producer/package artifacts, and the runtime-loaded compiled OPP artifact. Unknown future implementations that cannot satisfy this positive OPP producer contract must fail closed, even if they compile, run, or expose an Ascend-looking `.so`. `torch_npu.utils.cpp_extension.NpuExtension`, `torch.utils.cpp_extension.CppExtension`, ATen-only `npu_ops.cpp`, and libtorch/torch_cpu/torch_npu-only builds are not OPP producer evidence; NpuExtension can only be adapter evidence when separate strict OPP producer evidence exists. Its `migration_reports/performance.json` and final `performance_report` evidence must include per-unit entries for every manifest/source-inventory unit plus `overall_baseline_seconds`, `overall_custom_seconds`, `overall_speedup_vs_baseline`, `overall_all_units_replaced=true` or equivalent all-units-replaced proof, and project/public API route proof for the overall timing. The final Chinese summary must include the exact per-row report parity table columns required by the custom-op contract.

## Decision Priority
1. Use the project's documented non-interactive full validation command, adjusted only for absolute paths or venv Python.
2. Use an existing launcher (`train.py`, `run.py`, `generate.py`, `main.py`, `demo.py`, project test/benchmark/e2e runner) with arguments that cover the full target.
3. For custom-op projects, create or select a full validation script such as `{project_dir}/validate_custom_ops_full.py` when no documented full runner exists; it must discover the source-driven fine-grained inventory, validate every discovered unit independently, run per-unit acceleration/speedup measurements, run one overall all-units-replaced project/public API acceleration/speedup measurement, and write the required migration reports instead of merely inspecting reports.
4. Create `{project_dir}/smoke_test.py` only as a last resort for non-custom-op projects with no existing command. It must import real modules, run realistic data flow, include CUDA-dependent modules, and have an `if __name__ == "__main__":` guard.

## Headless Execution Compliance
The entry command is executed automatically in Phase 5:
- No `input()`, `getpass()`, REPL/debugger stops, blocking GUI calls, or unbounded loops in the execution path.
- If the existing launcher is interactive, prefer documented non-interactive flags/env vars. Otherwise create a wrapper that calls the real entry point with safe defaults.
- Do not invent unsupported CLI flags.
- If you create or select a generated/wrapper script, physically write it under `{project_dir}` before returning JSON. Never return an `entry_script_path` for a file that does not exist; custom-op Phase 3 validation fails before Phase 3.5 when the selected script is missing.

You may reason freely about the choice, but return exactly one JSON object.

## Output Format
Return exactly one JSON object. Legacy projects may return only the two existing fields:

```json
{
  "entry_script_path": "/path/to/project/generate.py",
  "run_command": "/path/to/project/.venv/bin/python /path/to/project/generate.py --config /path/to/project/config.yml"
}
```

For CUDA/C++ custom-op projects, keep those fields and add this backward-compatible contract:

```json
{
  "entry_script_path": "/path/to/project/validate_custom_ops_full.py",
  "run_command": "/path/to/project/.venv/bin/python /path/to/project/validate_custom_ops_full.py",
  "entry_script_kind": "custom_op_full_validation",
  "reports_dir": "/path/to/project/migration_reports",
  "operator_discovery_sources": ["source", "bindings", "wrappers", "autograd", "aliases", "launch", "setup", "tests"],
    "operator_inventory_schema": {
    "semantic_rows": "one row per fine-grained source-discovered operator unit; family-only rows are invalid final inventory rows",
    "fine_grained_operator_units": "complete list of source-discovered units, not just families",
    "unit_identity": "stable per-unit identity used consistently by source_inventory, manifest rows, and final gate rows",
    "variant_or_signature": "project-specific variant, signature, dtype/shape mode, or other discriminator discovered from source",
    "native_operator_symbols": "native/exported symbols per row",
    "kernel_functions": "CUDA/Ascend kernel functions per row",
    "kernel_launch_sites": "source locations or launch wrappers that call the kernels for this unit",
    "public_entry_mapping": "public Python/API/autograd entry points that route to this unit",
    "source_evidence": "source file/function evidence per row",
    "inventory_granularity": "must be fine_grained for the final inventory",
    "out_of_scope_source_groups": "excluded families with reason"
  },
  "performance_report_schema": {
    "per_unit_entries": "one speedup/parity entry for every manifest/source-inventory unit",
    "overall_baseline_seconds": "positive baseline timing for the full project/public API route",
    "overall_custom_seconds": "positive custom-op timing for the same route after all units are replaced",
    "overall_speedup_vs_baseline": "overall_custom_op_replaced speedup ratio versus baseline",
    "overall_all_units_replaced": true,
    "overall_route_proof": "evidence that the timed route is the project/public API path covering all units"
  },
  "required_report_paths": ["migration_reports/operator_inventory.json", "migration_reports/migration_manifest.json", "migration_reports/preflight.json", "migration_reports/baseline.json", "migration_reports/runtime_coverage.json", "migration_reports/performance.json", "migration_reports/build.json", "migration_reports/implementation_resolution.json", "migration_reports/custom_op_final_gate.json", "migration_reports/evidence_validation.json", "migration_reports/summary.json"],
  "required_checks": ["inventory_manifest_equality", "closed_pass_count_equals_manifest_entries", "remaining_entries_zero", "full_migration_status_full_pass", "fine_grained_operator_unit_inventory", "kernel_launch_site_inventory", "public_entry_mapping", "inventory_granularity_fine", "per_entry_opp_custom_op_artifact_evidence", "per_entry_adapter_evidence", "per_entry_parity_evidence", "integration_e2e_evidence", "same_run_runtime_coverage", "performance_evidence", "complete_performance_report", "overall_speedup_report", "strict_ascend_c_cann_opp_artifacts", "op_host_op_kernel_source_evidence", "cann_opp_build_install_provenance", "generated_opp_package_artifacts", "reject_npuextension_aten_only_as_opp_evidence", "reject_non_opp_producer_evidence", "project_root_artifact_existence", "final_chinese_per_row_table_parity", "no_fallback_no_zero_call_no_builtin_contamination", "native_operator_symbol_inventory"],
  "validation_obligations": ["project_local_artifact", "strict_opp_artifact", "op_host_op_kernel_source", "cann_opp_build_install", "generated_opp_package_artifacts", "reject_npuextension_aten_only", "reject_non_opp_producer_evidence", "project_root_artifact_existence", "runtime_project_api", "numeric_performance", "complete_speedup_report", "overall_speedup_report", "final_chinese_per_row_table", "no_fallback"],
  "phase5_entry_script_revision_allowed": true
}
```

## Field Semantics
- `entry_script_path`: absolute path to the selected script or wrapper.
- `run_command`: exact non-interactive command Phase 5 will execute, using the venv interpreter when available.
- `entry_script_kind`: use `custom_op_full_validation` for custom-op projects; omit for normal projects.
- `reports_dir`: target project's `migration_reports` directory for custom-op evidence.
- `operator_discovery_sources`: source locations the script must discover before validating; do not rely on external requirements docs for completion.
- `operator_inventory_schema`: required inventory fields; rows without fine-grained unit identity, variant/signature, native symbols, kernel launch sites, public entry mapping, or source evidence are incomplete.
- `performance_report_schema`: required `migration_reports/performance.json` / final `performance_report` fields for the final gate: per-unit entries for every manifest/source-inventory unit, `overall_baseline_seconds`, `overall_custom_seconds`, `overall_speedup_vs_baseline`, `overall_all_units_replaced=true` or equivalent nested all-units proof, and project/public API route proof.
- `required_report_paths`: required migration reports the script must produce/check.
- `required_checks`: fail-closed checks including strict Ascend C/CANN OPP producer artifacts, project-root artifact existence, native operator symbol/kernel inventory, complete `migration_reports/performance.json` per-unit speedup-report closure, final Chinese per-row table parity, and one overall/end-to-end speedup after every discovered custom-op unit has been replaced.
- `validation_obligations`: machine-checkable validation obligations; they must enforce full project-local strict OPP runtime migration, a complete per-unit speedup report, final Chinese per-row table parity, and an overall all-units-replaced speedup report, not smoke/MVP/report-only/NpuExtension-only or any other non-OPP producer success.
- `phase5_entry_script_revision_allowed`: `true` means Phase 5 may revise the entry script only to enforce this same full custom-op contract.
