# Phase 5 - Validation And Repair Loop Input

You are executing `{phase_name}` for `{project_dir}`.
Use upstream context from `{previous_outputs}` and the current loop count `{iteration_count}`.

## Goal
- Run the selected entry command and determine whether the adapted project now executes successfully.
- When execution fails, produce a compact machine-readable error summary for the next repair step.
- For custom-op runs, treat inventory, manifest, runtime-coverage, performance-report, and final-gate closure as part of the validation surface; success is not valid until the source-discovered inventory has closed every fine-grained operator unit, produced per-row `public_api_route_evidence` or `framework_integration_route_evidence` for real same-run custom-op execution (each field may be a single object or a non-empty object list, and every object must pass the same strict checks), produced complete same-run runtime coverage, produced a complete per-unit acceleration/speedup report, and produced one overall/end-to-end CPU-baseline vs Ascend OPP/custom-op speedup after all discovered custom-op units were replaced and routed through the project/public API. When `expanded_variant_inventory.variant_axes_detected=true`, `source_inventory`, `migration_manifest.required_units`, final-gate `rows`, `runtime_coverage_report.entries`, and `performance_report.entries` must exactly match every expanded variant identity from Phase 1/Phase 3; representative samples, family rows, or axis-only coverage are invalid. Coarse/family-only rows, row-count-only summaries, nested multi-unit symbol/kernel maps, missing unit identity/variant/kernel launch/public entry mapping, missing per-row route evidence, empty route-evidence lists, partially invalid route-evidence lists, direct-only/builtin-only/fallback/zero-call/report-only/synthetic/mock/benchmark-only/ATen-only/NpuExtension-only/CppExtension-only/Python-shim/baseline-only route evidence, missing `migration_reports/runtime_coverage.json`, missing `migration_reports/performance.json`, missing overall replacement speedup evidence, or incomplete speedup coverage must fail closed for active custom-op contracts only.
- For `vllm_serving` and `sglang_serving`, success is not valid until the route-specific serving final gate is current and `FULL_PASS`. The report must prove route/framework match, real project demo/test/API request validation, readiness probe success, NPU execution evidence, expected outputs, no CUDA fallback, no CPU fallback, and fresh required report paths. Import-only and smoke-only serving checks are failures.

## Required Actions
1. Run the Phase 3 command inside the prepared environment from earlier phases.
2. Capture exit status, stdout, and stderr.
3. If execution succeeds, report success only after custom-op final-gate evidence proves every manifest/source-inventory row has valid `public_api_route_evidence` or `framework_integration_route_evidence` correlated to that row, `same_run=true`, custom call count > 0, native custom-op/OPP execution, and public/framework entry invocation. Route evidence may be one object or a non-empty list of objects; if it is a list, every item must independently satisfy the same strict proof requirements and any invalid item must fail the row. Also prove `migration_reports/runtime_coverage.json` / final `runtime_coverage_report` exists and covers every manifest/source-inventory row, prove `migration_reports/performance.json` / final `performance_report` exists, covers every manifest/source-inventory unit with per-unit CPU-baseline vs Ascend OPP/custom-op speedup data, and includes `overall_baseline_seconds` from a real CPU baseline, `overall_custom_seconds` from the Ascend OPP/custom-op route, `overall_speedup_vs_baseline`, `overall_all_units_replaced=true` or equivalent all-units-replaced proof, and project/public API route proof for the full all-custom-op-replaced overall timing; reject same-NPU/self-baseline placeholder 1.0 reports and require speedup_vs_baseline ~= CPU baseline_seconds / Ascend OPP custom_seconds.
4. If execution fails, extract the most actionable errors and classify each one into a small set such as `dependency`, `code`, `operator`, or `unknown`.
5. Keep the error list concise; include only the failures that matter for the next repair iteration.

## Hard Rules
- Use the exact prepared environment whenever possible.
- Do not claim success if the command exits non-zero.
- Do not hide stderr details that are required for repair.
- If a failure is caused by missing packages or package resolution, prefer domestic mirrors such as 阿里云镜像 or 清华镜像 in any suggested remediation path.
- You may reason freely in your response, but end it with a single JSON object containing exactly the required keys for this phase. No other JSON objects should appear.
- `iteration_count` must reflect the current validation attempt, not a guessed future value.

## Output Format
Return exactly one JSON object with this shape:

```json
{
  "success": false,
  "iteration_count": 2,
  "errors": [
    {
      "message": "ModuleNotFoundError: No module named 'torch_npu'",
      "category": "dependency"
    }
  ]
}
```

## Field Semantics
- `success`: whether the entry command completed successfully.
- `iteration_count`: the current validation loop number.
- `errors`: empty list on success, otherwise concise actionable failures.
