# Phase 3 - Entry Script Confirmation (PPU, Base-Env-Aware, Entry-Fix)

You are executing `{phase_name}` for `{project_dir}`.

{execution_environment_context}

## Context
This is a CUDA - PPU migration workflow. The selected command will become the Phase 5 validation surface after Phase 4 migration and repair. PPU exposes CUDA-compatible APIs (`torch.cuda`), so `torch.cuda` calls are expected and correct in this environment.

## Goal
- Identify the TRUE entry script or command that validates the project's full real-world migration target.
- Prefer documented project commands and existing launchers over generated scripts.
- Create a script only when no usable command exists, and then exercise all core project features.
- Do not choose a smoke, MVP, import-only, direct-only, or partial command when the project has a broader migration target.

## User Constraints
{user_constraints}

The above user constraints are **binding**. They take Priority #0: explicit user-mandated entry scripts and commands win over any custom-op generated wrappers or heuristic choices. If the user specifies an entry script or command, you must select it regardless of other rules.

## Migration Constraints (from Phase 1.5)
{constraint_summary}

These constraints are binding. Consider them when selecting the entry script.

## Phase 2 Interpreter Choice (CRITICAL)

In this same OpenCode session, Phase 2 has already recorded its execution environment decision. Use it as follows:

- **Use Phase 2's `python_path` as the preferred interpreter** from the Phase 2 environment decision. This field is always present and guaranteed.
- Use Phase 2's `venv_path` and optional `env_type` only to understand context (base env vs project-local venv). They are hints, not bindings.
- The `python_path` from Phase 2 is a preferred choice; your `run_command` must be directly executable by the Phase 5 target execution backend.

## Custom-Op Mandatory Rules
If the project includes CUDA/C++ custom operators, select or create a non-interactive full validation script that discovers the inventory directly from source files, bindings, wrappers, autograd, aliases, launchers, setup scripts, and tests. The script must enumerate every source-discovered inventory unit before validation, execute coverage and performance checks for every unit, measure one overall/end-to-end speedup after all discovered custom-op units have been replaced and routed through the project/public API, and emit one final inventory row per fine-grained source-discovered operator unit.

