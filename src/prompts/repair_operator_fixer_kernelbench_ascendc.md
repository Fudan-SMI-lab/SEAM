# Repair: KernelBench Ascend C Operator Fixer

This is an operator-level repair task. Repair only the current Ascend C
custom-op candidate for the current KernelBench operator. Do not expand this
into project migration or dataset orchestration.

## Critical: Reference Implementation Is Not Ground Truth

The reference implementation is **one possible interpretation of the operator
semantics**, not absolute correctness. Before attempting a fix, assess whether
the failure is a **candidate bug** or a **reference defect**:

**Suspect reference defects when**:
- All failures share **identical patterns** correlated with specific parameter
  values (e.g., all `pad=False` fail, all `pad=True` pass).
- Reference code returns **uninitialized memory** or undefined values for certain
  input combinations.
- Candidate output is **semantically valid** but doesn't match reference due to
  ambiguous specification in `definition.json`.
- Reference has visible **TODOs, NotImplementedError, or incomplete branches**.

**Confirm candidate bugs when**:
- Failures have **diverse patterns** across workloads.
- Candidate produces **invalid outputs** (crashes, VA faults, wrong shapes, NaN
  where valid values expected).
- Source code has **direct implementation errors** (off-by-one, memory leaks,
  race conditions).

**If the failure is a reference defect**, return with:
```json
{
  "build_status": "success",
  "evaluation_status": "blocked",
  "failure_category": "reference_defect",
  "handoff_role": "none",
  "summary": "Reference implementation has defect: [具体描述]. Candidate output is semantically valid but doesn't match undefined reference behavior."
}
```

Do NOT modify the candidate to match a broken reference.

## CANN API Version Compatibility

The runtime detection has identified which `torch_npu.*` APIs are available in the
current CANN environment. **Respect these constraints during repairs**:

**Runtime API compatibility**: See `{runtime_report.api_availability}`

**If the failure is caused by using an unavailable API**:
1. Check the `api_availability` mapping from runtime detection.
2. Replace the unavailable API with its `fallback`.
3. Verify the fallback has equivalent semantics for this workload.

**Example**: Repairing `GroupedMatmulSwigluQuantV2` when V2 is unavailable:
```python
# Original (failing on CANN 8.3)
output = torch_npu.npu_grouped_matmul_swiglu_quant_v2(...)

# Repaired (fallback to V1)
output = torch_npu.npu_grouped_matmul_swiglu_quant(...)
```

**If no compatible fallback exists**, return with:
```json
{
  "build_status": "blocked",
  "evaluation_status": "blocked",
  "failure_category": "environment",
  "summary": "Operator requires aclnnXXX (CANN >= X.Y) but current environment only has CANN X.Z. No compatible fallback API exists."
}
```

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
- Source directory: `{solution_source_dir}`
- Target SoC: `{target_soc}`
- Solution type: `{solution_type}`
- Repair iteration: `{repair_iteration}`

## Current Operator Text Context

The controller already read the relevant task and evaluation evidence. Use
this text first; do not open evidence paths yourself or search for duplicate
task directories.

### Definition
```json
{operator_definition_text}
```

### Workload sample
```jsonl
{operator_workload_text}
```

### Phase 1 analysis
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

## Ascend C API Constraints (Critical Compilation Rules)

**Before making ANY repair to Ascend C kernel code**, verify the fix complies with these HARD CONSTRAINTS enforced by the Ascend C compiler:

### 1. Queue Position API
```cpp
// ❌ WRONG: These do NOT exist
TQue<QuePos::VEC_IN, ...>
TQue<QuePosition::VEC_IN, ...>

// ✅ CORRECT: Only these exist
TQue<QuePosition::VECIN, BUFFER_NUM>   // Input queue
TQue<QuePosition::VECOUT, BUFFER_NUM>  // Output queue
```

If the error mentions `QuePos` or `VEC_IN`, replace with `QuePosition::VECIN` / `VECOUT`.

### 2. LocalTensor Scalar Access
```cpp
// ❌ WRONG: Cannot assign LocalTensor to scalar
half value = tensor;  // Compiler error

// ✅ CORRECT: Use GetValue(index)
half value = tensor.GetValue(0);
```

If the error mentions "cannot convert LocalTensor to scalar type", add `.GetValue(i)`.

### 3. Cast API
```cpp
// ❌ WRONG: Mismatched signature or same-dtype cast
Cast(dst, src, RoundMode::CAST_NONE, N);  // Wrong param order
Cast(fp32Tensor, fp32Tensor, ...);        // Unnecessary

// ✅ CORRECT: Match template signature, skip same-dtype
if (srcDtype != dstDtype) {
    Cast(dst, src, RoundMode::CAST_NONE, count);
}
```

If the error mentions `Cast` signature mismatch, read the skill template's `Cast` usage.

### 4. Type Conversion in aicore
```cpp
// ❌ WRONG: float ↔ unsigned int inside __aicore__
unsigned int u = (unsigned int)floatVar;  // Not allowed

// ✅ CORRECT: Avoid or use bit_cast / do on host
```

If the error mentions "invalid conversion from float to unsigned int", remove the conversion or move it to host code.

### 5. Variable Initialization
```cpp
// ❌ WRONG: Uninitialized const
const float scale;  // Compiler error

// ✅ CORRECT: Always initialize
const float scale = 1.0f;
```

**Common repair patterns**:
- Compilation error mentioning `QuePos` → replace with `QuePosition`
- Compilation error mentioning `VEC_IN/VEC_OUT` → replace with `VECIN/VECOUT`
- Compilation error assigning `LocalTensor` to scalar → add `.GetValue(i)`
- Compilation error with `Cast` → check signature against template, skip same-dtype casts
- Compilation error with uninitialized variable → add explicit initialization

## Hard Rules

- Use an Ascend C native custom-op path.
- Do not add CPU fallback or silently call the reference implementation.
- Do not modify `definition.json` or `workload.jsonl`.
- Do not delete, skip, or rewrite failed workloads.
- Do not modify a report to hide an implementation failure.
- **Modify real source files AND update solution.json embedded sources.**
  → After editing a `.cpp` file, you MUST read it back and write its content into
    `solution.json`'s `sources[].content` field for that file. Otherwise the next
    evaluation will use the OLD embedded version, not your fix.
- After the repair, the outer workflow will rerun every workload for this operator.
- Do not spend tool calls reopening definition, workload, trace, stdout, or
  stderr files; their relevant contents are already included above.
- Work sequentially: do not issue parallel tool calls. Prefer one inspection
  call, one edit call, ONE solution.json update call, and one validation call.
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
  "build_status": "success|failed|blocked",
  "evaluation_status": "pending|success|failed|blocked",
  "failure_category": "",
  "remaining_failures": [],
  "handoff_role": "",
  "verification": {
    "definition_unchanged": true,
    "workload_unchanged": true,
    "cpu_fallback_added": false,
    "reference_bypass_detected": false,
    "requires_full_workload_rerun": true,
    "solution_json_updated": true
  }
}
```
