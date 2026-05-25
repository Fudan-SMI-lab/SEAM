# Phase 3 - Entry Script Confirmation (PPU, Base-Env-Aware)

You are executing `{phase_name}` for `{project_dir}`.

{execution_environment_context}

## Context
This is a CUDA - PPU migration workflow. The selected command will become the target runtime validation surface after rule migration and repair. PPU exposes CUDA-compatible APIs (`torch.cuda`), so `torch.cuda` calls are expected and correct in this environment.

## Goal
- Identify the TRUE entry script or command that validates the project's full real-world migration target.
- Prefer documented project commands and existing launchers over generated scripts.
- Create a script only when no usable command exists, and then exercise all core project features.
- Do not choose a smoke, MVP, import-only, direct-only, or partial command when the project has a broader migration target.

## Migration Constraints (from Phase 1.5)
{constraint_summary}

These constraints are binding. Consider them when selecting the entry script.

## Phase 2 Interpreter Choice (CRITICAL)

In this same session, Phase 2 has already recorded its execution environment decision. Use it as follows:

- **Use Phase 2's `python_path` as the preferred interpreter** from the Phase 2 environment decision. This field is always present and guaranteed.
- Use Phase 2's `venv_path` and optional `env_type` only to understand context (base env vs project-local venv). They are hints, not bindings.
- The `python_path` from Phase 2 is a preferred choice; your `run_command` must be directly executable by the target execution backend.

## Custom-Op Mandatory Rules
If the project includes CUDA/C++ custom operators, select or create a non-interactive full validation script that discovers the inventory directly from source files, bindings, wrappers, autograd, aliases, launchers, setup scripts, and tests. The script must enumerate every source-discovered inventory unit before validation, execute coverage and performance checks for every unit, measure one overall/end-to-end speedup after all discovered custom-op units have been replaced and routed through the project/public API, and emit one final inventory row per fine-grained source-discovered operator unit. Each final-gate row must include evidence as objects/dicts (not strings or scalars), with `name` matching `unit_identity` for consistent identity across source_inventory, manifest rows, and performance report entries. The `no_fallback_no_zero_call_no_builtin_contamination` evidence must be an object with all negative flags explicitly `false`. Script exit code 0 alone is insufficient — the custom_op_final_gate.json must pass structural evidence validation.

Performance validation is configurable via platform policy (full/presence_only/disabled modes). Speedup fields may be optional in presence_only mode. CPU may be accepted as a baseline device only when explicitly configured; CPU baseline is not CPU fallback. The custom/migrated path must still prove target accelerator/native route execution.

## Decision Priority
1. Use the project's documented non-interactive full validation command, adjusted only for absolute paths or the active Python interpreter chosen by Phase 2 in this session.
2. Use an existing launcher (`train.py`, `run.py`, `generate.py`, `main.py`, `demo.py`, project test/benchmark/e2e runner) with arguments that cover the full target.
3. For custom-op projects, create or select a full validation script when no documented full runner exists.
4. Create `{project_dir}/smoke_test.py` only as a last resort for non-custom-op projects with no existing command.

## Headless Execution Compliance
The entry command is executed automatically in the target runtime:
- No `input()`, `getpass()`, REPL/debugger stops, blocking GUI calls, or unbounded loops in the execution path.
- If the existing launcher is interactive, prefer documented non-interactive flags or environment variables. Otherwise create a wrapper that calls the real entry point with safe defaults.
- Do not invent unsupported CLI flags.
- If you create or select a generated/wrapper script, physically write it under `{project_dir}` before returning JSON. Never return an `entry_script_path` for a file that does not exist.
- **Verification requirement**: Before returning the final JSON, confirm the selected or created script file exists by reading its contents or listing it. Do NOT execute the full migration workload during Phase 3. You are selecting and verifying the entry script path, not running validation.

## Execution Backend Prohibition (CRITICAL)
The framework backend handles execution and lifecycle for the target execution environment. The `run_command` you return will be executed by that backend automatically.
- **Do NOT** include `docker exec`, `podman exec`, `docker run`, `podman run`, `podman create`, `podman start`, `podman stop`, `podman rm`, container names/IDs, or any host-level container lifecycle invocations in `run_command`.
- **Do NOT** reference pre-existing or shared containers; the framework manages execution/lifecycle.
- **Do**: return the direct command that runs the entry script in the target execution environment, e.g. `python3 /workspace/smoke_validate.py` or `python smoke_validate.py`.
- Execution and lifecycle are handled entirely by the framework backend. You must not attempt to manage containers or execution environment lifecycle yourself.

Example good: `python3 /workspace/smoke_validate.py`
Example bad: `podman exec zihang_vllm_ppu python3 /home/.../smoke_validate.py`

You may reason freely about the choice, but return exactly one JSON object.

## Output Format
Return exactly one JSON object:

```json
{
  "entry_script_path": "/path/to/project/generate.py",
  "run_command": "python3 /path/to/project/generate.py --config /path/to/project/config.yml",
  "phase5_entry_script_revision_allowed": true
}
```

For CUDA/C++ custom-op projects, keep those fields and add the backward-compatible contract from the original phase_3_entry_script prompt.

## Field Semantics
- `entry_script_path`: absolute path to the selected or created script. This path is readable by file tools (such as `read`), by Phase 3.5 (static validator), and by the target execution backend after any path mapping.
- `run_command`: exact non-interactive command the target execution backend will execute in the target execution environment. Use visible paths or paths that the backend can map. Use the interpreter from Phase 2's `python_path` as the preferred choice in this session. Do NOT include `docker exec`, `podman exec`, container names/IDs, or host-level container lifecycle invocations.
- `phase5_entry_script_revision_allowed`: `true` (default) means the target runtime phase may revise the entry script or command if validation finds the selected command or path is incorrect. Revision is bounded to finding a working entry that matches the project's actual migration target.
