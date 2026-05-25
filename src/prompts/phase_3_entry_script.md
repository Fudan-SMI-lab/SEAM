# Phase 3 - Entry Script Confirmation

You are executing `{phase_name}` for `{project_dir}`.

## Context
This is a CUDA -> Ascend NPU migration workflow. The selected command will become the target runtime validation surface after rule migration and repair. Original CUDA scripts may fail on NPU at this stage; do not avoid CUDA-dependent paths because they fail before migration.

## Goal
- Identify the TRUE entry script/command that validates the project's full real-world migration target.
- Prefer documented project commands and existing launchers over generated scripts.
- Create a script only when no usable command exists, and then exercise all core project features.
- Do not choose a smoke, MVP, import-only, direct-only, or partial command when the project has a broader migration target.

## Migration Constraints (from Phase 1.5)
{constraint_summary}

These constraints are binding. Consider them when selecting the entry script.

## Custom-Op Mandatory Rules
If the project includes CUDA/C++ custom operators, select or create a non-interactive full validation script that discovers the inventory directly from source files, bindings, wrappers, autograd, aliases, launchers, setup scripts, and tests. The script must enumerate every source-discovered inventory unit before validation, execute coverage and performance checks for every unit, measure one overall/end-to-end speedup after all discovered custom-op units have been replaced and routed through the project/public API, and emit one final inventory row per fine-grained source-discovered operator unit, not per coarse family. Each final-gate row must include evidence as objects/dicts (not strings or scalars), with `name` matching `unit_identity` for consistent identity across source_inventory, manifest rows, performance report entries, and final gate rows. The `no_fallback_no_zero_call_no_builtin_contamination` evidence must be an object with all negative flags explicitly `false` (fallback_detected, zero_call_detected, builtin_contamination_detected, baseline_only_detected, stub_detected). Script exit code 0 alone is insufficient — the custom_op_final_gate.json must pass structural evidence validation.

Performance validation is configurable via the platform policy (`performance_validation` field). Three modes exist: `full` (default — require baseline_seconds, custom_seconds, speedup_vs_baseline, and overall speedup fields with positive values), `presence_only` (require real baseline/custom timing presence and route/device proof but speedup fields are optional), and `disabled` (skip performance validation; all other gates remain active). CPU may be an accepted baseline device only when explicitly configured in the platform policy; CPU baseline does NOT imply CPU fallback is allowed for the custom/migrated path. Each unit must record unit identity, family/variant/signature, native symbols, kernel launch sites, public entry mapping, source evidence, and out-of-scope source groups. Its `migration_reports/performance.json` and final `performance_report` evidence must include per-unit entries for every manifest/source-inventory unit plus `overall_baseline_seconds`, `overall_custom_seconds`, `overall_speedup_vs_baseline` (unless configured otherwise), `overall_all_units_replaced=true` or equivalent all-units-replaced proof, and project/public API route proof for the overall timing.

## Decision Priority
1. Use the project's documented non-interactive full validation command, adjusted only for absolute paths or venv Python.
2. Use an existing launcher (`train.py`, `run.py`, `generate.py`, `main.py`, `demo.py`, project test/benchmark/e2e runner) with arguments that cover the full target.
3. For custom-op projects, create or select a full validation script such as `{project_dir}/validate_custom_ops_full.py` when no documented full runner exists; it must discover the source-driven fine-grained inventory, validate every discovered unit independently, run per-unit acceleration/speedup measurements, run one overall all-units-replaced project/public API acceleration/speedup measurement, and write the required migration reports instead of merely inspecting reports.
4. Create `{project_dir}/smoke_test.py` only as a last resort for non-custom-op projects with no existing command. It must import real modules, run realistic data flow, include CUDA-dependent modules, and have an `if __name__ == "__main__":` guard.

