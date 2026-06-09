# Phase 3.5 - Static Compliance Check

You are executing `{phase_name}` for `{project_dir}`.

## Context
This is Phase 3.5 in the CUDA source-project migration workflow for the platform selected by platform policy. Phase 3 has selected an entry script and run command. Your job is to **statically analyze** the selected entry script for patterns that would prevent automated, headless execution in the target runtime.

## Goal
- Analyze the `entry_script` for headless execution compliance (interactive calls, infinite loops, GUI, debug traps, blocking waits).
- When custom operators are present, also generate the full custom-op surface: enumerate fine-grained operator units, native symbols, kernel launch sites, public entry mappings, source evidence, and expanded variant inventory from the variant axes detected earlier.

## Analysis Checklist

Examine the entry script file at `{entry_script_path}`. This path is a **host-visible absolute path** provided by the Phase 3 output; it is readable via file tools (e.g. `read`) and accessible inside the execution container. Check for:

1. **Interactive input calls**: `input()`, `raw_input()`, `getpass()`, `getpass.getpass()`, `code.interact()`, `cmd.Cmd()`, `click.prompt()`, `rich.prompt.*`
2. **Infinite loops without exit**: `while True:` or `while 1:` loops that have no `break`, no signal handler, no epoch/step limit, and no timeout mechanism.
3. **Interactive GUI/display calls**: `cv2.imshow()`, `cv2.waitKey()`, `matplotlib.pyplot.show()`, `Tk().mainloop()`, PyQt/PySide event loops.
4. **Debug/REPL breakpoints**: `pdb.set_trace()`, `breakpoint()`, `IPython.embed()`, `code.interact()`.
5. **Blocking waits**: `threading.Event().wait()`, `queue.get()` without timeout in the main execution path.
6. **Serving validation wrappers**: for `vllm_serving_validation` and `sglang_serving_validation`, the selected entry must be the generated `validate_vllm_serving.py` or `validate_sglang_serving.py` wrapper. It must configure platform-policy runtime env, execute the real project launch/demo/API request path, write `serving_final_gate.json`, and reject inline shell env prefixes, CUDA/NVIDIA/NCCL marker leakage, CPU fallback, and smoke/import-only checks unless explicitly permitted by the selected backend policy.

## Custom-Op Surface Generation

When `previous_outputs` contains a `custom_op_surface` from earlier analysis with `custom_op_detected: true`, you must also produce a full custom-op surface inventory. This replaces the detection-only summary from the initial project scan with the complete fine-grained enumeration needed for the remainder of the migration pipeline.

**Required Actions:**
1. Read the entry script to understand which operator families and validation paths it exercises.
2. Search project source files (`.cpp`, `.cu`, `.py`, `.h`, bindings, setup scripts, tests) for all operator implementation sites, kernel functions, native symbol exports, and public API entry points.
3. Enumerate `fine_grained_operator_units` — one unit per concrete operator signature discovered from source. Do not collapse to family-level rows.
4. For each unit, record `native_operator_symbols` (the C/C++/CUDA symbol names or exported function names), `kernel_launch_sites` (source locations where kernels invoke), and `source_evidence` (file:line references proving the unit exists).
5. Map `public_entry_mapping` — the Python/API/autograd entry points that route to each custom-op unit.
6. If `variant_axes_detected: true` and `variant_axes` are present in the earlier analysis, enumerate the full `expanded_operator_variants`: one entry per concrete axis-value combination per base unit. Do not use collapsed or representative rows. Each variant entry must include `unit_identity`, `base_unit_identity`, `axis_values`, `source_evidence`, and `candidate_public_api_routes` or `candidate_framework_integration_routes`.
7. Record `negative_evidence` — what was searched and found absent.
8. Record `dynamic_loading_checks` and `build_load_checks` — runtime probes and build/install verification.
9. Track `unresolved_source_groups` — operator groups where source evidence is incomplete.
10. Track `out_of_scope_source_groups` — excluded families with reasons.
11. Set `discovery_complete: true` only when every discovered unit and expanded variant has source evidence and no unresolved groups remain.

