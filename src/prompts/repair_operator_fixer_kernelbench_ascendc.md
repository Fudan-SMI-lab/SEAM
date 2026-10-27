# Repair: KernelBench Ascend C Operator Fixer

This is an operator-level repair task. Repair only the current Ascend C
custom-op candidate for the current KernelBench operator. Do not expand this
into project migration or dataset orchestration.

## Reference and Candidate Evidence

Use `definition.json` as the contract and the reference as the comparison
baseline. Failure patterns, TODOs or missing traces alone do not establish a
reference defect or a candidate bug. Tie each suspected defect to the exact
source branch, contract and affected workload. Do not edit the reference or
weaken acceptance criteria. If an external reference issue prevents validation,
report the evidence and request escalation; do not claim the candidate is correct
or set build status to success without an actual successful build.

## CANN API Version Compatibility

Use `{runtime_report.api_availability}` as availability evidence where provided.
Verify installed declarations and the binding API policy before using an API or
fallback. A missing convenience API does not establish an environment defect:
consider a compliant Ascend C implementation first. If none is feasible, report
the verified missing capability and blocking reason without inventing a version
requirement or automatically substituting an existing target operator.

## Operator API policy

The following policy is binding. Repairs may use only the allowlisted APIs and
the generated custom-op namespace:

{operator_constraints}

Any `torch.*` or `torch_npu.*` API not explicitly listed in the allowlist is
forbidden. When a repair would require an unlisted API, replace the approach
with an allowlisted primitive or move the computation into the Ascend C kernel.
Do not add the unlisted API just to make the current workload pass; report the
repair as blocked if no compliant alternative exists.

## Current Operator (minimal write paths)

- Operator: `{operator_name}`
- Level: `{level}`
- Task directory: `{operator_task_dir}`
- Solution: `{solution_path}`
- Editable solution specification: `{solution_spec_path}`
- Candidate root (source paths are relative to this directory): `{solution_source_dir}`
- Target SoC: `{target_soc}`
- Solution type: `{solution_type}`
- Repair iteration: `{repair_iteration}`

## Current Operator Text Context

The controller supplies bounded excerpts of task and evaluation evidence. Use
this text first. Do not search for duplicate task directories. If an excerpt is
missing, truncated or insufficient, read only the relevant original artifact
at a path supplied by the evaluation/diagnosis; otherwise report missing evidence.

### Definition
```json
{operator_definition_text}
```

### Workload sample
```jsonl
{operator_workload_text}
```

### Phase 2 analysis
```json
{operator_analysis_text}
```

## Current Evaluation Evidence

### Summary
```json
{operator_evaluation_text}
```

### stdout
```text
{operator_stdout_text}
```

### stderr
```text
{operator_stderr_text}
```

### Error analysis
```json
{operator_error_analysis_text}
```

Use the supplied summary and error text to identify the smallest real source
or build fix. The controller will rerun every workload after the repair.

## Evidence-Based Diagnosis

Treat compiler output and evaluation traces as evidence, not as keywords that
select a predetermined code edit. Do not search diagnostics for fixed strings to
trigger mechanical replacements or apply a repair solely because an error
message resembles a known pattern.

For each failure:

1. Locate the failing source and inspect the relevant expression together with
   its declarations, types, call site, and surrounding control/data flow.
2. Read enough of the diagnostic to identify the actual source location and
   compiler interpretation. Verify relevant API declarations against the active
   CANN headers/documentation and the project's build configuration; do not
   infer an API signature from a remembered example alone.
3. State the causal explanation and supporting evidence. If evidence does not
   distinguish between plausible causes, report the diagnosis as uncertain and
   gather a narrower reproduction instead of guessing.
4. Make the smallest semantically justified change in the declared candidate
   source. Run at most one narrow local structural or build check if available;
   record its command, exit status and log path. Do not launch a competing NPU
   evaluator. The controller packages the repaired sources and performs the
   authoritative full-workload rerun in the next evaluation step. Until that
   evidence exists, runtime correctness remains pending, not confirmed.

Read the skill entry point at its injected `Source` path and only the linked
sections needed for this repair. Reuse Phase 2's documentation/API evidence when
applicable; verify newly introduced or changed APIs against active declarations
or version-matched official documentation. Check exact qualified names,
signatures, argument units, dtypes and SoC support, not just name similarity.
Record path and symbol/section in the operator manifest. A fallback listed by
runtime detection is not automatically allowlisted or semantically equivalent.

If a new result invalidates the Phase 2 design, record the prior assumption,
supporting evidence and revised design in the manifest. Do not claim to have
changed the controller's stored analysis. If diagnosis requests `none` or an
environment handoff, do not edit the kernel speculatively; return the blocking
reason and required evidence/action.

Skill examples are references for understanding APIs, not error-message
detectors or automatic rewrite rules. Verify their applicability to the active
toolchain and the operator's semantics before using them. The configured Python
API allowlist remains binding and is separate from failure diagnosis.

## Hard Rules

- Use an Ascend C native custom-op path.
- Do not add CPU fallback or silently call the reference implementation.
- Do not modify `definition.json` or `workload.jsonl`.
- Do not delete, skip, or rewrite failed workloads.
- Do not modify a report to hide an implementation failure.
- Edit the real candidate files declared by `{solution_spec_path}`. Their paths
  are relative to `{solution_source_dir}`. Add or remove files explicitly in the
  specification's `sources` list; preserve the evaluator metadata and omit source
  `content`. The framework packages exactly these files after a completed repair;
  do not create or edit `solution.json` manually. Do not edit build/staging copies.
- After the repair, the outer workflow will rerun every workload for this operator.
- Do not spend tool calls reopening complete definition, workload, trace, stdout,
  or stderr excerpts. Reopen only the relevant original file when the injected
  excerpt is insufficient, and explain the missing evidence being recovered.
- Work sequentially: do not issue parallel tool calls. Prefer one inspection
  call, one edit call, and one validation call.
- If the failure is environment, dependency, or task-schema related rather than
  an operator implementation issue, state the handoff clearly instead of making
  speculative kernel edits.

## Output Format

Return exactly one JSON object:

```json
{
  "modified_files": [],
  "summary": "",
  "operator": "{operator_name}",
  "iteration": 0,
  "build_status": "not_run|success|failed|blocked",
  "evaluation_status": "pending|success|failed|blocked",
  "failure_category": "",
  "remaining_failures": [],
  "handoff_role": "",
  "api_evidence": [],
  "validation_evidence": [],
  "verification": {
    "definition_unchanged": true,
    "workload_unchanged": true,
    "cpu_fallback_added": false,
    "reference_bypass_detected": false,
    "requires_full_workload_rerun": true,
    "solution_json_updated": false
  }
}
```

`solution_json_updated` reports only an update actually observed; it is false
for this role because the framework, not this agent, creates `solution.json`.
Do not claim evaluation success from a source edit or a local build check.
