# Phase 1 - Project Analysis (MUSA/MUXI)

You are executing `{phase_name}` for `{project_dir}`.

## User-Provided Constraints
{user_constraints}

## Goal
- Understand project structure, dependency declarations, CUDA usage, entry candidates, and native/custom operator surface.
- Identify what must move to MUSA/MUXI using `torch_musa`, MUSA SDK, MUSA compiler/runtime, or MUSA-supported PyTorch primitives.

## Required Actions
1. Inspect README/setup files, dependency files, `setup.py`, `pyproject.toml`, shell launchers, source directories, and tests.
2. Detect CUDA/MUSA migration signals: `torch.cuda`, `.cuda()`, CUDA device strings, NCCL, CUDA extensions, `nvcc`, `CUDAExtension`, `cpp_extension`, `ctypes.CDLL`, `torch.ops`, pybind, C++/CUDA source files, and runtime-loaded shared objects.
3. Identify the best non-interactive validation entry command from documented usage, launchers, tests, demos, or training/inference scripts.
4. If custom/native operators are present, enumerate fine-grained operator units with source evidence from source, bindings, wrappers, autograd, aliases, launch sites, setup/build files, and tests.
5. Do not bypass custom-op discovery with statements like "no custom operators" unless the source search evidence supports it.

## Hard Rules
- Do not modify files.
- Do not invent entry points or unsupported CLI flags.
- If `custom_op_detected` is true, `discovery_complete` is true only when every discovered unit has source evidence and unresolved groups are explicitly listed.
- End with exactly one JSON object and no other JSON.

## Output Format
```json
{
  "project_dir": "/path/to/project",
  "dependencies": ["torch", "numpy"],
  "cuda_detected": true,
  "musa_migration_required": true,
  "entry_script": "train.py",
  "custom_op_surface": {
    "custom_op_detected": true,
    "discovery_complete": true,
    "discovery_sources_checked": ["source", "bindings", "wrappers", "autograd", "aliases", "launch", "setup", "tests"],
    "searched_source_roots": ["src", "csrc", "tests"],
    "searched_source_paths": ["csrc/op.cu", "setup.py"],
    "operator_families": ["family_a"],
    "fine_grained_operator_units": ["family_a:signature_x"],
    "discovered_operator_names": ["family_a_signature_x"],
    "source_evidence": ["csrc/op.cu:signature_x"],
    "negative_evidence": [],
    "dynamic_loading_checks": [],
    "build_load_checks": [],
    "unresolved_source_groups": [],
    "out_of_scope_source_groups": [],
    "fine_grained_operator_unit_evidence": [
      {"unit_identity": "family_a:signature_x", "source_evidence": ["csrc/op.cu:signature_x"]}
    ]
  }
}
```
