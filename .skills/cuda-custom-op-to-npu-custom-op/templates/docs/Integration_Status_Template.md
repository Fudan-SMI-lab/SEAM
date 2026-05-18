# {{PROJECT_NAME}} Integration Status Report

This template summarizes architecture, verification observations, and open work
for a CUDA-to-Ascend migration.

## 1. Architecture

### Operator package summary

| Metric | Value | Source |
|--------|-------|--------|
| CUDA/source ops expected | `{{CUDA_SOURCE_OP_COUNT}}` | `{{CUDA_SOURCE_COUNT_SOURCE}}` |
| Real installed NPU OPP ops | `{{REAL_OPP_OP_COUNT}}` | generated ACLNN headers + op-info entries |
| Adapter callable entries | `{{ADAPTER_FUNCTION_COUNT}}` | `{{ADAPTER_COUNT_SOURCE}}` |
| Framework custom ops | `{{FRAMEWORK_OP_COUNT}}` | `{{FRAMEWORK_OP_SOURCE}}` |
| Actually-called custom ops in benchmark | `{{ACTUALLY_CALLED_OP_COUNT}}` | framework or adapter call counters |

Count differences are often the first thing worth explaining.

### Execution paths

| Path | Trigger | Compute device | Notes | Status |
|------|---------|----------------|-------|--------|
| Baseline | `{{BASELINE_MODE}}` | {{BASELINE_DEVICE}} | Reference path used for accuracy and performance comparison | {{BASELINE_STATUS}} |
| Custom op | `{{CUSTOM_OP_MODE}}` | Ascend NPU | Ascend C/CANN custom operator path through {{ADAPTER_TYPE}} | {{CUSTOM_OP_STATUS}} |
| Alternate path 1 | `{{ALT_MODE_1}}` | {{ALT_DEVICE_1}} | {{ALT_NOTES_1}} | {{ALT_STATUS_1}} |
| Alternate path 2 | `{{ALT_MODE_2}}` | {{ALT_DEVICE_2}} | {{ALT_NOTES_2}} | {{ALT_STATUS_2}} |

### Routing logic sample

```python
def {{routing_function}}(mode, *args, **kwargs):
    if mode == "{{CUSTOM_OP_MODE}}":
        return {{custom_op_route}}(*args, **kwargs)
    if mode == "{{BASELINE_MODE}}":
        return {{baseline_route}}(*args, **kwargs)
    return {{existing_route}}(mode, *args, **kwargs)
```

If the project is not Python-based, the same idea can be shown with the native
dispatcher, plugin registry, or C++ call path.

## 2. Integration Approach

### Computation ownership diagram

```text
                    +------------------------------------------+
                    |         {{custom_op_route}}              |
                    +-------------------+----------------------+
                                        |
              +-------------------------+-------------------------+
              v                         v                         v
        {{REGION_1_NAME}}         {{REGION_2_NAME}}         {{AUX_REGION_NAME}}
      ({{REGION_1_RULE}})       ({{REGION_2_RULE}})       ({{AUX_REGION_RULE}})
              |                         |                         |
        CANN custom op            {{REGION_2_PATH}}         {{AUX_REGION_PATH}}
      {{KERNEL_COMPUTATION}}      {{REGION_2_COMPUTE}}      {{AUX_REGION_COMPUTE}}
              |                         |                         |
              +-------------------------+-------------------------+
                                        v
                              {{OUTPUT}} = {{MERGE_RULE}}
```

For single-region operators, `{{REGION_2_NAME}}` and `{{AUX_REGION_NAME}}` can be
left as not applicable.

### Region detection notes

```python
def {{detect_region_function}}({{detect_region_args}}):
    """Return the region owned by the custom op."""
    {{DETECT_REGION_CODE}}
```

Stencil or hybrid-region operators often need halo or ghost-cell notes. PML-style
or absorbing-boundary operators often need profile tensor rank and normalization
notes before indexing.

### Fallback conditions

| Condition | Runtime behavior | Validation behavior |
|-----------|------------------|---------------------|
| Non-NPU device | {{NON_NPU_RUNTIME_BEHAVIOR}} | {{NON_NPU_VALIDATION_BEHAVIOR}} |
| `{{ADAPTER_ARTIFACT}}` not loadable | {{MISSING_ADAPTER_RUNTIME_BEHAVIOR}} | {{MISSING_ADAPTER_VALIDATION_BEHAVIOR}} |
| Custom op not installed | {{MISSING_OP_RUNTIME_BEHAVIOR}} | {{MISSING_OP_VALIDATION_BEHAVIOR}} |
| Unsupported dtype, shape, layout, dimension, or algorithm option | {{UNSUPPORTED_RUNTIME_BEHAVIOR}} | {{UNSUPPORTED_VALIDATION_BEHAVIOR}} |
| Zero custom-op calls in candidate run | not a valid acceleration observation | {{ZERO_CALL_VALIDATION_BEHAVIOR}} |

