# Phase 3 - Entry Script Confirmation (MUSA/MUXI, Base-Env-Aware, Entry-Fix)

You are executing `{phase_name}` for `{project_dir}`.

{execution_environment_context}

## Context
This is a CUDA-to-MUSA/MUXI workflow. The selected command becomes the target runtime validation surface after rule migration and repair. MUSA execution should use `torch_musa`, `torch.musa`, MUSA SDK/compiler/runtime, or MUSA-supported accelerator primitives. If the actual MUXI environment exposes MACA/MetaX-compatible packages, use the observed vendor stack without changing the evidence schema.

## User Constraints
{user_constraints}

## Migration Constraints
{constraint_summary}

## Goal
- Select a non-interactive command that validates the full target behavior.
- Prefer user-mandated commands, documented project commands, and existing launchers.
- Generate a wrapper only when no usable command exists.
- For projects with native/custom ops, select or create a full validation script that emits the unchanged custom-op final-gate evidence schema.

## Hard Rules
- Do not include `docker exec`, `podman exec`, `docker run`, container IDs, or container lifecycle commands in `run_command`; the framework backend handles execution.
- `entry_script_path` and `reports_dir` must be host-visible absolute paths under `{project_dir}`.
- `run_command` must be directly executable inside the target execution environment and may use `/workspace` paths.
- If Phase 2 or the execution context identifies a vendor/base interpreter such as `/opt/conda/bin/python3.10`, use that absolute interpreter in `run_command`; do not use bare `python` or `python3.10` when they may resolve to system Python without vendor torch.
- Do not weaken validation to import-only, smoke-only, report-only, direct-only, or CPU fallback success.
- Do not say there are no custom operators unless Phase 1 source evidence supports it.
- If custom/native ops exist, the entry must compile/load/run the native MUSA path and produce evidence.

## Normal Output Format
```json
{
  "entry_script_path": "{project_dir}/run_e2e.py",
  "run_command": "/opt/conda/bin/python3.10 /workspace/run_e2e.py",
  "phase5_entry_script_revision_allowed": true
}
```

## Custom-Op Rules
- Do not say there are no custom operators unless Phase 1 source evidence supports it.
- If custom/native ops exist, the entry must compile, load, run, and produce real project-local evidence for the observed MUXI-family accelerator path.
- CPU fallback, marker-only artifacts, and report-only success are invalid.

## Custom-Op Output Format
Keep the evidence schema names unchanged:

```json
{
  "entry_script_path": "{project_dir}/validate_custom_ops_full.py",
  "run_command": "/opt/conda/bin/python3.10 /workspace/validate_custom_ops_full.py",
  "entry_script_kind": "custom_op_full_validation",
  "reports_dir": "{project_dir}/migration_reports",
  "operator_discovery_sources": ["source", "bindings", "wrappers", "autograd", "aliases", "launch", "setup", "tests"],
  "operator_inventory_schema": {
    "semantic_rows": "one row per fine-grained source-discovered operator unit",
    "fine_grained_operator_units": "complete list of source-discovered units",
    "unit_identity": "stable per-unit identity shared by source_inventory, manifest rows, and final gate rows",
    "variant_or_signature": "project-specific discriminator",
    "native_operator_symbols": "MUSA/exported symbols per row",
    "kernel_functions": "native kernel functions per row",
    "kernel_launch_sites": "source locations or wrappers that call kernels",
    "public_entry_mapping": "public Python/API/autograd entries routing to this unit",
    "source_evidence": "source file/function evidence per row",
    "inventory_granularity": "fine_grained",
    "out_of_scope_source_groups": "excluded families with reason"
  },
  "performance_report_schema": {
    "per_unit_entries": "one timing/parity entry for every manifest/source-inventory unit",
    "overall_baseline_seconds": "positive baseline timing for the full project/public API route",
    "overall_custom_seconds": "positive MUSA/custom timing for the same route",
    "overall_speedup_vs_baseline": "optional when policy performance_validation is presence_only",
    "overall_all_units_replaced": true,
    "overall_route_proof": "evidence that the timed route is the project/public API path covering all units"
  },
  "required_report_paths": ["migration_reports/operator_inventory.json", "migration_reports/migration_manifest.json", "migration_reports/preflight.json", "migration_reports/baseline.json", "migration_reports/runtime_coverage.json", "migration_reports/performance.json", "migration_reports/build.json", "migration_reports/implementation_resolution.json", "migration_reports/custom_op_final_gate.json", "migration_reports/evidence_validation.json", "migration_reports/summary.json"],
  "required_checks": ["inventory_manifest_equality", "closed_pass_count_equals_manifest_entries", "remaining_entries_zero", "full_migration_status_full_pass", "fine_grained_operator_unit_inventory", "kernel_launch_site_inventory", "public_entry_mapping", "inventory_granularity_fine", "per_entry_opp_custom_op_artifact_evidence", "per_entry_adapter_evidence", "per_entry_parity_evidence", "integration_e2e_evidence", "same_run_runtime_coverage", "performance_evidence", "complete_performance_report", "overall_speedup_report", "no_fallback_no_zero_call_no_builtin_contamination", "native_operator_symbol_inventory"],
  "validation_obligations": ["project_local_artifact", "runtime_project_api", "numeric_performance", "complete_speedup_report", "overall_speedup_report", "no_fallback"],
  "phase5_entry_script_revision_allowed": true
}
```
