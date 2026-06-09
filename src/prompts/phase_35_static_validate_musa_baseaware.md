# Phase 3.5 - Static Compliance Check (MUXI, Base-Env-Aware)

You are executing `{phase_name}` for `{project_dir}`.

{execution_environment_context}

## Prior Phase Context
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
8. Apply the Serving-Backed Entry Gate when Phase 3 output targets a self-hosted/local inference service.

## Serving-Backed Entry Gate
- Treat self-hosted/local inference services, vLLM, SGLang, Ollama, OpenAI-compatible local APIs, localhost endpoints, `/v1/chat/completions`, and equivalent service/client modes as serving-backed validation surfaces.
- Reject a selected command that is only a client call and depends on an unmanaged, manually pre-started service.
- Accept a selected entry script or documented launcher when it owns server lifecycle, readiness polling, client validation, log capture, and cleanup inside the framework-managed runtime.
- Preserve the user constraint boundary: user constraints may express simple intent; this phase audits whether the selected Phase 3 execution contract is complete and runnable.

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
