# {{PROJECT_NAME}} CUDA to Ascend Migration Record

This template is for project-local documentation of a CUDA custom-op to Ascend
custom-op migration. It preserves what was learned, what was tried, and what stayed
open.

## 1. Project Background

{{Project summary, original CUDA custom op, target Ascend op, and migration goal.}}

| Count Type | Count | Source of Truth | Notes |
|------------|-------|-----------------|-------|
| CUDA source ops | `{{CUDA_SOURCE_OP_COUNT}}` | `{{CUDA_SOURCE_COUNT_METHOD}}` | `{{CUDA_SOURCE_COUNT_NOTES}}` |
| Real installed NPU OPP ops | `{{REAL_OPP_OP_COUNT}}` | generated ACLNN headers + op-info entries | `{{REAL_OPP_COUNT_NOTES}}` |
| Adapter callable entries | `{{ADAPTER_FUNCTION_COUNT}}` | `{{ADAPTER_COUNT_METHOD}}` | `{{ADAPTER_COUNT_NOTES}}` |
| Framework registry names or aliases | `{{FRAMEWORK_ALIAS_COUNT}}` | `{{FRAMEWORK_REGISTRY_METHOD}}` | `{{ALIAS_COUNT_NOTES}}` |

Count differences are useful context for later interpretation.

## 2. Initial State

### Evidence already available

- {{Accepted evidence item 1}}
- {{Accepted evidence item 2}}
- {{Accepted evidence item 3}}

### Open work at the start of migration

1. {{Open gap 1}}
2. {{Open gap 2}}
3. {{Open gap 3}}

### Migration goals

1. {{Goal 1}}
2. {{Goal 2}}
3. {{Goal 3}}

## 3. Migration Strategy Notes

### Core math formula

```
{{Write the mathematical formula the kernel implements.}}
```

### Execution regions

| Region | Condition | Computation |
|--------|-----------|-------------|
| {{REGION_1_NAME}} | {{REGION_1_CONDITION}} | {{REGION_1_COMPUTATION}} |
| {{REGION_2_NAME}} | {{REGION_2_CONDITION}} | {{REGION_2_COMPUTATION}} |

### Hybrid strategy notes

{{Notes about how custom kernel and baseline path interact, including any halo,
ghost-cell, boundary-layer, mask, or dependency-radius behavior.}}

### Active installation metadata

| Item | Value |
|------|-------|
| CANN install path | `{{CANN_INSTALL_PATH}}` |
| Active vendor OPP path | `{{CUSTOM_OPP_PATH}}` |
| Vendor op API lib path | `{{VENDOR_OP_API_LIB_PATH}}` |
| Generated ACLNN header path | `{{GENERATED_ACLNN_HEADER_PATH}}` |
| Kernel binary path | `{{KERNEL_BINARY_PATH}}` |
| Kernel function name | `{{KERNEL_FUNCTION_NAME}}` |
| `opParaSize` | `{{OP_PARA_SIZE}}` |

## 4. Implementation Notes

1. **{{Step 1 title}}**

   {{What changed, what was observed, and what remained open.}}

   ```{{lang}}
   {{code snippet if relevant}}
   ```

2. **{{Step 2 title}}**

   {{What changed, what was observed, and what remained open.}}

3. **{{Step 3 title}}**

   {{What changed, what was observed, and what remained open.}}

4. **{{Step 4 title}}**

   {{What changed, what was observed, and what remained open.}}

5. **{{Step 5 title}}**

   {{What changed, what was observed, and what remained open.}}

## 5. Problems Encountered

### Problem 1: {{Short title}}

**Symptom**: {{Observed error or wrong behavior.}}

**Root cause**: {{Explanation of why it happened.}}

**Correction notes**:

```{{lang}}
# Before:
{{wrong code}}

# After:
{{correct code}}
```

### Problem 2: {{Short title}}

**Symptom**: {{Observed error or wrong behavior.}}

**Root cause**: {{Explanation of why it happened.}}

**Correction notes**: {{How it was addressed and what evidence followed.}}

### Problem 3: {{Short title}}

**Symptom**: {{Observed error or wrong behavior.}}

**Root cause**: {{Explanation of why it happened.}}

**Correction notes**: {{How it was addressed and what evidence followed.}}

## 6. Key Technical Decisions

| Decision | Option A | Option B | Choice | Reason |
|----------|----------|----------|--------|--------|
| {{Decision 1}} | {{Option A}} | {{Option B}} | {{Which was chosen}} | {{Why}} |
| {{Decision 2}} | {{Option A}} | {{Option B}} | {{Which was chosen}} | {{Why}} |
| {{Decision 3}} | {{Option A}} | {{Option B}} | {{Which was chosen}} | {{Why}} |
| {{Decision 4}} | {{Option A}} | {{Option B}} | {{Which was chosen}} | {{Why}} |

