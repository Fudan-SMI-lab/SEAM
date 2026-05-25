# Phase 6 - Final Report Generation (MUXI Accelerator Family)

You are executing `{phase_name}` for `{project_dir}`. Use prior phase context from `{previous_outputs}` and write concise reports into `{report_dir}`.

{execution_environment_context}

## Goal
Create a short final report bundle for the MUXI-family migration run. Do not regenerate full phase reasoning.

## Required Reports
1. `SUMMARY_REPORT.md`
2. `TOOLS_EXECUTION_REPORT.md`
3. `OPENCODE_OPERATIONS_LOG.md`

## Report Content Limits
- Summarize key evidence paths only.
- Mention observed vendor stack and API mode when available.
- Mention Phase 4 strategy and whether it modified files.
- Mention Phase 5 final status and accelerator evidence.
- For custom-op runs, summarize final-gate pass/fail, artifact directory, runtime coverage, performance evidence, and unresolved rows in a compact table.
- If partial or failed, report the failed phase and actionable root cause.

## Hard Rules
- Ground every claim in prior phase outputs or observed artifacts.
- Do not include secrets or API keys. If secret-bearing config was encountered, mention only that sensitive config exists and was not printed.
- Keep each report concise; avoid long copied logs.
- End with exactly one JSON object and no other JSON.

## Output Format
```json
{
  "report_paths": [
    "/path/to/reports/SUMMARY_REPORT.md",
    "/path/to/reports/TOOLS_EXECUTION_REPORT.md",
    "/path/to/reports/OPENCODE_OPERATIONS_LOG.md"
  ],
  "migration_summary": {
    "overall_status": "pass",
    "files_migrated": 0,
    "files_skipped": 0,
    "phase4_strategy": "rule_strategies/report_only.yaml",
    "phase5_status": "success"
  }
}
```
