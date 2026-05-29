# Phase 3.5 - Static Compliance Check (PPU, Base-Env-Aware)

You are executing `{phase_name}` for `{project_dir}`.

{execution_environment_context}

## Context
This is Phase 3.5 in the CUDA -> PPU migration workflow. Phase 3 has selected an entry script and run command. Your job is to **statically analyze** the selected entry script for patterns that would prevent automated, headless execution in the target runtime.

## Goal
Analyze the `entry_script_path` selected in Phase 3 and determine if it can run non-interactively (no user input, no infinite loops, no GUI prompts) in the target execution backend.

## Analysis Checklist

Examine the entry script file at `{entry_script_path}`. This path is an absolute path provided by the Phase 3 output; it is readable via file tools (e.g. `read`) and accessible to the target execution backend after any path mapping. Check for:

1. **Interactive input calls**: `input()`, `raw_input()`, `getpass()`, `getpass.getpass()`, `code.interact()`, `cmd.Cmd()`, `click.prompt()`, `rich.prompt.*`
2. **Infinite loops without exit**: `while True:` or `while 1:` loops that have no `break`, no signal handler, no epoch/step limit, and no timeout mechanism.
3. **Interactive GUI/display calls**: `cv2.imshow()`, `cv2.waitKey()`, `matplotlib.pyplot.show()`, `Tk().mainloop()`, PyQt/PySide event loops.
4. **Debug/REPL breakpoints**: `pdb.set_trace()`, `breakpoint()`, `IPython.embed()`, `code.interact()`.
5. **Blocking waits**: `threading.Event().wait()`, `queue.get()` without timeout in the main execution path.
6. **Serving validation wrappers**: for `vllm_serving_validation` and `sglang_serving_validation`, the selected entry must be the generated `validate_vllm_serving.py` or `validate_sglang_serving.py` wrapper. It must configure PPU/platform runtime env, execute the real project launch/demo/API request path, write `serving_final_gate.json`, and reject inline shell env prefixes, CPU fallback, and smoke/import-only checks.

## Custom-Op Contract Gate

The `previous_outputs` block below contains **only** the `phase_3_entry_script` output (entry script path, run command, and custom-op contract fields). It does not include outputs from earlier phases.

```json
{previous_outputs}
```

If Phase 3 includes `entry_script_kind: custom_op_full_validation`, validate the selected script against the source-discovery contract embedded in `previous_outputs`.

## Important Notes

- Training loops with epoch limits (e.g., `for epoch in range(epochs):`) are **acceptable** — they will eventually exit.
- `if __name__ == "__main__":` guards are expected and good.
- The analysis should be **conservative but practical**: flag genuine blockers, not theoretical edge cases.
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
- `issues`: List of human-readable descriptions of each issue found.
- `fix_plan`: Actionable plan to resolve all issues.