Safe fallback is easier to interpret when it is separated from custom-op benchmark
observations.

## 3. Verification Observations

### 3.1 Kernel test, kernel vs CPU reference

| Metric | Value | Threshold | Status |
|--------|-------|-----------|--------|
| `max|diff|` | `{{KERNEL_MAX_DIFF}}` | `{{KERNEL_THRESHOLD}}` | {{KERNEL_STATUS}} |
| `mean|diff|` | `{{KERNEL_MEAN_DIFF}}` | `{{KERNEL_MEAN_THRESHOLD}}` | {{KERNEL_MEAN_STATUS}} |

Test conditions: {{KERNEL_TEST_CONDITIONS}}

### 3.2 Forward test, custom op vs baseline

| Output | `max_abs_diff` | Threshold | Status |
|--------|----------------|-----------|--------|
| `{{output_field_1}}` | `{{FORWARD_DIFF_1}}` | `{{FORWARD_THRESHOLD_1}}` | {{FORWARD_STATUS_1}} |
| `{{output_field_2}}` | `{{FORWARD_DIFF_2}}` | `{{FORWARD_THRESHOLD_2}}` | {{FORWARD_STATUS_2}} |
| `{{output_field_3}}` | `{{FORWARD_DIFF_3}}` | `{{FORWARD_THRESHOLD_3}}` | {{FORWARD_STATUS_3}} |

Test conditions: {{FORWARD_TEST_CONDITIONS}}

### 3.3 Backward or gradient test, if applicable

Test configuration: {{BACKWARD_TEST_CONFIG}}

| Metric | `{{BASELINE_MODE}}` | `{{CUSTOM_OP_MODE}}` | Difference | Status |
|--------|---------------------|----------------------|------------|--------|
| loss or objective | `{{BASELINE_LOSS}}` | `{{CUSTOM_LOSS}}` | `{{LOSS_DIFF}}` | {{LOSS_STATUS}} |
| gradient summary | `{{BASELINE_GRAD}}` | `{{CUSTOM_GRAD}}` | `{{GRAD_DIFF}}` | {{GRAD_STATUS}} |

If gradients are outside the current scope, the forward-only behavior and error
shape can be noted here instead.

### 3.4 End-to-end test

Configuration: {{E2E_CONFIG}}

| Step | Baseline metric | Custom-op metric | Difference | Match |
|------|-----------------|------------------|------------|-------|
| 1 | `{{STEP1_BASELINE}}` | `{{STEP1_CUSTOM}}` | `{{STEP1_DIFF}}` | {{STEP1_MATCH}} |
| 2 | `{{STEP2_BASELINE}}` | `{{STEP2_CUSTOM}}` | `{{STEP2_DIFF}}` | {{STEP2_MATCH}} |
| 3 | `{{STEP3_BASELINE}}` | `{{STEP3_CUSTOM}}` | `{{STEP3_DIFF}}` | {{STEP3_MATCH}} |

### 3.5 Whole-package framework benchmark

Machine-readable result: `{{BENCHMARK_RESULT_JSON}}`

Report/JSON parity status: {{REPORT_JSON_PARITY_STATUS}}

Manifest: `{{MIGRATION_MANIFEST_JSON}}`, sha256 `{{MANIFEST_SHA256}}`

| Case | Status | Baseline mode | Custom mode | Custom ops called | `max_abs_diff` | Baseline time | Custom time | Measured speed ratio or slowdown |
|------|--------|---------------|-------------|-------------------|----------------|---------------|-------------|----------------------------------|
| `{{CASE_1}}` | {{CASE_1_STATUS}} | `{{BASELINE_MODE}}` | `{{CUSTOM_OP_MODE}}` | `{{CASE_1_CALLS}}` | `{{CASE_1_DIFF}}` | `{{CASE_1_BASELINE_TIME}}` | `{{CASE_1_CUSTOM_TIME}}` | `{{CASE_1_SPEEDUP}}` |
| `{{CASE_2}}` | {{CASE_2_STATUS}} | `{{BASELINE_MODE}}` | `{{CUSTOM_OP_MODE}}` | `{{CASE_2_CALLS}}` | `{{CASE_2_DIFF}}` | `{{CASE_2_BASELINE_TIME}}` | `{{CASE_2_CUSTOM_TIME}}` | `{{CASE_2_SPEEDUP}}` |

### 3.6 Non-OK or unsupported cases

Unsupported, Non-OK, disabled, fallback-routed, stale, zero-call, or missing cases
inside the migration scope are useful to list separately.

| Case | Classification | Reason | Counted as custom acceleration? |
|------|----------------|--------|---------------------------------|
| `{{NON_OK_CASE_1}}` | {{NON_OK_CLASS_1}} | {{NON_OK_REASON_1}} | No |
| `{{NON_OK_CASE_2}}` | {{NON_OK_CLASS_2}} | {{NON_OK_REASON_2}} | No |

## 4. Open Work And Resolved Notes

