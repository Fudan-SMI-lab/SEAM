1. This is a report schema/aggregation fix. The custom-op final gate report structure fails schema validation (e.g., mismatched row counts, missing sections, incorrect aggregation fields, malformed JSON).
2. **CRITICAL: Fix the entry script or report aggregation logic that generates the report.** Do NOT directly edit `custom_op_final_gate.json` or any report file.
3. Read {runtime_error_artifact_path} and {runtime_card_artifact_path}; identify the violated report schema constraints.
4. Locate and fix the report-generation code in {project_dir} (typically inside {entry_script} or imported modules).
5. Ensure the report satisfies all structural constraints: matching counts, non-empty rows, source_inventory metadata, performance_report completeness.
6. After fixing the report-generation code, re-run {entry_script} to regenerate the report, then **validate with the command below**. Copy its exact output — do NOT guess, simulate, or paraphrase validator results. Run it repeatedly until ALL errors are resolved:

```
{final_gate_validator_command}
```

7. Preserve existing evidence-level content (rows, opp_custom_op_artifact_evidence, etc.) — do NOT fabricate or modify evidence fields. If evidence-level errors remain after schema/aggregation repair, report them in your summary as operator blockers requiring `operator_fixer`.
8. Treat the validator command as read-only. Do NOT modify framework, validator, prompt, or workflow files.

## Validator Contract
{final_gate_validator_contract_summary}