**Output Shape for custom_op_surface:**
```json
{
  "custom_op_surface": {
    "custom_op_detected": true,
    "discovery_complete": true,
    "discovery_sources_checked": ["source", "bindings", "wrappers", "autograd", "aliases", "launch", "setup", "tests"],
    "searched_source_roots": ["src", "csrc", "tests"],
    "searched_source_paths": ["csrc/custom_alpha.cpp"],
    "operator_families": ["family_a", "family_b"],
    "fine_grained_operator_units": ["family_a:signature_x", "family_a:signature_y", "family_b:mode_z"],
    "discovered_operator_names": ["family_a_signature_x", "family_a_signature_y", "family_b_mode_z"],
    "native_operator_symbols": ["custom_alpha_kernel_x", "custom_alpha_kernel_y"],
    "kernel_launch_sites": ["csrc/custom_alpha.cu:42", "csrc/custom_beta.cu:87"],
    "source_evidence": ["csrc/custom_alpha.cpp:signature_x", "csrc/custom_beta.cpp:mode_z"],
    "negative_evidence": ["grep under src/ found no additional operator families"],
    "dynamic_loading_checks": ["import torch.ops.custom_family_a succeeded"],
    "build_load_checks": ["python setup.py build_ext --inplace completed"],
    "unresolved_source_groups": [],
    "out_of_scope_source_groups": [],
    "fine_grained_operator_unit_evidence": [
      {"unit_identity": "family_a:signature_x", "source_evidence": ["csrc/custom_alpha.cpp:signature_x"], "candidate_public_api_routes": ["model.forward"]},
      {"unit_identity": "family_a:signature_y", "source_evidence": ["csrc/custom_alpha.cpp:signature_y"]},
      {"unit_identity": "family_b:mode_z", "source_evidence": ["csrc/custom_beta.cpp:mode_z"]}
    ],
    "variant_axes_detected": true,
    "variant_axes": {"ndim": ["1d", "2d", "3d"], "dtype": ["float", "double"]},
    "expanded_operator_variants": [
      {"unit_identity": "family_a:signature_x:ndim_1d:dtype_float", "base_unit_identity": "family_a:signature_x", "axis_values": {"ndim": "1d", "dtype": "float"}, "source_evidence": ["csrc/custom_alpha.cpp:signature_x"], "candidate_public_api_routes": ["model.forward"]},
      {"unit_identity": "family_a:signature_x:ndim_1d:dtype_double", "base_unit_identity": "family_a:signature_x", "axis_values": {"ndim": "1d", "dtype": "double"}, "source_evidence": ["csrc/custom_alpha.cpp:signature_x"], "candidate_public_api_routes": ["model.forward"]},
      {"unit_identity": "family_a:signature_x:ndim_2d:dtype_float", "base_unit_identity": "family_a:signature_x", "axis_values": {"ndim": "2d", "dtype": "float"}, "source_evidence": ["csrc/custom_alpha.cpp:signature_x"], "candidate_public_api_routes": ["model.forward"]},
      "… one entry for every remaining axis-value combination across all base units; see rules below for the required Cartesian product …"
    ],
    "expanded_operator_instances_count": 12
  }
}
```

**Rules for expanded variants — THIS IS A HARD CONTRACT. FAILURE TO COMPLY PRODUCES A BLOCKING VALIDATION DEFECT:**
- Every axis declared in `variant_axes` must be represented in every expanded variant entry's `axis_values`.
- You MUST enumerate the full Cartesian product: for each base unit in `fine_grained_operator_unit_evidence`, produce one variant entry per possible combination of axis values. All base units × all axis-value combinations. No collapsed rows, no representative samples, no "typical" entries.
- Count check: `expanded_operator_instances_count` MUST equal `len(expanded_operator_variants)`. The validator will compare `len(expanded_operator_variants)` against `len(fine_grained_operator_unit_evidence) × product of axis cardinalities`. Discrepancy is a blocking defect.
- Do not write `expanded_operator_instances_count` as the target count while the actual `expanded_operator_variants` array is empty or contains only 1–2 entries. The array itself is the authoritative data; the count is a consistency check.
- Each variant entry must have `source_evidence` and at least one of `candidate_public_api_routes` or `candidate_framework_integration_routes`. Inherit `source_evidence` from the base unit when the variant shares the same source file.
- {variant_placeholder} ← This token will be replaced at runtime. Do NOT guess or fabricate variant entries from this placeholder; wait for the actual variant axis data from the framework.

## Custom-Op Contract Gate

The `previous_outputs` block below contains the `phase_3_entry_script` output (entry script path, run command, and custom-op contract fields) along with the project analysis output for custom-op surface detection.

```json
{previous_outputs}
```

If Phase 3 includes `entry_script_kind: custom_op_full_validation`, validate the selected script against the source-discovery contract embedded in `previous_outputs`, the `migration_reports/` paths in `required_report_paths`, and the `required_checks` in `previous_outputs`. Set `validation_passed=false` for report-only, smoke, MVP, partial, synthetic, or benchmark routes, missing source inventory discovery, missing native symbol/kernel inventory or source evidence, missing out-of-scope groups, missing project-local artifacts, missing project API custom-op invocation, missing numeric performance, or fallback/zero-call/builtin/stub success. Reject inventories that only list row names/counts, group multiple source-discovered units into a family-only row, omit unit identity or variant/signature, omit kernel launch sites, omit public-entry mapping, or fail to prove source-driven fine-grained discovery.

For passing custom-op outputs, include `custom_op_static_required: true` plus these booleans set to `true`: `custom_op_requirements_checked`, `script_source_driven_inventory`, `script_emits_fine_grained_units`, `script_maps_public_api_to_units`, `script_discovers_full_inventory`, `script_records_native_operator_symbols`, `script_runs_project_api_custom_ops`, `script_rejects_report_only_success`, `script_requires_project_local_artifacts`, `script_requires_numeric_performance`, and `script_checks_no_fallback`. If the Phase 3 contract includes `expanded_variant_inventory`, `variant_axis_coverage`, `per_variant_performance_report`, or otherwise declares expanded variants, also include `expanded_variant_static_required: true`, `script_discovers_expanded_variant_inventory: true`, `script_checks_variant_axis_coverage: true`, and `script_requires_per_variant_performance: true`.

