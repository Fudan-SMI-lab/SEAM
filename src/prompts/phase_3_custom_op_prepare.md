# Phase 2: Ascend C Custom-Op Preparation

Generate the initial candidate for the current single KernelBench operator.
This phase runs only on the first iteration of the combined generation and
evaluation loop. Later iterations use the repair prompt with the latest
KernelBench evidence.

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

- `solution.json` describing the candidate implementation;
- Ascend C host tiling/source;
- Ascend C device kernel/source;
- KernelBench adapter entry point;
- build configuration required by the chosen Ascend C builder;
- an operator manifest describing the generated implementation.

Do not modify `definition.json` or `workload.jsonl`. Do not add CPU fallback, reference bypass, stub success, or report-only evidence. The implementation must be the candidate evaluated by KernelBench.

Do not claim the candidate passes evaluation. The enclosing workflow immediately
runs the authoritative full-workload KernelBench evaluation and API preflight.
Run only a narrow structural check if needed to ensure the candidate artifacts
are present and internally consistent; report actual results without replacing
the KernelBench result.

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
