# Phase 6 - Final Report Generation (MUSA/MUXI)

You are executing `{phase_name}` for `{project_dir}`. Use prior phase context from `{previous_outputs}` and write reports into `{report_dir}`.

## Goal
Create the final report bundle for the MUSA/MUXI migration run.

## Required Reports
1. `API_KEY_REPORT.md`
2. `OPENCODE_OPERATIONS_LOG.md`
3. `TOOLS_EXECUTION_REPORT.md`
4. `SUMMARY_REPORT.md`
5. `LOCAL_TOOL_OPTIMIZATION_REPORT.md`

## Hard Rules
- Ground every claim in prior phase outputs or observed artifacts.
- For custom-op runs, summarize inventory, manifest, parity, runtime coverage, performance, build evidence, final gate, no-fallback flags, and unresolved rows.
- End with exactly one JSON object.

## Output Format
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
    "files_migrated": 0,
    "files_skipped": 0
  }
}
```
