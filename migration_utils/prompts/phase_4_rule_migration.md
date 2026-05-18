# Phase 4 - Rule-Based Migration

You are executing `{phase_name}` for `{project_dir}`.
Use the upstream context in `{previous_outputs}` to guide scope and priorities.

## Goal
- Apply deterministic rule-based CUDA to NPU replacements.
- Capture how many files changed, how many were skipped, and how many replacements each rule produced.

## Required Actions
1. Scan source files that are likely to contain runtime device logic, distributed settings, or CUDA-specific imports.
2. Apply rule-based replacements for the most common migration patterns:
   - inject `import torch_npu` when required for NPU runtime enablement
   - replace `torch.cuda` with `torch.npu`
   - replace `.cuda()` with `.npu()`
   - replace string literals or device selectors from `cuda` to `npu` when the intent is device selection
   - replace `nccl` with `hccl` for distributed backend configuration
3. Skip generated files, virtual environments, third-party vendor code, build artifacts, and binary assets.
4. Keep replacements mechanical and auditable; do not perform speculative refactors in this phase.
5. Track replacement counts per rule key in a deterministic object.

## Hard Rules
- Only change files under `{project_dir}` that are part of the target project source.
- Do not rewrite unrelated logic, formatting, comments, or documentation unless needed for a rule application.
- Do not replace every `cuda` string blindly; preserve strings that are not device semantics.
- If a file is ambiguous or high-risk, skip it and count it in `files_skipped`.
- You may reason freely in your response, but end it with a single JSON object containing exactly the required keys for this phase. No other JSON objects should appear.
- If any package or tooling lookup is needed during this phase, prefer domestic mirrors such as 阿里云镜像 or 清华镜像.

## Output Format
Return exactly one JSON object with this shape:

```json
{
  "files_migrated": 12,
  "files_skipped": 3,
  "replacement_counts": {
    "import_torch_npu": 8,
    "torch_cuda_to_torch_npu": 14,
    "cuda_call_to_npu_call": 9,
    "device_cuda_to_npu": 11,
    "nccl_to_hccl": 2
  }
}
```

## Field Semantics
- `files_migrated`: count of files actually modified.
- `files_skipped`: count of reviewed but intentionally skipped files.
- `replacement_counts`: per-rule replacement totals.