## Large Operator Inventories

When Phase 3 contains an `expanded_variant_inventory` with many operator variants, analyze all variants — validate as many as possible within the context window. Do NOT batch-skip variants or assume remaining variants follow the same pattern. If the total variant count is too large to fit in a single analysis pass, validate as many as you can and note which ones were analyzed. Any variants not covered by this static pass will be caught by Phase 5's `custom_op_final_gate` validation at runtime.

## Important Notes

- Training loops with epoch limits (e.g., `for epoch in range(epochs):`) are **acceptable** — they will eventually exit.
- `if __name__ == "__main__":` guards are expected and good.
- The analysis should be **conservative but practical**: flag genuine blockers, not theoretical edge cases.
- If the script imports a module that does interactive things, check if the import path is actually executed.
- You may reason freely, but end with one JSON object using exactly the fields below.

## Output Format
Return exactly one JSON object. When custom operators are detected and `custom_op_surface` is generated, include it alongside the validation fields:

```json
{
  "validation_passed": true,
  "issues": [],
  "fix_plan": "No issues found. Script is headless-compliant.",
  "custom_op_surface": {
    "custom_op_detected": true,
    "discovery_complete": true,
    "discovery_sources_checked": ["source", "bindings", "wrappers", "autograd", "aliases", "launch", "setup", "tests"],
    "searched_source_roots": ["src"],
    "searched_source_paths": ["csrc/custom_alpha.cpp"],
    "operator_families": ["custom_family"],
    "fine_grained_operator_units": ["custom_family:signature_a"],
    "discovered_operator_names": ["custom_family_signature_a"],
    "native_operator_symbols": ["custom_kernel"],
    "kernel_launch_sites": ["csrc/custom_alpha.cu:42"],
    "source_evidence": ["csrc/custom_alpha.cpp:signature_a"],
    "negative_evidence": ["no additional operator families found"],
    "dynamic_loading_checks": ["import torch.ops.custom_family succeeded"],
    "build_load_checks": ["build_ext --inplace completed"],
    "unresolved_source_groups": [],
    "out_of_scope_source_groups": [],
    "fine_grained_operator_unit_evidence": [
      {"unit_identity": "custom_family:signature_a", "source_evidence": ["csrc/custom_alpha.cpp:signature_a"], "candidate_public_api_routes": ["model.forward"]}
    ],
    "variant_axes_detected": true,
    "variant_axes": {"ndim": ["2d", "3d"]},
    "expanded_operator_variants": [
      {"unit_identity": "custom_family:signature_a:ndim_2d", "base_unit_identity": "custom_family:signature_a", "axis_values": {"ndim": "2d"}, "source_evidence": ["csrc/custom_alpha.cpp:signature_a"], "candidate_public_api_routes": ["model.forward"]}
    ],
    "expanded_operator_instances_count": 2
  }
}
```

For a passing custom-op script with expanded variants, return every enforced boolean explicitly:

```json
{
  "validation_passed": true,
  "issues": [],
  "fix_plan": "No issues found. Script is headless-compliant and satisfies the source-driven custom-op and expanded-variant static gates.",
  "entry_script_kind": "custom_op_full_validation",
  "custom_op_static_required": true,
  "custom_op_requirements_checked": true,
  "script_source_driven_inventory": true,
  "script_emits_fine_grained_units": true,
  "script_maps_public_api_to_units": true,
  "script_discovers_full_inventory": true,
  "script_records_native_operator_symbols": true,
  "script_runs_project_api_custom_ops": true,
  "script_rejects_report_only_success": true,
  "script_requires_project_local_artifacts": true,
  "script_requires_numeric_performance": true,
  "script_checks_no_fallback": true,
  "expanded_variant_static_required": true,
  "script_discovers_expanded_variant_inventory": true,
  "script_checks_variant_axis_coverage": true,
  "script_requires_per_variant_performance": true
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
- `custom_op_surface`: optional, present only when custom operators are detected and the full surface is generated. Contains the complete fine-grained operator inventory — operator families, fine-grained units, discovered names, native symbols, kernel launch sites, source evidence, negative evidence, dynamic/build load checks, unresolved/out-of-scope groups, per-unit evidence, variant axes and values, expanded variant inventory, and instance count. This is the canonical custom-op surface used for contract enforcement, manifest matching, and final gate validation.
- Custom-op boolean fields: required and `true` whenever `custom_op_static_required` is true, `entry_script_kind` is `custom_op_full_validation`, or any custom-op static boolean appears. Do not omit them from passing custom-op responses.
- Expanded-variant boolean fields: required and `true` whenever expanded variants are present. Do not omit them from passing expanded-variant custom-op responses.
