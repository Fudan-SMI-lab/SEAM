# Phase 3 - Entry Script Confirmation (Ascend NPU, Base-Env-Aware, Entry-Fix)

You are executing `{phase_name}` for `{project_dir}`.

{execution_environment_context}

## Context
This is a CUDA-to-Ascend NPU migration workflow. The selected command becomes the target runtime validation surface after rule migration and repair. Ascend execution should use `torch_npu`, `torch.npu`, CANN runtime/compiler tools, AscendC operators, or NPU-supported accelerator primitives. Original CUDA scripts may fail on NPU at this stage; do not avoid CUDA-dependent project paths only because they fail before migration.

## Goal
- Identify the TRUE entry script or command that validates the project's full real-world migration target.
- Prefer documented project commands and existing launchers over generated scripts.
- Create a script only when no usable command exists, and then exercise all core project features.
- Do not choose a smoke, MVP, import-only, direct-only, or partial command when the project has a broader migration target.
- For projects with native/custom ops, select or create a full validation script that emits the unchanged custom-op final-gate evidence schema.
- For vLLM, SGLang, local API, or other serving-backed workflows, choose an entry that owns the service lifecycle instead of only calling an already-running server.

## User Constraints
{user_constraints}

The above user constraints are binding. They take Priority #0: explicit user-mandated entry scripts and commands win over any custom-op generated wrappers or heuristic choices. If the user specifies an entry script or command, you must select it regardless of other rules.

## Migration Constraints (from Phase 1.5)
{constraint_summary}

These constraints are binding. Consider them when selecting the entry script.

## Phase 2 Interpreter Choice (CRITICAL)

In this same session, Phase 2 has already recorded its execution environment decision. Use it as follows:

- Use Phase 2's `python_path` as the preferred interpreter from the Phase 2 environment decision. This field is always present and guaranteed.
- Use Phase 2's `venv_path` and optional `env_type` only to understand context, base env vs project-local venv. They are hints, not bindings.
- The `python_path` from Phase 2 is a preferred choice; your `run_command` must be directly executable by the target execution backend.
- If Phase 2 selected a container/base interpreter with `torch_npu` and CANN already present, prefer that interpreter and do not switch to a host or fresh `.venv` interpreter.

## Custom-Op Mandatory Rules
If the project includes CUDA/C++ custom operators, AscendC operators, or native NPU operators, select or create a non-interactive full validation script that discovers the inventory directly from source files, bindings, wrappers, autograd, aliases, launchers, setup scripts, and tests. The script must enumerate every source-discovered inventory unit before validation, execute coverage and performance checks for every unit, measure one overall/end-to-end speedup after all discovered custom-op units have been replaced and routed through the Ascend NPU native path and the project/public API, and emit one final inventory row per fine-grained source-discovered operator unit. Each final-gate row must include evidence as objects/dicts, not strings or scalars, with `name` matching `unit_identity` for consistent identity across source_inventory, manifest rows, and performance report entries. The `no_fallback_no_zero_call_no_builtin_contamination` evidence must be an object with all negative flags explicitly `false`, including `fallback_detected`, `zero_call_detected`, `builtin_contamination_detected`, `baseline_only_detected`, and `stub_detected`. Script exit code 0 alone is insufficient; the `custom_op_final_gate.json` must pass structural evidence validation.

Performance validation is configurable via platform policy, with full, presence_only, or disabled modes. Speedup fields may be optional in presence_only mode. CPU may be accepted as a baseline device only when explicitly configured; CPU baseline is not CPU fallback. The custom/migrated path must still prove Ascend NPU/native route execution through `torch_npu`, `torch.npu`, CANN, AscendC artifacts, operator package artifacts, loaded native symbols, runtime coverage, and project/public API calls.

Do not claim there are no custom or native operators unless Phase 1 source evidence supports it. If custom or native operators exist, the entry script must compile, load, and run the native Ascend path and produce real project-local evidence for the observed NPU accelerator path. CPU fallback, marker-only artifacts, generated report-only success, and passthrough to built-in framework ops without source-discovered operator routing are invalid.

