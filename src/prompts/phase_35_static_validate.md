# Phase 3.5 - Static Compliance Check

You are executing `{phase_name}` for `{project_dir}`.

## Context
This is Phase 3.5 in the CUDA → Ascend NPU migration workflow. Phase 3 has selected an entry script and run command. Your job is to **statically analyze** the selected entry script for patterns that would prevent automated, headless execution in Phase 5.

## Goal
Analyze the `entry_script_path` selected in Phase 3 and determine if it can run non-interactively (no user input, no infinite loops, no GUI prompts).

## Analysis Checklist

Examine the entry script file at `{entry_script_path}` (resolve from Phase 3 output) and check for:

1. **Interactive input calls**: `input()`, `raw_input()`, `getpass()`, `getpass.getpass()`, `code.interact()`, `cmd.Cmd()`, `click.prompt()`, `rich.prompt.*`
2. **Infinite loops without exit**: `while True:` or `while 1:` loops that have no `break`, no signal handler, no epoch/step limit, and no timeout mechanism.
3. **Interactive GUI/display calls**: `cv2.imshow()`, `cv2.waitKey()`, `matplotlib.pyplot.show()`, `Tk().mainloop()`, PyQt/PySide event loops.
4. **Debug/REPL breakpoints**: `pdb.set_trace()`, `breakpoint()`, `IPython.embed()`, `code.interact()`.
5. **Blocking waits**: `threading.Event().wait()`, `queue.get()` without timeout in the main execution path.
6. **Short internal validation timeouts**: generated custom-op validation scripts must not wrap the real project/API/E2E route in a short `subprocess.run(..., timeout=...)` or `communicate(..., timeout=...)`. The framework may monitor long-running phases; a generated-script timeout such as 600 seconds is a blocker for custom-op validation.
7. **Ascend serving wrappers**: for `vllm_serving_validation` and `sglang_serving_validation`, the selected entry must be the generated `validate_vllm_serving.py` or `validate_sglang_serving.py` wrapper. It must configure CANN/Ascend env, add CANN Python paths for `tbe`/`te`, import-probe `torch_npu`, reject CUDA/NVIDIA/NCCL markers, execute the real project launch/demo command, and write `migration_reports/serving/serving_final_gate.json`. Inline shell env prefixes, CUDA/NCCL allocator paths (`pynccl_allocator`, `torch.cuda.memory`), `nvidia-smi`, CPU fallback, and smoke/import-only checks are blockers.

## Custom-Op Contract Gate

Use the existing `previous_outputs` context below to inspect `phase_3_entry_script`:

```json
{previous_outputs}
```

If Phase 3 includes `entry_script_kind: custom_op_full_validation`, validate the selected script against the source-discovery contract embedded in `previous_outputs`, the `migration_reports/` paths in `required_report_paths`, and the `required_checks` in `previous_outputs`. Set `validation_passed=false` for report-only, smoke, MVP, partial, synthetic, or benchmark routes, missing source inventory discovery, missing native symbol/kernel inventory or source evidence, missing out-of-scope groups, missing project-local artifacts, missing project-root artifact existence checks, missing strict Ascend C/CANN OPP producer evidence checks, any non-OPP producer success path, missing project API custom-op invocation, missing per-row `public_api_route_evidence` or `framework_integration_route_evidence`, route evidence that is neither one object nor a non-empty object list, empty route-evidence lists, any invalid list item in route evidence, missing correlation between route evidence and manifest/source-inventory rows, missing native custom-op/OPP same-run execution in route evidence, missing numeric performance, or fallback/zero-call/builtin/stub success. Reject inventories that only list row names/counts, group multiple source-discovered units into a family-only row, omit unit identity or variant/signature, omit kernel launch sites, omit public-entry mapping, omit candidate public/framework integration routes, or fail to prove source-driven fine-grained discovery. The validation script must reject direct-only, builtin-only, fallback, zero-call, report-only, synthetic/mock, benchmark-only, ATen-only, NpuExtension-only, CppExtension-only, Python-shim, baseline-only, and stub route evidence for active custom-op contracts only.

For passing custom-op outputs, include `custom_op_static_required: true`, include the checked `entry_script_path`, and set these booleans to `true`: `custom_op_requirements_checked`, `script_source_driven_inventory`, `script_emits_fine_grained_units`, `script_maps_public_api_to_units`, `script_discovers_full_inventory`, `script_records_native_operator_symbols`, `script_requires_strict_opp_producer_evidence`, `script_rejects_non_opp_producer_success`, `script_runs_project_api_custom_ops`, `script_requires_per_row_route_evidence`, `script_correlates_route_evidence_to_manifest_rows`, `script_rejects_direct_or_builtin_only_routes`, `script_rejects_report_only_success`, `script_requires_project_local_artifacts`, `script_requires_project_root_artifact_existence`, `script_requires_numeric_performance`, and `script_checks_no_fallback`.

If Phase 3 includes expanded variant contract fields, additionally verify that the script discovers the expanded variant inventory, checks every active axis value combination promised by Phase 1, requires one final row per expanded variant identity, rejects collapsed parameterized rows, and requires one performance entry per expanded variant. Do not require these checks when Phase 3 omitted expanded variant contract fields.

For passing expanded-variant custom-op outputs, include `expanded_variant_static_required: true`, `script_discovers_expanded_variant_inventory: true`, `script_checks_variant_axis_coverage: true`, and `script_requires_per_variant_performance: true`.

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
- `entry_script_path`: required for custom-op static validation so deterministic validators can inspect the generated script for invalid short internal validation timeouts.
