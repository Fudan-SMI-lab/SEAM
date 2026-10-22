# Phase 3: Ascend C Custom-Op Preparation

Generate the initial candidate for the current single KernelBench operator.
This phase runs when the candidate is missing or structurally invalid. Existing
candidates use the repair prompt with the latest KernelBench evidence.

This is a bounded implementation task, not a KernelBench or CANN source-code
survey. Read only the supplied task definition, workload sample, reference,
the relevant custom-op skill, and the minimum build example needed for the
target SoC. Do not scan the whole KernelBench repository, anti-hack
implementation, CANN header tree, or unrelated operators. Do not write
exploratory test projects. Produce the candidate artifacts first, then run at
most one narrow structural check.

## Context

- Operator API policy: `{operator_constraints}`

- Operator workspace: `{operator_task_dir}`
- Operator name: `{operator_name}`
- Definition: `{definition_path}`
- Workloads: `{workload_path}`
- Target SoC: `{target_soc}`
- Analysis output: `{previous_outputs}`
- Runtime detection report: `{runtime_report}`
- Runtime configuration result: `{runtime_configuration}`
- Candidate root (source paths are relative to this directory): `{solution_source_dir}`
- Editable source specification: `{solution_spec_path}`
- Framework-generated solution (do not edit): `{solution_path}`

## CANN API Version Compatibility

The runtime detection has identified which `torch_npu.*` APIs are available in the
current CANN environment. **You must respect these constraints**:

**Runtime API compatibility**:
```json
{runtime_report.api_availability}
```

**Usage rules**:
1. **Check the `api_availability` mapping** before using any version-gated API.
2. **If an API is marked `"available": false`**, use its `"fallback"` instead.
3. **Never hardcode V2 APIs** without checking availability first.

**Example**: `npu_grouped_matmul_swiglu_quant_v2`
```python
# ❌ Wrong: hardcoded V2
output = torch_npu.npu_grouped_matmul_swiglu_quant_v2(...)

# ✅ Correct: use fallback from runtime config
# If V2 unavailable, fallback is "npu_grouped_matmul_swiglu_quant"
output = torch_npu.npu_grouped_matmul_swiglu_quant(...)
```

**If you encounter a missing API during implementation**, return with:
```json
{
  "build_status": "blocked",
  "blocking_category": "environment",
  "summary": "Operator requires aclnnXXX (CANN >= X.Y) but current environment only has CANN X.Z. No compatible fallback exists."
}
```

## Critical: Reference Implementation Is a Baseline, Not Ground Truth

The reference implementation in the task directory is **one possible interpretation
of the operator semantics**, not absolute correctness. When studying the reference:

1. **Use it as a semantic guide**, not a literal template to copy.
2. **Identify the core algorithm** and data flow, not implementation details.
3. **Expect potential defects**: uninitialized memory for certain parameter
   combinations, undefined edge case handling, mixed precision inconsistencies.
4. **Definition.json is the contract**, not the reference code. If they conflict,
   the JSON specification takes precedence.

**Your candidate will be judged against the reference output**, but if you detect
obvious reference bugs during implementation (e.g., uninitialized returns, TODOs,
NotImplementedError), note them in the operator manifest so later repair phases
can distinguish candidate bugs from reference defects.

Do NOT blindly replicate reference bugs. Implement the semantics as specified in
`definition.json` first; if that produces valid outputs that happen to differ
from a broken reference, the repair phase will handle it.

## Memory Safety Requirements (Critical)

The Ascend C implementation MUST guarantee memory safety. VA (Virtual Address) faults are the most common failure mode in the official KernelBench 105 benchmark suite:

1. **Address calculation**: All GM/UB offsets must stay within allocated regions.
   - For tiling: verify each tile's start/end offset before DataCopy.
   - For tail blocks: if `remaining < tileSize`, use `remaining`, not `tileSize`.
   - Example: `offset = blockIdx * tileSize + threadIdx * elementSize` must satisfy
     `offset + copyLen <= totalAllocatedBytes`.

2. **DataCopy blockLen**: Must match the actual data size, not exceed buffer capacity.
   - Common mistake: using `tileSize` for the last block when only `remaining` elements exist.
   - Fix: `actualLen = min(tileSize, totalElements - processedElements)`.

3. **Alignment**: NPU requires 32-byte alignment for bulk DataCopy.
   - If offset is NOT 32-byte aligned, use scalar GetValue/SetValue instead.
   - After scalar operations, call `PipeBarrier<PIPE_ALL>()` before vector ops.

4. **Verification**: Before submission, mentally trace the LAST workload:
   - What is the final tile's offset?
   - Does `offset + blockLen` exceed allocated size?
   - If yes, reduce `blockLen` for the tail block.

VA (Virtual Address) faults manifest as:
- `devmm Va error / Check alloced va / Device fault error ret=-22`
- Timeout with 0/0 traces (worker stops responding)
- Access address exactly `allocEnd + pageSize` (典型的 +1 页越界)

If your implementation has ANY dynamic offset calculation (e.g., loop with 
`blockIdx`, `for (int i = 0; i < rows; i++)`), apply tail-block protection.

## Ascend C API Constraints (Critical for Code Generation)

**These are HARD CONSTRAINTS enforced by the Ascend C compiler**. Violating any of these will cause compilation failures:

### 1. Queue Position API
```cpp
// ❌ WRONG: These do NOT exist
TQue<QuePos::VEC_IN, BUFFER_NUM>
TQue<AscendC::QuePos::VECIN, ...>
TQue<QuePosition::VEC_IN, ...>

// ✅ CORRECT: Only these exist
TQue<QuePosition::VECIN, BUFFER_NUM>   // Input queue
TQue<QuePosition::VECOUT, BUFFER_NUM>  // Output queue
```

