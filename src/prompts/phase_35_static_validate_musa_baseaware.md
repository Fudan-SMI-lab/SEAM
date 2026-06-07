# Phase 3.5 - Static Compliance Check (MUXI, Base-Env-Aware)

You are executing `{phase_name}` for `{project_dir}`.

{execution_environment_context}

## Context from Earlier Steps
{previous_outputs}

## Goal
Statically validate the Phase 3 entry script and command contract before the target runtime executes it. Do not run the workload.

## Required Actions
1. Read `{entry_script_path}` and verify it exists on the host-visible filesystem.
2. Check for blocking input, REPL/debugger stops, GUI/display calls, and infinite loops without bounds.
3. Confirm `run_command` uses the Phase 2 `python_path` or a justified equivalent.
4. Confirm `run_command` does not contain `docker`, `podman`, container IDs, or container lifecycle commands.
5. Check host/container path semantics: host-visible fields stay under `{project_dir}`, while container-mode commands may use the container project path shown in the execution context.
6. If `entry_script_kind` is `custom_op_full_validation`, check that the contract includes reports_dir, required_report_paths, required_checks, operator discovery sources, source inventory obligations, runtime coverage, performance evidence, and no-fallback evidence.
7. For custom/native ops, reject import-only or report-only wrappers that do not compile/load/run accelerator evidence.
8. If `entry_script_kind` is `vllm_serving_validation` or `sglang_serving_validation`, require the generated `validate_vllm_serving.py` or `validate_sglang_serving.py` wrapper, platform-policy runtime env setup, real project launch/demo/API request execution, `serving_final_gate.json` output, and no CPU fallback, inline shell env prefix, or smoke/import-only success path.

## Hard Rules
- Do not execute the target runtime here.
- Do not accept CPU fallback or validation that only imports modules.
- Return exactly one JSON object and no other JSON.

## Output Format
```json
{
  "validation_passed": true,
  "issues": [],
  "fix_plan": "No issues found. Script is headless-compliant and uses the selected interpreter."
}
```
