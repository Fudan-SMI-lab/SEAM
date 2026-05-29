# Phase 5 - Validation And Repair Loop Input

You are executing `{phase_name}` for `{project_dir}`.
Use upstream context from `{previous_outputs}` and the current loop count `{iteration_count}`.

## Goal
- Run the selected entry command and determine whether the adapted project now executes successfully.
- When execution fails, produce a compact machine-readable error summary for the next repair step.
- For custom-op runs, treat inventory, manifest, performance-report, and final-gate closure as part of the validation surface; success is not valid until the source-discovered inventory has closed every fine-grained operator unit, produced a complete per-unit acceleration/speedup report, and produced one overall/end-to-end speedup after all discovered custom-op units were replaced and routed through the project/public API. Coarse/family-only rows, row-count-only summaries, nested multi-unit symbol/kernel maps, missing unit identity/variant/kernel launch/public entry mapping, missing `migration_reports/performance.json`, missing overall replacement speedup evidence, or incomplete speedup coverage must fail closed.
- For `vllm_serving` and `sglang_serving`, success is not valid until the route-specific serving final gate is present and `FULL_PASS`. The report must prove route/framework match, real project demo/test/API request validation, readiness probe success, platform-policy accelerator execution evidence, expected outputs, no CUDA/NVIDIA/NCCL runtime markers unless explicitly allowed by the selected backend, no CUDA fallback, no CPU fallback, no import-only/smoke-only validation, and fresh required serving reports.

## Required Actions
1. Run the Phase 3 command inside the prepared environment from earlier phases.
2. Capture exit status, stdout, and stderr.
3. If execution succeeds, report success only after custom-op final-gate evidence proves `migration_reports/performance.json` / final `performance_report` exists, covers every manifest/source-inventory unit with per-unit speedup data, and includes `overall_baseline_seconds`, `overall_custom_seconds`, `overall_speedup_vs_baseline`, `overall_all_units_replaced=true` or equivalent all-units-replaced proof, and project/public API route proof for the full all-custom-op-replaced overall timing.
4. For vLLM/SGLang serving routes, report success only after `serving_final_gate.json` proves real launch/readiness/request execution and route-specific platform runtime evidence.
5. If execution fails, extract the most actionable errors and classify each one into a small set such as `dependency`, `code`, `operator`, or `unknown`.
6. Keep the error list concise; include only the failures that matter for the next repair iteration.

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