**Never abbreviate** `QuePosition` to `QuePos`. Never use `VEC_IN` or `VEC_OUT` — the correct enum values are `VECIN` and `VECOUT` (no underscore).

### 2. LocalTensor Scalar Access
```cpp
// ❌ WRONG: Cannot assign LocalTensor directly to scalar
LocalTensor<half> tensor = ...;
half value = tensor;  // ❌ Compiler error

// ✅ CORRECT: Use GetValue(index)
half value = tensor.GetValue(0);
```

**Every scalar read** from `LocalTensor` must use `.GetValue(i)`, even when the tensor is 1D and you only need element 0.

### 3. Cast API
```cpp
// ❌ WRONG: Mismatched signature or unnecessary cast
Cast(dst, src, RoundMode::CAST_NONE, N);  // Wrong param order/type
Cast(fp32Tensor, fp32Tensor, ...);        // Same dtype, no cast needed

// ✅ CORRECT: Match template signature, skip same-dtype cast
if (dtype != TARGET_DTYPE) {
    Cast(dst, src, RoundMode::CAST_NONE, elementCount);
}
```

**Read the skill template's `Cast` signature** before using it. Never cast a tensor to its own dtype (e.g., `float → float`).

### 4. Type Conversion in aicore Functions
```cpp
// ❌ WRONG: float ↔ unsigned int conversion inside __aicore__ function
__aicore__ void kernel(...) {
    float f = 1.0f;
    unsigned int u = (unsigned int)f;  // ❌ Not allowed
}

// ✅ CORRECT: Avoid float ↔ uint conversion, or do it on host
// If unavoidable, use bit_cast or extract the conversion to a separate CPU function
```

### 5. Variable Initialization
```cpp
// ❌ WRONG: Uninitialized const variables
const float scale;  // ❌ Must be initialized

// ✅ CORRECT: Always initialize
const float scale = 1.0f;
```

**Before calling any tool to write Ascend C code**, verify your generated code against these 5 rules. If unsure about an API, **read the skill template** (`op_template.cpp`) first — it contains the correct usage patterns.

## Required Actions

Create or update the real Ascend C candidate implementation in the operator workspace.

The operator API policy is binding. Do not introduce unlisted APIs or call an
existing implementation of the target operator through PyTorch, ATen, or
torch_npu. The candidate must dispatch the generated Ascend C custom-op.
Any `torch.*` or `torch_npu.*` API absent from the allowlist must not be used;
replace that approach with an allowlisted primitive or implement the operation
inside the Ascend C kernel. If no compliant approach is available, report a
blocked build instead of silently using the unlisted API.

Required artifacts:

- `solution_spec.json` beside the configured `solution.json`, containing the
  evaluator's solution metadata and an explicit `sources` list of objects with
  `path` but no `content`. Preserve the evaluator's metadata schema; do not invent
  replacement field names. Paths are relative to the directory containing the
  specification, and must point to real candidate files (prefer `sources/`).
- Ascend C host tiling/source;
- Ascend C device kernel/source;
- KernelBench adapter entry point;
- build configuration required by the chosen Ascend C builder;
- an operator manifest describing the generated implementation. The framework
  reads only the files explicitly listed in `solution_spec.json` and atomically
  creates `solution.json` after this phase. Do not create or edit `solution.json`,
  duplicate source text into the specification, or list build outputs/cache files.
  List all adapters, headers, device/host sources, and build inputs needed by the
  evaluator. To add or remove a source, update the explicit list.

Do not modify `definition.json` or `workload.jsonl`. Do not add CPU fallback, reference bypass, stub success, or report-only evidence. The implementation must be the candidate evaluated by KernelBench.

Do not claim the candidate passes evaluation. The enclosing workflow immediately
runs the authoritative full-workload KernelBench evaluation and API preflight.
Run only a narrow structural check if needed to ensure the candidate artifacts
are present and internally consistent; report actual results without replacing
the KernelBench result.

## Pre-Submission Validation (Critical for Multi-Dtype Operators)

**Before finalizing the candidate**, verify it handles ALL dtypes present in the workload:

1. **Check `workload.jsonl` for dtype variants** (e.g., `float16`, `float32`, `bfloat16`).
2. **Test the candidate with at least one workload per dtype**:
   - If the operator has fp16/fp32/bf16 workloads, run 1 sample of each.
   - Verify the kernel does NOT unconditionally cast (e.g., `float → float` is unnecessary).
3. **If any dtype path fails**, fix it before submission.

**Common dtype issues**:
- **Unconditional Cast**: `Cast(fp32, fp32, ...)` is redundant and may cause numerical errors.
  → Fix: Skip Cast when `srcDtype == dstDtype`.
- **Only fp16 tested**: Kernel works for fp16 but breaks for fp32/bf16 due to precision/alignment differences.
  → Fix: Run at least 1 workload per dtype variant before submission.

**Why this matters**: The official KernelBench 105 contains fp16/fp32/bf16 variants. A candidate that only validates fp16 will fail 2/3 of the workloads. Testing ALL dtype paths **once before submission** prevents wasting repair iterations later.

## Output Format

Return exactly one JSON object:

```json
{
  "phase_id": "phase_3_custom_op_prepare",
  "operator": "{operator_name}",
  "operator_task_dir": "{operator_task_dir}",
  "solution_type": "ascendc",
  "solution_path": "{operator_task_dir}/solution.json",
  "source_dir": "{operator_task_dir}/sources",
  "manifest_path": "",
  "build_config_path": "",
  "prepared": true,
  "build_status": "not_run|success|failed",
  "modified_files": [],
  "summary": ""
}
```