## Decision Priority
0. **User-mandated entry scripts (Priority #0)**: If the user explicitly specifies an entry script or command via constraints, that takes absolute precedence over all other rules.
1. Use the project's documented non-interactive full validation command, adjusted only for absolute paths or the active Python interpreter chosen by Phase 2 in this session.
2. Use an existing launcher (`train.py`, `run.py`, `generate.py`, `main.py`, `demo.py`, project test/benchmark/e2e runner) with arguments that cover the full target.
3. For custom-op projects, create or select a full validation script when no documented full runner exists.
4. Create `{project_dir}/smoke_test.py` only as a last resort for non-custom-op projects with no existing command.

## Headless Execution Compliance
The entry command is executed automatically in Phase 5:
- No `input()`, `getpass()`, REPL/debugger stops, blocking GUI calls, or unbounded loops in the execution path.
- If the existing launcher is interactive, prefer documented non-interactive flags or environment variables. Otherwise create a wrapper that calls the real entry point with safe defaults.
- Do not invent unsupported CLI flags.
- If you create or select a generated/wrapper script, physically write it under `{project_dir}` before returning JSON. Never return an `entry_script_path` for a file that does not exist.
- **Verification requirement**: Before returning the final JSON, confirm the selected or created script file exists by reading its contents or listing it. Do NOT execute the full migration workload during Phase 3. You are selecting and verifying the entry script path, not running validation.

## Execution Backend Prohibition (CRITICAL)
The framework backend handles execution and lifecycle for the Phase 5 target execution environment. The `run_command` you return will be executed by that backend automatically.
- **Do NOT** include `docker exec`, `podman exec`, `docker run`, `podman run`, `podman create`, `podman start`, `podman stop`, `podman rm`, container names/IDs, or any host-level container lifecycle invocations in `run_command`.
- **Do NOT** reference pre-existing or shared containers; the framework manages execution/lifecycle.
- **Do**: return the direct command that runs the entry script in the target execution environment, e.g. `python3 /workspace/smoke_validate.py` or `python smoke_validate.py`.
- Execution and lifecycle are handled entirely by the framework backend. You must not attempt to manage containers or execution environment lifecycle yourself.

Example good: `python3 /workspace/smoke_validate.py`
Example bad: `podman exec my-container-name python3 /opt/.../smoke_validate.py`

You may reason freely about the choice, but return exactly one JSON object.

## Field Semantics (CRITICAL — read carefully)

- **`entry_script_path`**: MUST be a **host-visible absolute path** under `{project_dir}` that is readable by OpenCode tools (e.g. `read`) and by the Phase 3.5 static validator. In container workflows, the container mounts `{project_dir}` at a container workdir (shown in execution environment context as `container_project_dir`). If the file is at `${container_project_dir}/validate_fwi.py` inside the container, the corresponding host-visible path is `{project_dir}/validate_fwi.py`. **Never return a container-internal-only path as `entry_script_path`** — the Phase 3 validator resolves this path on the host filesystem.

- **`reports_dir`**: MUST be a **host-visible absolute path** to the project's `migration_reports` directory, normally `{project_dir}/migration_reports`. This path is used by the Phase 3 validator to locate report files on the host filesystem.

- **`run_command`**: The exact non-interactive command the Phase 5 execution backend will execute. This runs *inside* the container, so it may use container-visible paths (e.g. `${container_project_dir}/validate_fwi.py` or `/workspace/validate_fwi.py`). The Phase 5 backend handles host-to-container path rewriting for you. Do NOT include `docker exec`, `podman exec`, container names/IDs, or host-level container lifecycle invocations.

## Output Format
Return exactly one JSON object:

```json
{
  "entry_script_path": "{project_dir}/generate.py",
  "run_command": "python3 /workspace/generate.py --config /workspace/config.yml",
  "phase5_entry_script_revision_allowed": true
}
```

For CUDA/C++ custom-op projects, keep those fields and add this backward-compatible contract:

```json
{
  "entry_script_path": "{project_dir}/validate_custom_ops_full.py",
  "run_command": "python3 /workspace/validate_custom_ops_full.py",
  "entry_script_kind": "custom_op_full_validation",
  "reports_dir": "{project_dir}/migration_reports",
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

## Field Semantics (custom-op fields)
- `entry_script_kind`: use `custom_op_full_validation` for custom-op projects; omit for normal projects.
- `reports_dir`: target project's `migration_reports` directory for custom-op evidence. Must be a host-visible absolute path.
- `operator_discovery_sources`: source locations the script must discover before validating; do not rely on external requirements docs for completion.
- `operator_inventory_schema`: required inventory fields; rows without fine-grained unit identity, variant/signature, native symbols, kernel launch sites, public entry mapping, or source evidence are incomplete.
- `performance_report_schema`: required `migration_reports/performance.json` / final `performance_report` fields for the final gate: per-unit entries for every manifest/source-inventory unit, `overall_baseline_seconds`, `overall_custom_seconds`, `overall_speedup_vs_baseline`, `overall_all_units_replaced=true` or equivalent nested all-units proof, and project/public API route proof.
- `required_report_paths`: required migration reports the script must produce/check. Must include `build` (e.g. `migration_reports/build.json`).
- `required_checks`: fail-closed checks including native operator symbol/kernel inventory, complete `migration_reports/performance.json` per-unit speedup-report closure, and one overall/end-to-end speedup after every discovered custom-op unit has been replaced.
- `validation_obligations`: machine-checkable validation obligations; they must enforce full project-local runtime migration, a complete per-unit speedup report, and an overall all-units-replaced speedup report, not smoke/MVP/report-only success.
- `phase5_entry_script_revision_allowed`: `true` means Phase 5 may revise the entry script/command if validation finds the selected command or path is incorrect. For custom-op projects, revision is bounded to enforcing the same full custom-op contract.