## Decision Priority
0. User-mandated entry scripts (Priority #0): If the user explicitly specifies an entry script or command via constraints, that takes absolute precedence over all other rules.
1. Use the project's documented non-interactive full validation command, adjusted only for absolute paths or the active Python interpreter chosen by Phase 2 in this session.
2. Use an existing launcher (`train.py`, `run.py`, `generate.py`, `main.py`, `demo.py`, project test/benchmark/e2e runner) with arguments that cover the full target.
3. For custom-op projects, create or select a full validation script when no documented full runner exists.
4. For serving-backed vLLM, SGLang, local OpenAI-compatible API, or local inference workflows, use or create a launcher that starts the server, waits for readiness, runs client validation, captures logs, and cleans up.
5. Create `{project_dir}/smoke_test.py` only as a last resort for non-custom-op projects with no existing command.

## Headless Execution Compliance
The entry command is executed automatically in the target runtime:
- No `input()`, `getpass()`, REPL/debugger stops, blocking GUI calls, or unbounded loops in the execution path.
- If the existing launcher is interactive, prefer documented non-interactive flags or environment variables. Otherwise create a wrapper that calls the real entry point with safe defaults.
- Do not invent unsupported CLI flags.
- If you create or select a generated/wrapper script, physically write it under `{project_dir}` before returning JSON. Never return an `entry_script_path` for a file that does not exist.
- If a generated/wrapper script launches child processes, it must drain and capture child stdout/stderr before exiting; do not let pipes block or drop output.
- On failure, generated/wrapper scripts must print a concise diagnostic summary to stderr that includes the child command, exit code, and the most relevant stderr or stdout tail. Long logs may be written to artifacts, but the failure summary must be visible on stderr.
- Verification requirement: Before returning the final JSON, confirm the selected or created script file exists by reading its contents or listing it. Do not execute the full migration workload during Phase 3. You are selecting and verifying the entry script path, not running validation. Do not build, adapt, repair, or migrate the project in Phase 3.

## Serving-Backed Entry Requirements
- Treat vLLM, SGLang, self-hosted/local inference services, OpenAI-compatible local APIs, localhost endpoints, `/v1/chat/completions`, and equivalent service/client modes as serving-backed validation surfaces.
- Do not select a client-only command that depends on a manually pre-started service.
- The selected script or documented launcher must own server startup, readiness polling with a finite timeout, client request validation, log capture, and cleanup of its own child processes.
- Use dynamic free ports or preflight checks for busy ports, then propagate the final base URL to the client.
- Captured logs must be available for failure diagnostics without deadlocking child stdout or stderr.

## Execution Backend Prohibition (CRITICAL)
The framework backend handles execution and lifecycle for the target execution environment. The `run_command` you return will be executed by that backend automatically.
- Do NOT include `docker exec`, `podman exec`, `docker run`, `podman run`, `podman create`, `podman start`, `podman stop`, `podman rm`, container names/IDs, or any host-level container lifecycle invocations in `run_command`.
- Do NOT reference pre-existing or shared containers; the framework manages execution/lifecycle.
- Do: return the direct command that runs the entry script in the target execution environment, e.g. `python3 /workspace/run_e2e.py` or `python /workspace/run_e2e.py`.
- Execution and lifecycle are handled entirely by the framework backend. You must not attempt to manage containers or execution environment lifecycle yourself.

Example good: `python3 /workspace/run_e2e.py`
Example bad: `podman exec my-container-name python3 /opt/project/run_e2e.py`

You may reason freely about the choice, but return exactly one JSON object.

## Field Semantics (CRITICAL, read carefully)

- `entry_script_path`: MUST be a host-visible absolute path under `{project_dir}` that is readable by file tools and by the Phase 3.5 static validator. In container workflows, the container mounts `{project_dir}` at a container workdir shown in execution environment context as `container_project_dir`. If the file is at `${container_project_dir}/run_e2e.py` inside the container, the corresponding host-visible path is `{project_dir}/run_e2e.py`. Never return a container-internal-only path as `entry_script_path`; the Phase 3 validator resolves this path on the host filesystem.

- `reports_dir`: MUST be a host-visible absolute path to the project's `migration_reports` directory, normally `{project_dir}/migration_reports`. This path is used by the Phase 3 validator to locate report files on the host filesystem.

- `run_command`: The exact non-interactive command the target execution backend will execute. This runs inside the container, so it may use container-visible paths such as `${container_project_dir}/run_e2e.py` or `/workspace/run_e2e.py`. The backend handles host-to-container path rewriting for you. Do NOT include `docker exec`, `podman exec`, container names/IDs, or host-level container lifecycle invocations.

## Normal Output Format
Return exactly one JSON object:

```json
{
  "entry_script_path": "{project_dir}/run_e2e.py",
  "run_command": "<phase2_python_path> /workspace/run_e2e.py",
  "phase5_entry_script_revision_allowed": true
}
```

## Custom-Op Output Format
Keep the evidence schema names unchanged:

```json
{
  "entry_script_path": "{project_dir}/validate_custom_ops_full.py",
  "run_command": "<phase2_python_path> /workspace/validate_custom_ops_full.py",
  "entry_script_kind": "custom_op_full_validation",
  "reports_dir": "{project_dir}/migration_reports",
  "operator_discovery_sources": ["source", "bindings", "wrappers", "autograd", "aliases", "launch", "setup", "tests"],
  "operator_inventory_schema": {
    "semantic_rows": "one row per fine-grained source-discovered operator unit",
    "fine_grained_operator_units": "complete list of source-discovered units",
    "unit_identity": "stable per-unit identity shared by source_inventory, manifest rows, and final gate rows",
    "variant_or_signature": "project-specific discriminator",
    "native_operator_symbols": "AscendC or exported native symbols per row",
    "kernel_functions": "AscendC/native kernel functions per row",
    "kernel_launch_sites": "source locations or wrappers that call kernels",
    "public_entry_mapping": "public Python/API/autograd entries routing to this unit",
    "source_evidence": "source file/function evidence per row",
    "inventory_granularity": "fine_grained",
    "out_of_scope_source_groups": "excluded families with reason"
  },
  "performance_report_schema": {
    "per_unit_entries": "one timing/parity entry for every manifest/source-inventory unit",
    "overall_baseline_seconds": "positive baseline timing for the full project/public API route",
    "overall_custom_seconds": "positive Ascend NPU/custom timing for the same route",
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

## Field Semantics (custom-op fields)
- In the examples above, `<phase2_python_path>` is a placeholder. Replace it with Phase 2's concrete `python_path` or a verified equivalent executable in the target runtime; never output the placeholder literally or hard-code a Python minor version unless Phase 2 selected that exact executable.
- `entry_script_kind`: use `custom_op_full_validation` for custom-op projects; omit for normal projects.
- `reports_dir`: target project's `migration_reports` directory for custom-op evidence. Must be a host-visible absolute path.
- `operator_discovery_sources`: source locations the script must discover before validating; do not rely on external requirements docs for completion.
- `operator_inventory_schema`: required inventory fields; rows without fine-grained unit identity, variant/signature, native symbols, kernel launch sites, public entry mapping, or source evidence are incomplete.
- `performance_report_schema`: required `migration_reports/performance.json` and final `performance_report` fields for the final gate: per-unit entries for every manifest/source-inventory unit, `overall_baseline_seconds`, `overall_custom_seconds`, `overall_speedup_vs_baseline`, `overall_all_units_replaced=true` or equivalent nested all-units proof, and project/public API route proof.
- `required_report_paths`: required migration reports the script must produce/check. Must include `build`, for example `migration_reports/build.json`.
- `required_checks`: fail-closed checks including native AscendC/operator artifact evidence, native operator symbol/kernel inventory, complete `migration_reports/performance.json` per-unit speedup-report closure, runtime coverage, no fallback, and one overall/end-to-end speedup after every discovered custom-op unit has been replaced.
- `validation_obligations`: machine-checkable validation obligations; they must enforce full project-local runtime migration, a complete per-unit speedup report, and an overall all-units-replaced speedup report, not smoke/MVP/report-only success.
- `phase5_entry_script_revision_allowed`: `true` means the target runtime phase may revise the entry script/command if validation finds the selected command or path is incorrect. For custom-op projects, revision is bounded to enforcing the same full custom-op contract.
