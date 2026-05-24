# Phase 3 - Entry Script Confirmation

You are executing `{phase_name}` for `{project_dir}`.

## Context
This is a CUDA -> Ascend NPU migration workflow. The selected command will become the Phase 5 validation surface after Phase 4 migration and repair. Original CUDA scripts may fail on NPU at this stage; do not avoid CUDA-dependent paths because they fail before migration.

## Goal
- Identify the TRUE entry script/command that validates the project's full real-world migration target.
- Prefer documented project commands and existing launchers over generated scripts.
- Create a script only when no usable command exists, and then exercise all core project features.
- Do not choose a smoke, MVP, import-only, direct-only, or partial command when the project has a broader migration target.

## Route-Aware Phase 3 Contract
- Ordinary CUDA route: preserve the existing behavior. Select the documented/project validation command or last-resort non-custom-op smoke script exactly as before; do not add custom-op report requirements.
- Custom-op route without expanded variants: select or generate a fail-closed validation script contract. The script must fail after Phase 5 unless every fine-grained Phase 1 unit has corresponding Ascend OPP build/install provenance in the Phase 5 reports, including `migration_reports/build.json`, CANN/OPP build evidence, install/provenance evidence, and strict `unit_identity` closure against the source/manifest/final-gate rows.
- Custom-op route with expanded variants: apply the custom-op route requirements and additionally require one `migration_reports/build.json` row for every expanded target variant, exact `unit_identity` set equality between expanded variants and build/final rows, `expanded_variant_inventory`, `variant_axis_coverage`, `per_variant_performance_report`, and CANN/OPP build/install provenance for every expanded variant. Collapsed rows, representative rows, and build.json existence-only checks are invalid.
- vLLM serving route: return `entry_script_kind="vllm_serving_validation"`, `serving_framework="vllm"`, the real project launch command, readiness probe, request validation, project demo/test/API files, expected outputs, runtime env, serving reports dir, required report paths, required checks, and serving validation obligations.
- SGLang serving route: return `entry_script_kind="sglang_serving_validation"`, `serving_framework="sglang"`, the same strict serving contract fields, and route/framework evidence tied to project-provided demo/test/API files.
- Serving success is strict: actual project demo/test/API validation must run against the serving endpoint on NPU; import-only, smoke-only, package-availability-only, synthetic, CPU fallback, CUDA fallback, stale reports, or route/framework mismatch are failures.

## Migration Constraints (from Phase 1.5)
{constraint_summary}

These constraints are binding. Consider them when selecting the entry script.

## Custom-Op Mandatory Rules
If the project includes CUDA/C++ custom operators, select or create a non-interactive full validation script that discovers the inventory directly from source files, bindings, wrappers, autograd, aliases, launchers, setup scripts, and tests. The script must enumerate every source-discovered inventory unit before validation, execute coverage and performance checks for every unit, measure one overall/end-to-end CPU-baseline vs Ascend OPP/custom-op speedup after all discovered custom-op units have been replaced and routed through the project/public API, and emit one final inventory row per fine-grained source-discovered operator unit, not per coarse family. Each unit must record unit identity, family/variant/signature, native symbols, kernel launch sites, public entry mapping, candidate public API routes, candidate framework integration routes, source evidence, and out-of-scope source groups. Final row evidence for each unit must include either `public_api_route_evidence` or `framework_integration_route_evidence` proving real same-run custom-op execution through that public/framework route, correlated to the row identity, with positive custom call count and native custom-op/OPP execution. Each route evidence field may be a single object or a non-empty object list; every object must independently satisfy the same strict proof checks, and empty lists or any invalid list item must fail closed. Its `opp_custom_op_artifact_evidence` must prove strict Ascend C/CANN OPP custom operator artifacts as the only acceptable producer target: op_host source, op_kernel/AscendC source, CMakeLists.txt/build.sh or equivalent OPP build script, CANN/OPP build-install log, install/provenance evidence, generated header/op_info/kernel_meta/producer/package artifacts, and the runtime-loaded compiled OPP artifact. Unknown future implementations that cannot satisfy this positive OPP producer contract must fail closed, even if they compile, run, or expose an Ascend-looking `.so`. `torch_npu.utils.cpp_extension.NpuExtension`, `torch.utils.cpp_extension.CppExtension`, ATen-only `npu_ops.cpp`, and libtorch/torch_cpu/torch_npu-only builds are not OPP producer evidence; NpuExtension can only be adapter evidence when separate strict OPP producer evidence exists. Its `migration_reports/performance.json` and final `performance_report` evidence must include per-unit entries for every manifest/source-inventory unit plus `overall_baseline_seconds` from a real CPU baseline, `overall_custom_seconds` from the Ascend OPP/custom-op route, `overall_speedup_vs_baseline`, `overall_all_units_replaced=true` or equivalent all-units-replaced proof, and project/public API route proof for the overall timing. Direct-only, builtin-only, fallback, zero-call, report-only, synthetic/mock, benchmark-only, baseline-only, stub, ATen-only, NpuExtension-only, CppExtension-only, or Python-shim route evidence must fail closed for active custom-op contracts only. The final Chinese summary must include the exact per-row report parity table columns required by the custom-op contract.

