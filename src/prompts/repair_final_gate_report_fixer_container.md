1. This is a report schema/aggregation fix. The custom-op final gate report structure fails schema validation (e.g., mismatched row counts, missing sections, incorrect aggregation fields, malformed JSON).
2. **CRITICAL: Fix the entry script or report aggregation logic that generates the report.** Do NOT directly edit `custom_op_final_gate.json` or any report file.
3. Read {runtime_error_artifact_path} and {runtime_card_artifact_path}; identify the violated report schema constraints.
4. Locate and fix the report-generation code in {project_dir} (typically inside {entry_script} or imported modules).
5. Ensure the report satisfies all structural constraints: matching counts, non-empty rows, source_inventory metadata, performance_report completeness.
6. After fixing the report-generation code, re-run the entry script via the container backend to regenerate the report, then **validate with the command below** (run from the host where the framework is available). Copy its exact output — do NOT guess, simulate, or paraphrase validator results. Run it repeatedly until ALL errors are resolved:

```
{final_gate_validator_command}
```

7. Preserve existing evidence-level content (rows, opp_custom_op_artifact_evidence, etc.) — do NOT fabricate or modify evidence fields. If evidence-level errors remain after schema/aggregation repair, report them in your summary as operator blockers requiring `operator_fixer`.
8. Treat the validator command as read-only. Do NOT modify framework, validator, prompt, or workflow files.

## Validator Contract
{final_gate_validator_contract_summary}

## Container Execution Context

This workflow uses a container execution backend for Phase 5 validation.

- **Execution backend mode**: `{execution_backend_mode}`
- **Actual execution command**: `{actual_execution_command}`
- **Container name or ID**: `{container_name_or_id}`
- **Container workdir**: `{container_workdir}`
- **Host project directory**: `{host_project_dir}`
- **Container project directory**: `{container_project_dir}`

当你在容器工作流中手动验证修复时，使用 `actual_execution_command` 进行验证执行。
不要直接在宿主机上运行 `{entry_script}`，该脚本需要在容器环境中执行。
如果需要在容器内手动验证，请使用：
`{actual_execution_command}`

## Repair Loop
- Inspect `latest_complete_stdout_artifact_path`, `latest_complete_stderr_artifact_path`, and `latest_complete_meta_artifact_path` when populated; prefer complete stdout/stderr over truncated summaries.
- After each in-scope report schema/aggregation fix, run `actual_execution_command` with a timeout, then run `{final_gate_validator_command}`. If the next complete artifacts show another report fixer failure, fix and rerun.
- If the next complete artifacts show only out-of-scope evidence-level, native/custom-op, compiler, shared-object, dependency, environment, or Python-level source failure, stop and write the handoff role and reason in `summary`.
