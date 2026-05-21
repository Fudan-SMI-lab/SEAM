# Phase 3.5 - Static Compliance Check

You are executing `{phase_name}` for `{project_dir}`.

## Context
This is Phase 3.5 in the CUDA → Ascend NPU migration workflow. Phase 3 has selected an entry script and run command. Your job is to **statically analyze** the selected entry script for patterns that would prevent automated, headless execution in Phase 5.

## Goal
Analyze the `entry_script_path` selected in Phase 3 and determine if it can run non-interactively (no user input, no infinite loops, no GUI prompts).

## Analysis Checklist

Examine the entry script file at `{entry_script_path}`. This path is a **host-visible absolute path** provided by the Phase 3 output; it is readable via OpenCode file tools (e.g. `read`) and accessible inside the execution container. Check for:

1. **Interactive input calls**: `input()`, `raw_input()`, `getpass()`, `getpass.getpass()`, `code.interact()`, `cmd.Cmd()`, `click.prompt()`, `rich.prompt.*`
2. **Infinite loops without exit**: `while True:` or `while 1:` loops that have no `break`, no signal handler, no epoch/step limit, and no timeout mechanism.
3. **Interactive GUI/display calls**: `cv2.imshow()`, `cv2.waitKey()`, `matplotlib.pyplot.show()`, `Tk().mainloop()`, PyQt/PySide event loops.
4. **Debug/REPL breakpoints**: `pdb.set_trace()`, `breakpoint()`, `IPython.embed()`, `code.interact()`.
5. **Blocking waits**: `threading.Event().wait()`, `queue.get()` without timeout in the main execution path.

## Custom-Op Contract Gate

The `previous_outputs` block below contains **only** the `phase_3_entry_script` output (entry script path, run command, and custom-op contract fields). It does not include outputs from earlier phases.

```json
{previous_outputs}
```

If Phase 3 includes `entry_script_kind: custom_op_full_validation`, validate the selected script against the source-discovery contract embedded in `previous_outputs`, the `migration_reports/` paths in `required_report_paths`, and the `required_checks` in `previous_outputs`. Set `validation_passed=false` for report-only, smoke, MVP, partial, synthetic, or benchmark routes, missing source inventory discovery, missing native symbol/kernel inventory or source evidence, missing out-of-scope groups, missing project-local artifacts, missing project API custom-op invocation, missing numeric performance, or fallback/zero-call/builtin/stub success. Reject inventories that only list row names/counts, group multiple source-discovered units into a family-only row, omit unit identity or variant/signature, omit kernel launch sites, omit public-entry mapping, or fail to prove source-driven fine-grained discovery.

For passing custom-op outputs, include `custom_op_static_required: true` plus these booleans set to `true`: `custom_op_requirements_checked`, `script_source_driven_inventory`, `script_emits_fine_grained_units`, `script_maps_public_api_to_units`, `script_discovers_full_inventory`, `script_records_native_operator_symbols`, `script_runs_project_api_custom_ops`, `script_rejects_report_only_success`, `script_requires_project_local_artifacts`, `script_requires_numeric_performance`, and `script_checks_no_fallback`.

## Important Notes

- Training loops with epoch limits (e.g., `for epoch in range(epochs):`) are **acceptable** — they will eventually exit.
- `if __name__ == "__main__":` guards are expected and good.
- The analysis should be **conservative but practical**: flag genuine blockers, not theoretical edge cases.
- If the script imports a module that does interactive things, check if the import path is actually executed.
- You may reason freely, but end with one JSON object using exactly the fields below.

## Output Format
Return exactly one JSON object with this shape:

```json
{
  "validation_passed": true,
  "issues": [],
  "fix_plan": "No issues found. Script is headless-compliant."
}
```

Or if issues are found:

```json
{
  "validation_passed": false,
  "issues": [
    "Line 42: input() call detected — will block waiting for user input",
    "Line 87: while True: loop with no break or timeout — will run indefinitely"
  ],
  "fix_plan": "Replace input() with argparse defaults; add max_steps limit to training loop or add signal handler for graceful exit."
}
```

## Field Semantics
- `validation_passed`: `true` if the script can run fully non-interactively; `false` if any blocking patterns are found.
- `issues`: List of human-readable descriptions of each issue found, including file path, line number, and the problematic pattern. Empty list when validation passes.
- `fix_plan`: Actionable plan to resolve all issues. If `validation_passed` is true, this should confirm compliance. If false, describe specific code changes, wrapper scripts, or command-line flags needed.