For custom-op projects whose Phase 1 analysis reports active expanded variants, the full validation script contract must be variant-aware: it must carry `expanded_variant_inventory`, `variant_axis_coverage`, and `per_variant_performance_report`; it must validate one manifest/source-inventory/final-gate row per expanded target Ascend OPP/custom-op variant identity; it must validate one `migration_reports/build.json` row per expanded target variant with exact `unit_identity` set equality and CANN/OPP build/install provenance; it must reject collapsed parameterized rows and any row/value derived from CPU/reference/baseline/host/ctypes/symbol-loader evidence in axis-like fields such as `device`, `backend`, `reference`, `baseline`, or `comparison`; and it must emit one performance entry per expanded target variant plus the overall all-units-replaced report. Omit these expanded-variant contract fields for non-variant custom-op projects.

## Decision Priority
1. Use the project's documented non-interactive full validation command, adjusted only for absolute paths or venv Python.
2. Use an existing launcher (`train.py`, `run.py`, `generate.py`, `main.py`, `demo.py`, project test/benchmark/e2e runner) with arguments that cover the full target.
3. For custom-op projects, create or select a full validation script such as `{project_dir}/validate_custom_ops_full.py` when no documented full runner exists; it must discover the source-driven fine-grained inventory, validate every discovered unit independently, run per-unit acceleration/speedup measurements, run one overall all-units-replaced project/public API acceleration/speedup measurement, and write the required migration reports instead of merely inspecting reports.
4. Create `{project_dir}/smoke_test.py` only as a last resort for non-custom-op projects with no existing command. It must import real modules, run realistic data flow, include CUDA-dependent modules, and have an `if __name__ == "__main__":` guard.

## Headless Execution Compliance
The entry command is executed automatically in Phase 5:
- Do not call `task()`, launch background/sub-agent work, create todos, or wait for background task notifications in this phase. Inspect or create files directly and return the phase JSON in this same response.
- No `input()`, `getpass()`, REPL/debugger stops, blocking GUI calls, or unbounded loops in the execution path.
- If the existing launcher is interactive, prefer documented non-interactive flags/env vars. Otherwise create a wrapper that calls the real entry point with safe defaults.
- Do not invent unsupported CLI flags.
- If you create or select a generated/wrapper script, physically write it under `{project_dir}` before returning JSON. Never return an `entry_script_path` for a file that does not exist; custom-op Phase 3 validation fails before Phase 3.5 when the selected script is missing.
- Do not wrap the real project/API/E2E validation route in a short internal subprocess timeout. For generated validation scripts, omit `timeout=` on the real public/API/E2E subprocess or make it explicitly long-running; a short value such as 600 seconds is invalid because each phase may legitimately run for hours while making progress.

