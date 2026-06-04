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
- For custom-op migrations, summarize compliance using the fine-grained source inventory, migration manifest, final gate, unit identities, variants/signatures, kernel launch sites, public-entry mappings, and any out-of-scope source groups; do not rely on external requirements docs or coarse row counts as the source of truth.
- For vLLM/SGLang serving migrations, summarize compliance from the current `serving_final_gate.json`: route/framework/backend, launch command, readiness probe, request validation, project demo/test/API files, expected outputs, accelerator runtime evidence, no-fallback flags, and final `full_migration_status`. Do not convert import-only, smoke-only, stale, or fallback evidence into migration success.
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
