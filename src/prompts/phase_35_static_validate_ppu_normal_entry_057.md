# Phase 3.5 - Static Compliance Check (PPU, Normal Entry 057 Experiment)

You are executing `{phase_name}` for `{project_dir}`.

{execution_environment_context}

## Context
This is Phase 3.5 in the CUDA -> PPU migration workflow for a NORMAL APPLICATION DEMO. Phase 3 has selected an entry script and run command. Your job is to **statically analyze** the selected entry script for patterns that would prevent automated, headless execution in the target runtime.

This workflow uses the normal-entry route. Custom-op contract validation is disabled by route policy — the framework omits custom-op contract fields and the target runtime custom_op_final_gate auto-skips. Validate ONLY headless execution compliance and ordinary entry-script requirements.

## Goal
Analyze the `entry_script_path` selected in Phase 3 and determine if it can run non-interactively (no user input, no infinite loops, no GUI prompts) in the target execution backend.

## Analysis Checklist

Examine the entry script file at `{entry_script_path}`. This path is an absolute path provided by the Phase 3 output; it is readable via file tools (e.g. `read`) and accessible to the target execution backend after any path mapping. Check for:

1. **Interactive input calls**: `input()`, `raw_input()`, `getpass()`, `getpass.getpass()`, `code.interact()`, `cmd.Cmd()`, `click.prompt()`, `rich.prompt.*`
2. **Infinite loops without exit**: `while True:` or `while 1:` loops that have no `break`, no signal handler, no epoch/step limit, and no timeout mechanism.
3. **Interactive GUI/display calls**: `cv2.imshow()`, `cv2.waitKey()`, `matplotlib.pyplot.show()`, `Tk().mainloop()`, PyQt/PySide event loops.
4. **Debug/REPL breakpoints**: `pdb.set_trace()`, `breakpoint()`, `IPython.embed()`, `code.interact()`.
5. **Blocking waits**: `threading.Event().wait()`, `queue.get()` without timeout in the main execution path.

6. **Data file integrity check (CRITICAL for container execution)**: Check that `.bin` data files needed by the entry script are REAL FILES, not symlinks. The entry script uses `torch.from_file('marmousi_vp.bin', ...)` which reads from the current working directory (`/workspace` inside the container). Use `ls -la` or `os.path.islink()` to inspect:
   - `{project_dir}/marmousi_vp.bin`
   - `{project_dir}/marmousi_data.bin`
   If either file is a symlink, flag it as a **headless execution blocker** because the symlink target is a host-absolute path that does not exist inside the container. The file must be an actual binary file with non-zero size.

7. **CPU fallback patterns (FORBIDDEN)**: Search the entry script for patterns that would force CPU execution or bypass CUDA:
   - `CUDA_VISIBLE_DEVICES=''` or `CUDA_VISIBLE_DEVICES=""` -- forces CPU mode
   - `os.environ['CUDA_VISIBLE_DEVICES']` set to empty
   - `device = 'cpu'` or `device = "cpu"` -- explicit CPU device
   - `torch.device('cpu')` -- explicit CPU device creation
   - `.to('cpu')` or `.cpu()` calls that would override CUDA placement
   If any CPU-forcing pattern is found, flag it as a **validation failure**. This experiment MUST use PPU CUDA-compatible hardware.

## Known Issue 1: plt.show() in 057_example_fwi.py

The source script `057_example_fwi.py` contains `plt.show()` at approximately line 141. This is a **headless execution blocker** -- it opens an interactive GUI window that will hang in a container environment.

**Expected resolution**: If Phase 3 has not already patched the script, flag this issue and recommend:
- Wrap with `matplotlib.use('Agg')` before any matplotlib import OR
- Comment out `plt.show()` and add `plt.savefig()` for the loss plot

## Known Issue 2: Symlinked .bin Data Files (Container Execution Blocker)

The E2E harness creates host-absolute symlinks for `.bin` files: `marmousi_vp.bin -> /home/zihang/.../marmousi_vp.bin`. Inside the container, the host-absolute path does not exist, so these files appear as broken symlinks.

**Check procedure**:
- Run `ls -la {project_dir}/marmousi_vp.bin` -- if output shows `-> /home/...`, the file is a symlink and will be broken in the container
- Run `python3 -c "import os; print(os.path.islink('{project_dir}/marmousi_vp.bin'))"` -- `True` means still a symlink

**Expected fix** (should have been done by Phase 3 already): Copy the real file content over the symlink:
```bash
cp --remove-destination /path/to/deepwave_upstream_fwi_original/marmousi_vp.bin {project_dir}/marmousi_vp.bin
```

If the files are still symlinks, flag this issue and return `validation_passed: false`. This is a container execution blocker.

Training loops in the script use `for epoch in range(n_epochs)` which is bounded and acceptable.

## Important Notes

- Training loops with epoch limits (e.g., `for epoch in range(epochs):`) are **acceptable** -- they will eventually exit.
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
    "Line 141: plt.show() call detected -- will block waiting for GUI display in headless container",
    "marmousi_vp.bin is a symlink (host-absolute target) -- file will be unavailable inside container",
    "Line 87: while True: loop with no break or timeout -- will run indefinitely"
  ],
  "fix_plan": "Set matplotlib backend to Agg before import; or comment out plt.show() and replace with plt.savefig(). Copy real marmousi_vp.bin content over the symlink. Add max_steps limit to training loop."
}
```

## Field Semantics
- `validation_passed`: `true` if the script can run fully non-interactively; `false` if any blocking patterns are found.
- `issues`: List of human-readable descriptions of each issue found. Reference exact line numbers.
- `fix_plan`: Actionable plan to resolve all issues. Do NOT suggest creating a new entry script unless the existing one is fundamentally unfixable.
- Do not return Phase 3 entry-script fields from this phase: no `entry_script_path`, no `run_command`, and no `runtime_entry_script_revision_allowed` at the top level. If a different entry script is needed, set `validation_passed=false`, describe the replacement in `issues`, and put the proposed Phase 3 contract in `fix_plan` text.

## Must NOT Do
- Do NOT check for custom-op contract compliance — this route omits custom-op contract fields by policy.
- Do NOT validate `required_report_paths`, `operator_inventory_schema`, or any custom-op contract fields. The normal-entry route does not produce these fields.
- If the Phase 3 output does not contain `entry_script_kind`, that is expected and correct for this route. Do not flag it as missing.