### Open implementation work

| Issue | Symptom | Root cause | Next implementation action |
|-------|---------|------------|----------------------------|
| {{ISSUE_1_TITLE}} | {{ISSUE_1_SYMPTOM}} | {{ISSUE_1_CAUSE}} | {{ISSUE_1_NEXT_ACTION}} |
| {{ISSUE_2_TITLE}} | {{ISSUE_2_SYMPTOM}} | {{ISSUE_2_CAUSE}} | {{ISSUE_2_NEXT_ACTION}} |

### Implementation evidence

This area works well for notes that describe the next migration or benchmark step
that remains. For each log, include the attempted stage, command or probe, output
or error summary, failed fact or failure, remediation attempted, and next artifact
or fact.

| Implementation log | Attempted stage | Command or probe | Output or error summary | Failure or precondition | Remediation attempted | Next artifact or fact |
|---------|-----------------|------------------|-------------------------|------------------------|-----------------------|-----------------------|
| {{IMPLEMENTATION_WORK_ITEM_1}} | {{STAGE_1}} | `{{COMMAND_1}}` | {{OUTPUT_SUMMARY_1}} | {{FAILURE_1}} | {{REMEDIATION_1}} | `{{NEXT_ARTIFACT_1}}` |
| {{IMPLEMENTATION_WORK_ITEM_2}} | {{STAGE_2}} | `{{COMMAND_2}}` | {{OUTPUT_SUMMARY_2}} | {{FAILURE_2}} | {{REMEDIATION_2}} | `{{NEXT_ARTIFACT_2}}` |

### Historical issues, resolved

| Issue | Resolution |
|-------|------------|
| {{RESOLVED_1}} | {{RESOLUTION_1}} |
| {{RESOLVED_2}} | {{RESOLUTION_2}} |

## 5. File Manifest

### Core modified files

| File | Status | Description |
|------|--------|-------------|
| `{{file_1}}` | {{STATUS_1}} | {{DESC_1}} |
| `{{file_2}}` | {{STATUS_2}} | {{DESC_2}} |

### Supporting files

| File | Description |
|------|-------------|
| `{{support_file_1}}` | {{SUPPORT_DESC_1}} |
| `{{support_file_2}}` | {{SUPPORT_DESC_2}} |
| `{{support_file_3}}` | {{SUPPORT_DESC_3}} |
| `{{MIGRATION_MANIFEST_JSON}}` | Manifest for the op, artifact, adapter, hash, and coverage mapping |
| `{{BENCHMARK_RESULT_JSON}}` | Machine-readable benchmark result used as the report source |

### Documentation

| File | Description |
|------|-------------|
| `Reproduction_Guide.md` | Reproduction notes and commands |
| `Migration_Record.md` | Implementation record with observations and lessons |
| `Integration_Status_Report.md` | This document |

## 6. Evidence Matrix And Open Work

This section is useful when evidence and open work need to stay visible together.

| # | Item | Status | Key metric |
|---|------|--------|-----------|
| 1 | Direct kernel correctness | {{EVIDENCE_STATE_1}} | {{METRIC_1}} |
| 2 | Adapter or caller smoke | {{EVIDENCE_STATE_2}} | {{METRIC_2}} |
| 3 | Project forward comparison | {{EVIDENCE_STATE_3}} | {{METRIC_3}} |
| 4 | Backward or gradient behavior | {{EVIDENCE_STATE_4}} | {{METRIC_4}} |
| 5 | End-to-end behavior | {{EVIDENCE_STATE_5}} | {{METRIC_5}} |
| 6 | Custom-op coverage | {{EVIDENCE_STATE_6}} | {{METRIC_6}} |
| 7 | Manifest artifacts for each entry | {{MANIFEST_ARTIFACTS_STATUS}} | {{MANIFEST_ARTIFACTS_METRIC}} |
| 8 | Preflight observations | {{PREFLIGHT_STATUS}} | {{PREFLIGHT_METRIC}} |
| 9 | Project test suite | {{PROJECT_TESTS_STATUS}} | {{PROJECT_TESTS_METRIC}} |
| 10 | Report/JSON parity | {{REPORT_PARITY_STATUS}} | {{REPORT_PARITY_METRIC}} |
| 11 | Final validation | {{FINAL_VALIDATION_STATUS}} | {{FINAL_VALIDATION_METRIC}} |
| 12 | Measured speedup or slowdown | {{SPEEDUP_OR_SLOWDOWN_STATUS}} | {{SPEEDUP_OR_SLOWDOWN_METRIC}} |
| 13 | Unresolved items | {{UNRESOLVED_ZERO_PROOF_STATUS}} | `{{FINAL_UNRESOLVED_COUNT}}` |

## 7. Lessons For Future Work

| Lesson | Notes |
|--------|-------|
| {{LESSON_1}} | {{LESSON_1_NOTES}} |
| {{LESSON_2}} | {{LESSON_2_NOTES}} |