You may reason freely in your response, but end it with a single JSON object containing exactly the required keys for this phase. No other JSON objects should appear.

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
  "project_dir": "/path/to/project",
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
    "candidate_public_api_routes": "public API routes that can execute this custom-op unit",
    "candidate_framework_integration_routes": "framework/module/autograd/training routes that can execute this custom-op unit",
    "route_evidence_fields": "final rows must include public_api_route_evidence or framework_integration_route_evidence for same-run custom-op execution; either field may be one object or a non-empty object list, and every object must independently pass the same strict proof checks",
    "source_evidence": "source file/function evidence per row",
    "inventory_granularity": "must be fine_grained for the final inventory",
    "out_of_scope_source_groups": "excluded families with reason"
  },
  "performance_report_schema": {
    "per_unit_entries": "one speedup/parity entry for every manifest/source-inventory unit",
    "overall_baseline_seconds": "positive CPU baseline timing for the full project/public API route",
    "overall_custom_seconds": "positive Ascend OPP/custom-op timing for the same route after all units are replaced",
    "overall_speedup_vs_baseline": "CPU baseline seconds divided by Ascend OPP/custom-op seconds",
    "overall_all_units_replaced": true,
    "overall_route_proof": "evidence that the timed route is the project/public API path covering all units"
  },
  "required_report_paths": ["migration_reports/operator_inventory.json", "migration_reports/migration_manifest.json", "migration_reports/preflight.json", "migration_reports/baseline.json", "migration_reports/runtime_coverage.json", "migration_reports/performance.json", "migration_reports/build.json", "migration_reports/implementation_resolution.json", "migration_reports/custom_op_final_gate.json", "migration_reports/evidence_validation.json", "migration_reports/summary.json"],
  "required_checks": ["inventory_manifest_equality", "closed_pass_count_equals_manifest_entries", "remaining_entries_zero", "full_migration_status_full_pass", "fine_grained_operator_unit_inventory", "kernel_launch_site_inventory", "public_entry_mapping", "inventory_granularity_fine", "per_entry_opp_custom_op_artifact_evidence", "per_entry_adapter_evidence", "per_entry_parity_evidence", "integration_e2e_evidence", "per_entry_public_api_or_framework_integration_route_evidence", "correlate_route_evidence_to_manifest_rows", "reject_direct_or_builtin_only_routes", "same_run_runtime_coverage", "performance_evidence", "complete_performance_report", "overall_speedup_report", "strict_ascend_c_cann_opp_artifacts", "op_host_op_kernel_source_evidence", "cann_opp_build_install_provenance", "generated_opp_package_artifacts", "reject_npuextension_aten_only_as_opp_evidence", "reject_non_opp_producer_evidence", "project_root_artifact_existence", "final_chinese_per_row_table_parity", "no_fallback_no_zero_call_no_builtin_contamination", "native_operator_symbol_inventory"],
  "validation_obligations": ["project_local_artifact", "strict_opp_artifact", "op_host_op_kernel_source", "cann_opp_build_install", "generated_opp_package_artifacts", "reject_npuextension_aten_only", "reject_non_opp_producer_evidence", "project_root_artifact_existence", "runtime_project_api", "per_row_public_or_framework_route_evidence", "reject_direct_builtin_only_routes", "numeric_performance", "complete_speedup_report", "overall_speedup_report", "final_chinese_per_row_table", "no_fallback"],
  "phase5_entry_script_revision_allowed": true
}
```

If and only if Phase 1 reports active expanded variants, add this overlay and append `expanded_variant_inventory`, `variant_axis_coverage`, and `per_variant_performance_report` to `required_checks`:

```json
{
  "expanded_variant_inventory": {"variant_axes_detected": true, "unit_identities": ["custom_family_alpha:signature_x:axis_name=value_a"], "target_closure_only": true, "expanded_operator_instances_count": 1},
  "variant_axis_coverage": {"all_axes_covered": true, "axes": {"axis_name": ["value_a", "value_b"]}},
  "per_variant_performance_report": {"required": true, "one_entry_per_expanded_variant": true}
}
```

## Field Semantics
- `entry_script_path`: absolute path to the selected script or wrapper.
- `run_command`: exact non-interactive command Phase 5 will execute, using the venv interpreter when available.
- `project_dir`: trusted target project root for custom-op contracts; use `{project_dir}` exactly.
- `entry_script_kind`: use `custom_op_full_validation` for custom-op projects; omit for normal projects.
- `reports_dir`: target project's `migration_reports` directory for custom-op evidence.
- `operator_discovery_sources`: source locations the script must discover before validating; do not rely on external requirements docs for completion.
- `operator_inventory_schema`: required inventory fields; rows without fine-grained unit identity, variant/signature, native symbols, kernel launch sites, public entry mapping, candidate public/framework integration routes, route evidence fields, or source evidence are incomplete. Route evidence fields may be one object or a non-empty object list only; empty lists and partially invalid lists are incomplete.
- `performance_report_schema`: required `migration_reports/performance.json` / final `performance_report` fields for the final gate: per-unit entries for every manifest/source-inventory unit, `overall_baseline_seconds` from a real CPU baseline, `overall_custom_seconds` from the Ascend OPP/custom-op route, `overall_speedup_vs_baseline`, `overall_all_units_replaced=true` or equivalent nested all-units proof, and project/public API route proof.
- `expanded_variant_inventory`, `variant_axis_coverage`, and `per_variant_performance_report`: optional fields that become required only when Phase 1 reports active expanded variants; omit them for non-variant custom-op projects. Expanded variant inventory is the target Ascend OPP/custom-op closure only; CPU/reference/baseline/loader evidence can support reports but cannot create target expanded variant rows.
- `required_report_paths`: required migration reports the script must produce/check.
- `required_checks`: fail-closed checks including strict Ascend C/CANN OPP producer artifacts, project-root artifact existence, native operator symbol/kernel inventory, per-row public API or framework integration route evidence correlated to manifest rows, complete `migration_reports/performance.json` per-unit speedup-report closure, final Chinese per-row table parity, and one overall/end-to-end CPU-baseline vs Ascend OPP/custom-op speedup after every discovered custom-op unit has been replaced. The route evidence shape check accepts one object or a non-empty object list only, and every list item must independently pass the same strict checks.
- `validation_obligations`: machine-checkable validation obligations; they must enforce full project-local strict OPP runtime migration, per-row same-run public API or framework integration route evidence, rejection of empty or partially invalid route-evidence lists and direct/builtin-only routes, a complete per-unit CPU-baseline vs Ascend OPP/custom-op speedup report, final Chinese per-row table parity, and an overall all-units-replaced speedup report, not smoke/MVP/report-only/NpuExtension-only or any other non-OPP producer success.
- `phase5_entry_script_revision_allowed`: `true` means Phase 5 may revise the entry script only to enforce this same full custom-op contract.
- Generated custom-op validation scripts must not impose short internal timeouts around the real project/API/E2E validation route; the framework/session monitor handles long-running phases.
