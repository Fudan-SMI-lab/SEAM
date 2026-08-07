# Phase 3.5 - Static Compliance Check (Ascend NPU, Base-Env-Aware)

You are executing `{phase_name}` for `{project_dir}`.

{execution_environment_context}

## Prior Phase Context
{previous_outputs}

## Context
This is Phase 3.5 in the CUDA-to-Ascend NPU migration workflow. Phase 3 has selected an entry script and run command. Your job is to statically analyze the selected entry script and command contract before the target runtime executes it. Do not run the workload.

## Goal
Validate that the Phase 3 entry script and command can run non-interactively in the framework-managed target runtime, use the selected base/container interpreter correctly, preserve host/container path semantics, and enforce custom-op or serving-backed requirements when present.

## Required Actions
1. Read `{entry_script_path}` and verify it exists on the host-visible filesystem.
2. Check for blocking input, REPL/debugger stops, GUI/display calls, blocking waits, and infinite loops without bounds.
3. Confirm `run_command` uses the Phase 2 `python_path` or a justified equivalent valid in the target runtime.
4. Confirm `run_command` does not contain `docker`, `podman`, container IDs, pre-existing container names, or container lifecycle commands.
5. Check host/container path semantics: host-visible fields stay under `{project_dir}`, while container-mode commands may use the container project path shown in the execution context.
6. Check the script does not switch to a host-only interpreter or fresh `.venv` when Phase 2 selected a container/base interpreter that provides `torch`, `torch_npu`, and CANN.
7. If `entry_script_kind` is `custom_op_full_validation`, check that the contract includes reports_dir, required_report_paths, required_checks, operator discovery sources, source inventory obligations, native AscendC/operator artifact evidence, runtime coverage, performance evidence, and no-fallback evidence.
8. For custom/native ops, reject import-only, report-only, marker-only, CPU fallback, stub, zero-call, or built-in-op-contaminated wrappers that do not compile/load/run Ascend NPU native evidence.
9. Apply the Serving-Backed Entry Gate when Phase 3 output targets a self-hosted/local inference service.

## Analysis Checklist
Examine the entry script file at `{entry_script_path}`. This path is a host-visible absolute path provided by the Phase 3 output; it is readable via file tools and accessible to the target execution backend after any path mapping. Check for:

1. Interactive input calls: `input()`, `raw_input()`, `getpass()`, `getpass.getpass()`, `code.interact()`, `cmd.Cmd()`, `click.prompt()`, `rich.prompt.*`.
2. Infinite loops without exit: `while True:` or `while 1:` loops that have no `break`, no signal handler, no epoch/step limit, and no timeout mechanism.
3. Interactive GUI/display calls: `cv2.imshow()`, `cv2.waitKey()`, `matplotlib.pyplot.show()`, `Tk().mainloop()`, PyQt/PySide event loops.
4. Debug/REPL breakpoints: `pdb.set_trace()`, `breakpoint()`, `IPython.embed()`, `code.interact()`.
5. Blocking waits: `threading.Event().wait()`, `queue.get()`, process waits, or network readiness waits without timeouts in the main execution path.

## Custom-Op Contract Gate
The `previous_outputs` block above contains the Phase 3 output, including entry script path, run command, and custom-op contract fields when present.

If Phase 3 includes `entry_script_kind: custom_op_full_validation`, validate the selected script against the source-discovery contract embedded in `previous_outputs`, the `migration_reports/` paths in `required_report_paths`, and the `required_checks` in `previous_outputs`. Set `validation_passed=false` for report-only, smoke, MVP, partial, synthetic, or benchmark-only routes, missing source inventory discovery, missing native AscendC/operator symbol/kernel inventory or source evidence, missing out-of-scope groups, missing project-local artifacts, missing project API custom-op invocation, missing runtime coverage, missing numeric performance, or fallback/zero-call/built-in/stub success. Reject inventories that only list row names/counts, group multiple source-discovered units into a family-only row, omit unit identity or variant/signature, omit kernel launch sites, omit public-entry mapping, or fail to prove source-driven fine-grained discovery.

For passing custom-op outputs, include `custom_op_static_required: true` plus these booleans set to `true`: `custom_op_requirements_checked`, `script_source_driven_inventory`, `script_emits_fine_grained_units`, `script_maps_public_api_to_units`, `script_discovers_full_inventory`, `script_records_native_operator_symbols`, `script_records_ascendc_operator_artifacts`, `script_runs_project_api_custom_ops`, `script_rejects_report_only_success`, `script_requires_project_local_artifacts`, `script_requires_runtime_coverage`, `script_requires_numeric_performance`, and `script_checks_no_fallback`.

## Serving-Backed Entry Gate
- Treat self-hosted/local inference services, vLLM, SGLang, Ollama, OpenAI-compatible local APIs, localhost endpoints, `/v1/chat/completions`, and equivalent service/client modes as serving-backed validation surfaces.
- Reject a selected command that is only a client call and depends on an unmanaged, manually pre-started service.
- Accept a selected entry script or documented launcher when it owns server lifecycle, readiness polling, client validation, log capture, and cleanup inside the framework-managed runtime.
- Check long-lived server child processes do not use undrained `subprocess.PIPE`; require `communicate()`, reader threads/tasks, or redirected log files so server and client stdout/stderr are captured without pipe deadlocks.
- Require readiness polling with a finite timeout and useful failure/log evidence.
- Require busy ports to be handled by preflight checks or dynamic free-port fallback, with the final port/base URL propagated to the client.
- Require cleanup of the launcher's own child processes/process groups, not blind killing of unrelated processes.
- Preserve the user constraint boundary: user constraints may express simple intent; this phase audits whether the selected Phase 3 execution contract is complete and runnable.

## Container Path Semantics
- `entry_script_path` and `reports_dir` must be host-visible paths, normally under `{project_dir}`.
- `run_command` may use container-visible paths only because the framework backend executes it inside the target runtime.
- Do not require `run_command` to wrap itself with `docker exec`, `podman exec`, or container lifecycle commands. Those are always wrong here.
- Do not reject a valid container path in `run_command` solely because it is not a host path, as long as it maps to the selected host-visible project file.

## Hard Rules
- Do not execute the target runtime here.
- Do not accept CPU fallback or validation that only imports modules.
- Do not accept report-only success for custom/native op workflows.
- Return exactly one JSON object and no other JSON.

## Output Format
```json
{
  "validation_passed": true,
  "issues": [],
  "fix_plan": "No issues found. Script is headless-compliant and uses the selected interpreter."
}
```
