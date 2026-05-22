# Phase 6 - Final Report Generation

You are executing `{phase_name}` for `{project_dir}`.
Use the full upstream context from `{previous_outputs}` and write reports into `{report_dir}`.

## Goal
- Produce the final report bundle for the migration run.
- Create all required markdown reports and then return a machine-readable manifest.

## Required Reports
1. `API_KEY_REPORT.md` - inventory whether any API keys, tokens, or credential placeholders were found and how they were handled.
2. `OPENCODE_OPERATIONS_LOG.md` - chronological log of major operations, phase outcomes, retries, and important decisions.
3. `TOOLS_EXECUTION_REPORT.md` - tools used, commands run, notable outputs, and tool-specific constraints.
4. `SUMMARY_REPORT.md` - concise end-to-end summary, final outcome, and a tool usage ratio table.
5. `LOCAL_TOOL_OPTIMIZATION_REPORT.md` - opportunities to replace remote or manual work with deterministic local tooling.

## Hard Rules
- All five reports must be created under `{report_dir}`.
- `SUMMARY_REPORT.md` must include a table that shows tool usage ratios.
- Do not invent credentials or redact non-existent secrets; report only what was actually observed.
- Keep the reports specific to this run and grounded in `{previous_outputs}`.
- For custom-op migrations, summarize compliance using the fine-grained source inventory, migration manifest, final gate, unit identities, variants/signatures, kernel launch sites, public-entry mappings, per-row route evidence type (`public_api_route_evidence` or `framework_integration_route_evidence`) and shape (single object or non-empty object list), strict Ascend C/CANN OPP producer artifacts, and any out-of-scope source groups; do not rely on external requirements docs or coarse row counts as the source of truth.
- Custom-op final success must be grounded in strict OPP evidence: op_host source, op_kernel/AscendC source, CMakeLists.txt/build.sh or equivalent OPP build script, CANN/OPP build-install log, install/provenance evidence, generated header/op_info/kernel_meta/producer/package artifacts, runtime-loaded compiled OPP artifact, adapter/import/link, parity, route evidence through public API or framework integration with same-run positive native custom-op calls, coverage, CPU-baseline vs Ascend OPP/custom-op performance proof, and no-fallback proof. Route evidence may be one object or a non-empty object list; every object must independently prove the same strict route requirements, and empty or partially invalid lists are failed migration evidence, not success. Do not summarize same-NPU/self-baseline placeholder 1.0 speedups as final custom-op speedup. Do not report direct-only, builtin-only, fallback, zero-call, report-only, synthetic/mock, benchmark-only, ATen-only, NpuExtension-only, CppExtension-only, Python-shim, baseline-only, or stub routes as custom-op route evidence. Do not report `torch_npu.utils.cpp_extension.NpuExtension`, `torch.utils.cpp_extension.CppExtension`, ATen-only `npu_ops.cpp`, or libtorch/torch_cpu/torch_npu-only builds as `opp_custom_op_artifact_evidence`; NpuExtension can only be adapter evidence when separate strict OPP producer evidence exists.
- If Phase 5 reports `fail_closed_missing_strict_opp_evidence` or `stagnation_fail_closed_missing_strict_opp_evidence`, report generation itself may be successful but the migration is still failed. In `SUMMARY_REPORT.md`, explicitly separate: report generation succeeded, migration did not pass, the blocker is missing strict Ascend C/CANN OPP producer evidence, and the available `inventory_count`, `manifest_entries`, `closed_pass_entries`, `remaining_entries`, and `full_migration_status` values. Do not convert this fail-closed terminal artifact into migration success.
- For custom-op migrations, `SUMMARY_REPORT.md` must include a final Chinese summary and the exact per-row table below, with one row per in-scope manifest/source-inventory unit:

| row | semantic operator | public entries / aliases | route evidence type | route evidence summary | OPP artifact | adapter callable | coverage key/count | parity | integration/e2e | CPU baseline vs Ascend OPP/custom-op performance | status | next action |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |

- If report generation requires package installs or tooling setup, prefer domestic mirrors such as 阿里云镜像 or 清华镜像.
- After writing the reports, you may reason freely in your response, but end it with a single JSON object containing exactly the required keys for this phase. No other JSON objects should appear.

## Output Format
Return exactly one JSON object with this shape:

```json
{
  "report_paths": [
    "/path/to/reports/API_KEY_REPORT.md",
    "/path/to/reports/OPENCODE_OPERATIONS_LOG.md",
    "/path/to/reports/TOOLS_EXECUTION_REPORT.md",
    "/path/to/reports/SUMMARY_REPORT.md",
    "/path/to/reports/LOCAL_TOOL_OPTIMIZATION_REPORT.md"
  ],
  "migration_summary": {
    "files_migrated": 12,
    "files_skipped": 3
  }
}
```

## Field Semantics
- `report_paths`: absolute paths to the five generated reports.
- `migration_summary`: final migration counters grounded in prior phase outputs.
