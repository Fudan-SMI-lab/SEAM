# Phase 1 - Project Analysis

You are executing `{phase_name}` for `{project_dir}`.

## User-Provided Constraints (for awareness)
{user_constraints}

*Note: A detailed, project-specific constraint summary will be generated in Phase 1.5.*

## Goal
- Understand the project structure and likely execution path.
- Extract dependency signals relevant to CUDA to NPU migration.
- Identify the most likely entry script for training, inference, evaluation, or demo execution.
- When the source surface indicates custom operators, also discover the custom-op surface itself: fine-grained operator units, family/variant/signature identity, native symbols, kernel launch sites, public entry mappings, candidate public API routes, candidate framework integration routes, searched source roots/paths, negative evidence, dynamic loading/build/load checks, unresolved source groups, and the source evidence for each unit.

## Required Actions
1. Map the top-level layout of `{project_dir}` and identify source, config, scripts, and docs directories.
2. Inspect dependency declarations such as `requirements*.txt`, `environment*.yml`, `pyproject.toml`, `setup.py`, and shell launch scripts.
3. Detect CUDA-related code distribution by checking for patterns like `torch.cuda`, `.cuda()`, `device='cuda'`, `device=\"cuda\"`, `nccl`, custom CUDA extensions, or GPU-only launch flags.
4. Identify the best entry script candidate by combining README instructions, CLI files, launcher scripts, and common entry names such as `train.py`, `main.py`, `run.py`, `infer.py`, or `app.py`.
5. Keep the dependency list concise and useful; prefer direct project dependencies over transitive noise.
6. If custom operators are present, report the discovered fine-grained operator units, the exact source evidence that proves them, and the source-visible search/probe trail that led to discovery. Do not assume a fixed inventory shape across projects; surface whatever the project actually contains.

## Hard Rules
- Do not modify the project during this phase.
- Use README guidance and project files as evidence; do not invent an entry point.
- If multiple entry candidates exist, choose the most likely executable path and make the choice deterministic.
- If no strong CUDA evidence exists, set `cuda_detected` to `false`.
- If `custom_op_detected` is `true`, set `discovery_complete` to `true` only when every discovered unit is linked to source evidence, candidate public API route or framework integration route evidence, and the search/probe trail is source-visible.
- If `custom_op_detected` is `true` and `discovery_complete` is `true`, keep `unresolved_source_groups` empty.
- You may reason freely in your response, but end it with a single JSON object containing exactly the required keys for this phase. No other JSON objects should appear.
- If any package source or version lookup is needed, prefer domestic mirrors such as 阿里云镜像 or 清华镜像 over foreign mirrors.

## Output Format
Return exactly one JSON object with this shape:

```json
{
  "project_dir": "/path/to/project",
  "dependencies": ["torch", "numpy", "pyyaml"],
  "cuda_detected": true,
  "entry_script": "train.py",
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
      {"unit_identity": "custom_family_alpha:signature_x", "source_evidence": ["csrc/custom_alpha.cpp:signature_x"], "candidate_public_api_routes": ["pkg.ops.alpha_x"], "candidate_framework_integration_routes": ["pkg.layers.Alpha.forward"]},
      {"unit_identity": "custom_family_alpha:signature_y", "source_evidence": ["csrc/custom_alpha.cpp:signature_y"], "candidate_public_api_routes": ["pkg.ops.alpha_y"], "candidate_framework_integration_routes": ["pkg.autograd.AlphaY.apply"]},
      {"unit_identity": "custom_family_beta:mode_z", "source_evidence": ["csrc/custom_beta.cpp:mode_z"], "candidate_public_api_routes": ["pkg.ops.beta"], "candidate_framework_integration_routes": ["pkg.layers.Beta.forward"]}
    ]
  }
}
```

## Field Semantics
- `project_dir`: normalized project root path.
- `dependencies`: short list of directly relevant dependencies.
- `cuda_detected`: whether CUDA-specific code or dependencies were found.
- `entry_script`: best relative or root-level script path candidate.
- `custom_op_surface`: optional, only present when custom operators are discovered. Use it to describe the source-discovered fine-grained custom-op inventory shape, candidate public API/framework integration routes per unit, the source-visible search trail, negative evidence, source roots/paths searched, dynamic loading/build/load checks, unresolved source groups, and per-unit source evidence without hard-coding a fixed family or variant list.
