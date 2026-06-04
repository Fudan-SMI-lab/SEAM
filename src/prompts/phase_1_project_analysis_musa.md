# Phase 1 - Project Analysis (MUXI Accelerator Family)

You are executing `{phase_name}` for `{project_dir}`.

{execution_environment_context}

## User-Provided Constraints
{user_constraints}

## Prior Phase Context
{previous_outputs}

## Serving Route Detection
- Classify `migration_route` as exactly one of `ordinary_cuda`, `custom_op`, `custom_op_with_variants`, `vllm_serving`, or `sglang_serving`.
- Use `vllm_serving` only when project files show a vLLM serving runtime surface such as project-local imports, requirements, launch scripts, README commands, API demos, or tests. Use `sglang_serving` only for equivalent SGLang surface evidence. Do not infer either serving route from package availability alone.
- For vLLM/SGLang routes, include `serving_runtime_surface` with `serving_framework`, `detection_complete`, `launch_command`, `launch_evidence`, `project_demo_or_test_evidence`, `project_test_files`, `readiness_probe`, `request_validation`, `expected_outputs`, `required_runtime_env`, and `unresolved_source_groups`.
- Keep this MUXI/MUSA-specific and platform-neutral: do not copy Ascend/NPU-only requirements such as `torch_npu`, CANN, `tbe`, or `te`.
- Serving route classification is fail-closed: framework must match the route, launch/demo/API/test evidence must be project-local, `project_demo_or_test_evidence` and `project_test_files` must be non-empty, and `unresolved_source_groups` must be empty when `detection_complete=true`.

## Goal
Analyze the project migration surface without editing files. Distinguish Python API changes, dependency/runtime changes, entry-command selection, and native/custom operator work. Do not decide API rewrites by workflow name alone; use Phase 0 observed vendor facts.

## Required Actions
1. Inspect README/setup files, dependency declarations, launch scripts, source directories, tests, `setup.py`, `pyproject.toml`, and build files.
2. Detect CUDA/MUXI signals: `torch.cuda`, `.cuda()`, CUDA device strings, NCCL, CUDA extensions, `nvcc`, `CUDAExtension`, `cpp_extension`, `ctypes.CDLL`, `torch.ops`, pybind, C++/CUDA sources, and runtime-loaded shared objects.
3. Classify API policy from observed vendor facts:
   - CUDA-compatible vendor torch means `torch.cuda` may be correct and should be preserved unless evidence says otherwise.
   - Native MUSA stack means `torch_musa` or `torch.musa` may be required.
   - Communication backend changes such as `nccl` to `mccl` require runtime/vendor evidence.
4. Identify the best non-interactive validation entry candidate from documented usage, launchers, tests, demos, or train/inference scripts.
5. If native/custom operators exist, enumerate fine-grained operator units with source evidence from source, bindings, wrappers, autograd, aliases, launch sites, setup/build files, and tests.

## Hard Rules
- Do not modify files.
- Do not invent entry points, CLI flags, packages, or vendor APIs not observed.
- Do not call `task()`, launch background/sub-agent work, create todos, or wait for background task notifications in this phase. Inspect files directly and return the phase JSON in this same response.
- Use README guidance and project files as evidence; do not invent an entry point.
- If multiple entry candidates exist, choose the most likely executable path and make the choice deterministic.
- If no strong CUDA evidence exists, set `cuda_detected` to `false`.
- Do not classify a broken Python import as a custom op unless source/build/native loading evidence exists.
- If `custom_op_detected` is true, `discovery_complete` is true only when every discovered unit has source evidence and unresolved groups are listed.
- End with exactly one JSON object and no other JSON.

## Output Format
```json
{
  "project_dir": "/path/to/project",
  "dependencies": ["torch", "numpy"],
  "cuda_detected": true,
  "muxi_migration_required": true,
  "entry_script": "train.py",
  "migration_route": "sglang_serving",
  "serving_runtime_surface": {
    "serving_framework": "sglang",
    "detection_complete": true,
    "launch_command": "python -m sglang.launch_server --model-path example",
    "launch_evidence": ["README documents SGLang launch_server"],
    "project_demo_or_test_evidence": ["tests/test_api.py calls the serving API"],
    "project_test_files": ["tests/test_api.py"],
    "readiness_probe": {"type": "http", "path": "/health"},
    "request_validation": {"type": "http", "path": "/generate"},
    "expected_outputs": ["HTTP 200 with generated text"],
    "required_runtime_env": ["MUXI/MUSA serving runtime and vendor-compatible vLLM/SGLang package"],
    "unresolved_source_groups": []
  },
  "api_compatibility_assessment": {
    "torch_cuda_can_be_preserved": true,
    "native_musa_api_required": false,
    "comm_backend_changes": ["nccl -> mccl if distributed path executes and mccl exists"],
    "device_string_policy": "preserve_cuda_when_vendor_cuda_compatible"
  },
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
    "negative_evidence": ["searched source roots and found no additional operator families"],
    "dynamic_loading_checks": ["inspected runtime-loaded torch.ops/ctypes/pybind paths"],
    "build_load_checks": ["inspected setup.py/pyproject build hooks for native extensions"],
    "unresolved_source_groups": [],
    "out_of_scope_source_groups": [],
    "fine_grained_operator_unit_evidence": [
      {"unit_identity": "family_a:signature_x", "source_evidence": ["csrc/op.cu:signature_x"]}
    ]
  }
}
```
