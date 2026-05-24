# Phase 3.5 - Static Compliance Check (MUSA/MUXI, Base-Env-Aware)

You are executing `{phase_name}` for `{project_dir}`.

{execution_environment_context}

## Goal
Statically validate the Phase 3 entry script and contract before Phase 5 executes it.

## Required Actions
1. Read `{entry_script_path}` and verify it exists on the host-visible filesystem.
2. Check for blocking input, REPL/debugger stops, GUI/display calls, and infinite loops without bounds.
3. Confirm `run_command` does not contain container lifecycle commands.
4. If `entry_script_kind` is `custom_op_full_validation`, check that the contract includes reports_dir, required_report_paths, required_checks, operator discovery sources, source inventory obligations, runtime coverage, performance evidence, and no-fallback evidence.
5. For custom/native ops, reject import-only or report-only wrappers that do not compile/load/run native MUSA evidence.

## Output Format
```json
{
  "validation_passed": true,
  "issues": [],
  "fix_plan": "No issues found. Script is headless-compliant."
}
```