### Fallback and validation notes

| Case | Runtime behavior | Validation behavior | Reason |
|------|------------------|---------------------|--------|
| Non-NPU device | {{NON_NPU_RUNTIME_BEHAVIOR}} | {{NON_NPU_VALIDATION_BEHAVIOR}} | {{NON_NPU_REASON}} |
| Missing adapter or custom op | {{MISSING_OP_RUNTIME_BEHAVIOR}} | {{MISSING_OP_VALIDATION_BEHAVIOR}} | {{MISSING_OP_REASON}} |
| Unsupported shape, dtype, or order | {{UNSUPPORTED_RUNTIME_BEHAVIOR}} | {{UNSUPPORTED_VALIDATION_BEHAVIOR}} | {{UNSUPPORTED_REASON}} |
| Framework unsupported mode | {{FRAMEWORK_UNSUPPORTED_RUNTIME_BEHAVIOR}} | {{FRAMEWORK_UNSUPPORTED_VALIDATION_BEHAVIOR}} | {{FRAMEWORK_UNSUPPORTED_REASON}} |

### Open implementation evidence

Use this table for work items that still need proof or follow-up. Missing real
artifacts, incomplete slices, and compile-only evidence stay visible here rather
than being blended into final migration notes.

| Implementation log | Attempted stage | Command or probe | Output or error summary | Failing artifact | Failure or precondition | Remediation attempted | Next artifact or fact |
|---------|-----------------|------------------|-------------------------|------------------|-------------------------|-----------------------|-----------------------|
| {{IMPLEMENTATION_WORK_ITEM_1}} | {{STAGE_1}} | `{{COMMAND_1}}` | {{OUTPUT_SUMMARY_1}} | {{ARTIFACT_1}} | {{FAILURE_1}} | {{REMEDIATION_1}} | `{{NEXT_ARTIFACT_1}}` |
| {{IMPLEMENTATION_WORK_ITEM_2}} | {{STAGE_2}} | `{{COMMAND_2}}` | {{OUTPUT_SUMMARY_2}} | {{ARTIFACT_2}} | {{FAILURE_2}} | {{REMEDIATION_2}} | `{{NEXT_ARTIFACT_2}}` |

## 7. Modified Files

| File | Change type | Description |
|------|-------------|-------------|
| `{{file_path_1}}` | New / Modified / Rewritten | {{What changed and why}} |
| `{{file_path_2}}` | New / Modified / Rewritten | {{What changed and why}} |
| `{{file_path_3}}` | New / Modified / Rewritten | {{What changed and why}} |

## 8. Out-of-Scope Notes

| Item | Scope | Why it stayed open | Next action |
|------|-------|--------------------|-------------|
| {{NOTE_OR_GAP_1}} | {{OUT_OF_SCOPE_OR_OPEN}} | {{NON_FINAL_REASON_1}} | {{NEXT_ACTION_1}} |
| {{NOTE_OR_GAP_2}} | {{OUT_OF_SCOPE_OR_OPEN}} | {{NON_FINAL_REASON_2}} | {{NEXT_ACTION_2}} |

## 9. Lessons Learned

1. **{{Lesson 1 title}}**: {{What was learned and how it applies to future work.}}
2. **{{Lesson 2 title}}**: {{What was learned and how it applies to future work.}}
3. **{{Lesson 3 title}}**: {{What was learned and how it applies to future work.}}
4. **{{Lesson 4 title}}**: {{What was learned and how it applies to future work.}}

### Backwrite candidates

Use `Solved_Lesson_Backwrite_Template.md` for lessons that feel reusable across
projects. Project-specific paths, secrets, raw logs, and private URLs stay out of
shared skill files.

| Lesson | Generic rule? | Generalization proof | Validation evidence | Scope / anti-scope | Leakage scan | Target file | Backwrite status |
|--------|---------------|----------------------|---------------------|--------------------|--------------|-------------|------------------|
| {{LESSON_CANDIDATE_1}} | {{YES_NO}} | {{GENERALIZATION_1}} | {{VALIDATION_1}} | {{SCOPE_1}} / {{ANTI_SCOPE_1}} | {{SCAN_1}} | `{{TARGET_FILE_1}}` | {{STATUS_1}} |
| {{LESSON_CANDIDATE_2}} | {{YES_NO}} | {{GENERALIZATION_2}} | {{VALIDATION_2}} | {{SCOPE_2}} / {{ANTI_SCOPE_2}} | {{SCAN_2}} | `{{TARGET_FILE_2}}` | {{STATUS_2}} |
