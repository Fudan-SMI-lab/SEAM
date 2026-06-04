# Phase 1 - Project Analysis (PPU)

You are executing `{phase_name}` for `{project_dir}`.

## User-Provided Constraints (for awareness)
{user_constraints}

*Note: A detailed, project-specific constraint summary will be generated in Phase 1.5.*

## Serving Route Detection
- Classify `migration_route` as exactly one of `ordinary_cuda`, `custom_op`, `custom_op_with_variants`, `vllm_serving`, or `sglang_serving`.
- Use `vllm_serving` only when project files show a vLLM serving runtime surface such as project-local imports, requirements, launch scripts, README commands, API demos, or tests. Use `sglang_serving` only for equivalent SGLang surface evidence. Do not infer either serving route from package availability alone.
- For vLLM/SGLang routes, include `serving_runtime_surface` with `serving_framework`, `detection_complete`, `launch_command`, `launch_evidence`, `project_demo_or_test_evidence`, `project_test_files`, `readiness_probe`, `request_validation`, `expected_outputs`, `required_runtime_env`, and `unresolved_source_groups`.
- Keep this PPU-specific and platform-neutral: PPU uses CUDA-compatible APIs, so do not copy Ascend/NPU-only requirements such as `torch_npu`, CANN, `tbe`, or `te`.
- Serving route classification is fail-closed: framework must match the route, launch/demo/API/test evidence must be project-local, `project_demo_or_test_evidence` and `project_test_files` must be non-empty, and `unresolved_source_groups` must be empty when `detection_complete=true`.

## Goal
- Understand the project structure and likely execution path.
- Extract dependency signals relevant to CUDA/PPU migration.
- Identify the most likely entry script for training, inference, evaluation, or demo execution.
- When the source surface indicates custom operators, also discover the custom-op surface itself.

## Required Actions
1. Map the top-level layout of `{project_dir}` and identify source, config, scripts, and docs directories.
2. Inspect dependency declarations such as `requirements*.txt`, `environment*.yml`, `pyproject.toml`, `setup.py`, and shell launch scripts.
3. Detect CUDA/PPU-related code distribution by checking for patterns like `torch.cuda`, `.cuda()`, `device='cuda'`, `device="cuda"`, `nccl`, custom CUDA extensions, or GPU-only launch flags.
4. Identify the best entry script candidate by combining README guidance, CLI files, launcher scripts, and common entry names such as `train.py`, `main.py`, `run.py`, `infer.py`, or `app.py`.
5. Keep the dependency list concise and useful; prefer direct project dependencies over transitive noise.
6. If custom operators are present, report the discovered fine-grained operator units, the exact source evidence that proves them, and the source-visible search/probe trail that led to discovery.

## Hard Rules
- Do not modify the project during this phase.
- Use README guidance and project files as evidence; do not invent an entry point.
- If multiple entry candidates exist, choose the most likely executable path and make the choice deterministic.
- If no strong CUDA evidence exists, set `cuda_detected` to `false`.
- If `custom_op_detected` is `true`, set `discovery_complete` to `true` only when every discovered unit is linked to source evidence and the search/probe trail is source-visible.
- You may reason freely in your response, but end it with a single JSON object containing exactly the required keys for this phase. No other JSON objects should appear.
- If any package source or version lookup is needed, prefer PPU vendor index, PTG/t-head artifactory, or offline PPU wheelhouse. Public PyPI installs for key packages can contaminate the PPU environment.

## Output Format
Return exactly one JSON object with this shape:

```json
{
  "project_dir": "/path/to/project",
  "dependencies": ["torch", "numpy", "pyyaml"],
  "cuda_detected": true,
  "entry_script": "train.py",
  "migration_route": "vllm_serving",
  "serving_runtime_surface": {
    "serving_framework": "vllm",
    "detection_complete": true,
    "launch_command": "python serve.py --model example",
    "launch_evidence": ["README documents python serve.py"],
    "project_demo_or_test_evidence": ["tests/test_api.py calls the serving API"],
    "project_test_files": ["tests/test_api.py"],
    "readiness_probe": {"type": "http", "path": "/health"},
    "request_validation": {"type": "http", "path": "/generate"},
    "expected_outputs": ["HTTP 200 with generated text"],
    "required_runtime_env": ["PPU serving runtime and vendor-compatible vLLM/SGLang package"],
    "unresolved_source_groups": []
  },
  "custom_op_surface": {
    "custom_op_detected": true,
    "discovery_complete": true,
    "discovery_sources_checked": ["source", "bindings", "wrappers", "autograd", "aliases", "launch", "setup", "tests"],
    "searched_source_roots": ["src", "csrc", "tests"],
    "searched_source_paths": ["csrc/custom_alpha.cpp", "tests/test_custom_alpha.py"],
    "operator_families": ["custom_family_alpha", "custom_family_beta"],
    "fine_grained_operator_units": ["custom_family_alpha:signature_x", "custom_family_alpha:signature_y", "custom_family_beta:mode_z"],
    "discovered_operator_names": ["custom_family_alpha_signature_x", "custom_family_alpha_signature_y", "custom_family_beta_mode_z"],
    "source_evidence": ["csrc/custom_alpha.cpp:signature_x", "csrc/custom_alpha.cpp:signature_y", "csrc/custom_beta.cpp:mode_z"],
    "negative_evidence": ["grep under src/ and tests/ found no additional operator families"],
    "dynamic_loading_checks": ["import torch.ops.custom_family_alpha succeeded"],
    "build_load_checks": ["python setup.py build_ext --inplace completed"],
    "unresolved_source_groups": [],
    "out_of_scope_source_groups": [],
    "fine_grained_operator_unit_evidence": [
      {"unit_identity": "custom_family_alpha:signature_x", "source_evidence": ["csrc/custom_alpha.cpp:signature_x"]},
      {"unit_identity": "custom_family_alpha:signature_y", "source_evidence": ["csrc/custom_alpha.cpp:signature_y"]},
      {"unit_identity": "custom_family_beta:mode_z", "source_evidence": ["csrc/custom_beta.cpp:mode_z"]}
    ]
  }
}
```

## Field Semantics
- `project_dir`: normalized project root path.
- `dependencies`: short list of directly relevant dependencies.
- `cuda_detected`: whether CUDA-pattern code (including PPU-compatible `torch.cuda`) was found.
- `entry_script`: best relative or root-level script path candidate.
- `migration_route`: exactly one of `ordinary_cuda`, `custom_op`, `custom_op_with_variants`, `vllm_serving`, or `sglang_serving`.
- `serving_runtime_surface`: required for `vllm_serving` and `sglang_serving`; omit it or leave it absent for ordinary/custom-op non-serving projects.
- `custom_op_surface`: optional, only present when custom operators are discovered.
