# Phase 3 - Entry Script Confirmation (PPU)

You are executing `{phase_name}` for `{project_dir}`.

## Context
This is a CUDA -> PPU migration workflow. The selected command will become the target runtime validation surface after rule migration and repair. PPU exposes CUDA-compatible APIs (`torch.cuda`), so `torch.cuda` calls are expected and correct in this environment.

## Goal
- Identify the TRUE entry script/command that validates the project's full real-world migration target.
- Prefer documented project commands and existing launchers over generated scripts.
- Create a script only when no usable command exists, and then exercise all core project features.
- Do not choose a smoke, MVP, import-only, direct-only, or partial command when the project has a broader migration target.

## Migration Constraints (from Phase 1.5)
{constraint_summary}

These constraints are binding. Consider them when selecting the entry script.

## Custom-Op Mandatory Rules
If the project includes CUDA/C++ custom operators, select or create a non-interactive full validation script that discovers the inventory directly from source files, bindings, wrappers, autograd, aliases, launchers, setup scripts, and tests. The script must enumerate every source-discovered inventory unit before validation, execute coverage and performance checks for every unit, measure one overall/end-to-end speedup after all discovered custom-op units have been replaced and routed through the project/public API, and emit one final inventory row per fine-grained source-discovered operator unit. Each final-gate row must include evidence as objects/dicts (not strings or scalars), with `name` matching `unit_identity` for consistent identity across source_inventory, manifest rows, and performance report entries. The `no_fallback_no_zero_call_no_builtin_contamination` evidence must be an object with all negative flags explicitly `false`. Script exit code 0 alone is insufficient — the custom_op_final_gate.json must pass structural evidence validation.

Performance validation is configurable via platform policy (full/presence_only/disabled modes). Speedup fields may be optional in presence_only mode. CPU may be accepted as a baseline device only when explicitly configured; CPU baseline is not CPU fallback. The custom/migrated path must still prove target accelerator/native route execution.

## Decision Priority
1. Use the project's documented non-interactive full validation command, adjusted only for absolute paths or the active Python interpreter (container base env by default; project-local `.venv` only if Phase 2 created one explicitly).
2. Use an existing launcher (`train.py`, `run.py`, `generate.py`, `main.py`, `demo.py`, project test/benchmark/e2e runner) with arguments that cover the full target.
3. For custom-op projects, create or select a full validation script when no documented full runner exists.
4. Create `{project_dir}/smoke_test.py` only as a last resort for non-custom-op projects with no existing command.

## Headless Execution Compliance
The entry command is executed automatically in the target runtime:
- No `input()`, `getpass()`, REPL/debugger stops, blocking GUI calls, or unbounded loops in the execution path.
- If the existing launcher is interactive, prefer documented non-interactive flags/env vars. Otherwise create a wrapper that calls the real entry point with safe defaults.
- Do not invent unsupported CLI flags.
- If you create or select a generated/wrapper script, physically write it under `{project_dir}` before returning JSON. Never return an `entry_script_path` for a file that does not exist.
- If a generated/wrapper script launches child processes, it must drain and capture child stdout/stderr before exiting; do not let pipes block or drop output.
- On failure, generated/wrapper scripts must print a concise diagnostic summary to stderr that includes the child command, exit code, and the most relevant stderr or stdout tail. Long logs may be written to artifacts, but the failure summary must be visible on stderr.
- **Verification requirement**: Before returning the final JSON, confirm the selected/created script file exists by reading its contents or listing it. Do NOT execute the full migration workload during Phase 3; you are selecting and verifying the entry script path, not running validation.

## Container Workflow Prohibition (CRITICAL)
This workflow runs inside a framework-created container. The `run_command` you return will be executed *inside* that container automatically.
- **Do NOT** include `docker exec`, `podman exec`, container names/IDs, or host-level container invocations in `run_command`.
- **Do NOT** reference pre-existing or shared containers — the framework already created an exclusive container for this workflow.
- **Do**: return the direct command that runs the entry script inside the container, e.g. `python3 /workspace/smoke_validate.py` or `python3 smoke_validate.py`.

Example good: `python3 /workspace/smoke_validate.py`
Example bad: `podman exec zihang_vllm_ppu python3 /home/.../smoke_validate.py`

You may reason freely about the choice, but return exactly one JSON object.

## Output Format
Return exactly one JSON object. Legacy projects may return only the two existing fields:

```json
{
  "entry_script_path": "/path/to/project/generate.py",
  "run_command": "python /path/to/project/generate.py --config /path/to/project/config.yml",
  "phase5_entry_script_revision_allowed": true
}
```

For CUDA/C++ custom-op projects, keep those fields and add the backward-compatible contract as in the original phase_3_entry_script prompt.

## Field Semantics
- `entry_script_path`: host-visible absolute path to the selected or created script. This path is readable by file tools (such as `read`), by Phase 3.5 (static validator), and by the target execution backend after any container path mapping.
- `run_command`: exact non-interactive command the target runtime will execute inside the framework-created container. Use container-visible paths or host paths that the backend can map. Use the container base Python interpreter by default; use a project-local venv interpreter only if Phase 2 explicitly created one. Do NOT include `docker exec`, `podman exec`, container names/IDs, or host-level container lifecycle invocations.
- `phase5_entry_script_revision_allowed`: `true` (default) means the target runtime phase may revise the entry script/command if validation finds the selected command or path is incorrect. Revision is bounded to finding a working entry that matches the project's actual migration target.
