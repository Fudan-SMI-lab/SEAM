# Phase 3 - Entry Script Confirmation (MUSA/MUXI, Base-Env-Aware, Entry-Fix, Normal)

You are executing `{phase_name}` for `{project_dir}`.

{execution_environment_context}

## Context
This is a CUDA-to-MUSA/MUXI workflow. The selected command becomes the target runtime validation surface after rule migration and repair. MUSA execution should use `torch_musa`, `torch.musa`, MUSA SDK/compiler/runtime, or MUSA-supported accelerator primitives. If the actual MUXI environment exposes MACA/MetaX-compatible packages, use the observed vendor stack without changing the evidence schema.

This workflow uses the normal-entry route. Custom-op contract fields (`entry_script_kind`, `reports_dir`, `required_report_paths`, `required_checks`, `operator_discovery_sources`, `operator_inventory_schema`, `performance_report_schema`, `validation_obligations`) are omitted by route policy — the framework disables custom-op contract injection for this workflow, and the target runtime `custom_op_final_gate` auto-skips when no contract fields are present.

## Goal
- Identify the TRUE entry script or command that validates the project's full real-world migration target.
- Prefer documented project commands and existing launchers over generated scripts.
- Create a script only when no usable command exists, and then exercise all core project features.
- Do not choose a smoke, MVP, import-only, direct-only, or partial command when the project has a broader migration target.

## User Constraints
{user_constraints}

The above user constraints are **binding**. They take Priority #0: explicit user-mandated entry scripts and commands win over any heuristic choices. If the user specifies an entry script or command, you must select it regardless of other rules.

## Migration Constraints (from Phase 1.5)
{constraint_summary}

These constraints are binding. Consider them when selecting the entry script.

## Phase 2 Interpreter Choice (CRITICAL)

In this same session, Phase 2 has already recorded its execution environment decision. Use it as follows:

- **Use Phase 2's `python_path` as the preferred interpreter** from the Phase 2 environment decision. This field is always present and guaranteed.
- Use Phase 2's `venv_path` and optional `env_type` only to understand context (base env vs project-local venv). They are hints, not bindings.
- The `python_path` from Phase 2 is a preferred choice; your `run_command` must be directly executable by the target execution backend.

## Decision Priority
0. **User-mandated entry scripts (Priority #0)**: If the user explicitly specifies an entry script or command via constraints, that takes absolute precedence over all other rules.
1. Use the project's documented non-interactive full validation command, adjusted only for absolute paths or the active Python interpreter chosen by Phase 2 in this session.
2. Use an existing launcher (`train.py`, `run.py`, `generate.py`, `main.py`, `demo.py`, project test/benchmark/e2e runner) with arguments that cover the full target.
3. Create `{project_dir}/smoke_test.py` only as a last resort with no existing command.

## Headless Execution Compliance
The entry command is executed automatically in the target runtime:
- No `input()`, `getpass()`, REPL/debugger stops, blocking GUI calls, or unbounded loops in the execution path.
- If the existing launcher is interactive, prefer documented non-interactive flags or environment variables. Otherwise create a wrapper that calls the real entry point with safe defaults.
- Do not invent unsupported CLI flags.
- If you create or select a generated/wrapper script, physically write it under `{project_dir}` before returning JSON. Never return an `entry_script_path` for a file that does not exist.
- If a generated/wrapper script launches child processes, it must drain and capture child stdout/stderr before exiting; do not let pipes block or drop output.
- On failure, generated/wrapper scripts must print a concise diagnostic summary to stderr that includes the child command, exit code, and the most relevant stderr or stdout tail. Long logs may be written to artifacts, but the failure summary must be visible on stderr.
- **Verification requirement**: Before returning the final JSON, confirm the selected or created script file exists by reading its contents or listing it. Do NOT execute the full migration workload during Phase 3. You are selecting and verifying the entry script path, not running validation. Do not build, adapt, repair, or migrate the project in Phase 3.

## Execution Backend Prohibition (CRITICAL)
The framework backend handles execution and lifecycle for the target execution environment. The `run_command` you return will be executed by that backend automatically.
- **Do NOT** include `docker exec`, `podman exec`, `docker run`, `podman run`, `podman create`, `podman start`, `podman stop`, `podman rm`, container names/IDs, or any host-level container lifecycle invocations in `run_command`.
- **Do NOT** reference pre-existing or shared containers; the framework manages execution/lifecycle.
- **Do**: return the direct command that runs the entry script in the target execution environment, e.g. `python3 /workspace/run_e2e.py` or `python /workspace/run_e2e.py`.
- Execution and lifecycle are handled entirely by the framework backend. You must not attempt to manage containers or execution environment lifecycle yourself.

Example good: `python3 /workspace/run_e2e.py`
Example bad: `podman exec my-container-name python3 /opt/.../run_e2e.py`

## Output Format
Return exactly one JSON object. **Do NOT include any custom-op contract fields** — these are omitted by route policy for this workflow.

```json
{
  "entry_script_path": "{project_dir}/run_e2e.py",
  "run_command": "python3 /workspace/run_e2e.py",
  "phase5_entry_script_revision_allowed": true
}
```

## Field Semantics
- `entry_script_path`: absolute host-visible path to the selected or created script. This path is readable by file tools (such as `read`), by Phase 3.5 (static validator), and by the target execution backend after any path mapping.
- `run_command`: exact non-interactive command the target execution backend will execute in the target execution environment. Use visible paths or paths that the backend can map. Use the interpreter from Phase 2's `python_path` as the preferred choice in this session. Do NOT include `docker exec`, `podman exec`, container names/IDs, or host-level container lifecycle invocations.
- `phase5_entry_script_revision_allowed`: `true` (default) means the target runtime phase may revise the entry script or command if validation finds the selected command or path is incorrect. Revision is bounded to finding a working entry that matches the project's actual migration target.

## MUST NOT INCLUDE
- Do NOT output `entry_script_kind`, `reports_dir`, `required_report_paths`, `required_checks`, `operator_discovery_sources`, `operator_inventory_schema`, `performance_report_schema`, `validation_obligations`, `Custom-Op Output Format`, or any other custom-op contract fields — these are omitted by route policy.