## Headless Execution Compliance
The entry command is executed automatically in the target runtime:
- No `input()`, `getpass()`, REPL/debugger stops, blocking GUI calls, or unbounded loops in the execution path.
- If the existing launcher is interactive, prefer documented non-interactive flags/env vars. Otherwise create a wrapper that calls the real entry point with safe defaults.
- Do not invent unsupported CLI flags.
- If you create or select a generated/wrapper script, physically write it under `{project_dir}` before returning JSON. Never return an `entry_script_path` for a file that does not exist; custom-op Phase 3 validation fails before Phase 3.5 when the selected script is missing.
- **Verification requirement**: Before returning the final JSON, confirm the selected/created script file exists by reading its contents or listing it. Do NOT execute the full migration workload during Phase 3; you are selecting and verifying the entry script path, not running validation.

You may reason freely about the choice, but return exactly one JSON object.

## Output Format
Return exactly one JSON object. Legacy projects may return only the two existing fields:

```json
{
  "entry_script_path": "/path/to/project/generate.py",
  "run_command": "/path/to/project/.venv/bin/python /path/to/project/generate.py --config /path/to/project/config.yml",
  "phase5_entry_script_revision_allowed": true
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
  "required_checks": ["inventory_manifest_equality", "closed_pass_count_equals_manifest_entries", "remaining_entries_zero", "full_migration_status_full_pass", "fine_grained_operator_unit_inventory", "kernel_launch_site_inventory", "public_entry_mapping", "inventory_granularity_fine", "per_entry_opp_custom_op_artifact_evidence", "per_entry_adapter_evidence", "per_entry_parity_evidence", "integration_e2e_evidence", "same_run_runtime_coverage", "performance_evidence", "complete_performance_report", "overall_speedup_report", "no_fallback_no_zero_call_no_builtin_contamination", "native_operator_symbol_inventory"],
  "validation_obligations": ["project_local_artifact", "runtime_project_api", "numeric_performance", "complete_speedup_report", "overall_speedup_report", "no_fallback"],
  "phase5_entry_script_revision_allowed": true
}
```

## Field Semantics
- `entry_script_path`: host-visible absolute path to the selected or created script. This path is readable by file tools (such as `read`), by Phase 3.5 (static validator), and by the target execution backend after any container path mapping.
- `run_command`: exact non-interactive command the target runtime will execute. In container workflows, the framework executes this command inside its created container; use container-visible paths or host paths that the backend can map. Do NOT include `docker exec`, `podman exec`, container names/IDs, or host-level container lifecycle invocations.
- `entry_script_kind`: use `custom_op_full_validation` for custom-op projects; omit for normal projects.
- `reports_dir`: target project's `migration_reports` directory for custom-op evidence.
- `operator_discovery_sources`: source locations the script must discover before validating; do not rely on external requirements docs for completion.
- `operator_inventory_schema`: required inventory fields; rows without fine-grained unit identity, variant/signature, native symbols, kernel launch sites, public entry mapping, or source evidence are incomplete.
- `performance_report_schema`: required `migration_reports/performance.json` / final `performance_report` fields for the final gate: per-unit entries for every manifest/source-inventory unit, `overall_baseline_seconds`, `overall_custom_seconds`, `overall_speedup_vs_baseline`, `overall_all_units_replaced=true` or equivalent nested all-units proof, and project/public API route proof.
- `required_report_paths`: required migration reports the script must produce/check.
- `required_checks`: fail-closed checks including native operator symbol/kernel inventory, complete `migration_reports/performance.json` per-unit speedup-report closure, and one overall/end-to-end speedup after every discovered custom-op unit has been replaced.
- `validation_obligations`: machine-checkable validation obligations; they must enforce full project-local runtime migration, a complete per-unit speedup report, and an overall all-units-replaced speedup report, not smoke/MVP/report-only success.
- `phase5_entry_script_revision_allowed`: `true` means the target runtime phase may revise the entry script/command if validation finds the selected command or path is incorrect. For custom-op projects, revision is bounded to enforcing the same full custom-op contract. For non-custom-op projects, revision is similarly bounded to finding a working entry that matches the project's actual migration target.
