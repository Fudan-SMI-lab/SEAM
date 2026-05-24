# Phase 1 - Project Analysis

You are executing `{phase_name}` for `{project_dir}`.

## User-Provided Constraints (for awareness)
{user_constraints}

*Note: A detailed, project-specific constraint summary will be generated in Phase 1.5.*

## Serving Route Detection
- Use `vllm_serving` only when project files show a vLLM serving runtime surface, and `sglang_serving` only when project files show an SGLang serving runtime surface. Do not infer these routes from package availability alone.
- For vLLM/SGLang routes, include `serving_runtime_surface` with `serving_framework`, `detection_complete`, `launch_command`, `launch_evidence`, `project_demo_or_test_evidence`, `project_test_files`, `readiness_probe`, `request_validation`, `expected_outputs`, `required_runtime_env`, and `unresolved_source_groups`.
- Serving route classification is fail-closed: framework must match the route, launch evidence must cite project launch/demo/API/test files, `project_demo_or_test_evidence` and `project_test_files` must be non-empty, and `unresolved_source_groups` must be empty when `detection_complete=true`.
- Project-provided serving demos/tests may be any real project assets such as demo scripts, API examples, integration tests, README commands, request fixtures, or validation scripts. Do not hard-code known example projects.

## Goal
- Understand the project structure and likely execution path.
- Classify the migration route as exactly one of `ordinary_cuda`, `custom_op`, `custom_op_with_variants`, `vllm_serving`, or `sglang_serving`.
- Extract dependency signals relevant to CUDA to NPU migration.
- Identify the most likely entry script for training, inference, evaluation, or demo execution.
- When the source surface indicates custom operators, also discover the custom-op surface itself: fine-grained operator units, family/variant/signature identity, native symbols, kernel launch sites, public entry mappings, candidate public API routes, candidate framework integration routes, searched source roots/paths, negative evidence, dynamic loading/build/load checks, unresolved source groups, and the source evidence for each unit.
- Treat public/native boundary custom operators as first-class fine-grained units when they are declared or defined in C/C++/CUDA sources and participate in the GPU execution path. This includes true exported `extern "C"`/macro-generated/native `*_cuda`/`*_gpu` units, storage/compression/memory helpers, registration functions, and framework bridge symbols when they are independently source-required migration units. Do not limit the inventory to top-level Python-visible APIs.
- Do not make implementation details into `fine_grained_operator_units`: raw CUDA kernels, kernel launch wrappers, check macros, thread/block helper functions, block-size specializations, runtime launch heuristics, and performance-tuning template specializations belong in `kernel_launch_sites`, `source_evidence`, or `fine_grained_operator_unit_evidence` for the owning public/native boundary operator row.
- When macro/template/build logic creates separate source-required semantic operator units across axes such as dimension, accuracy, dtype (only when dtype creates separate source-required units), layout, or mode, report that as optional expanded variant metadata instead of collapsing it into one parameterized unit. Use generic fields under `custom_op_surface`: `variant_axes_detected`, `variant_axes`, `expanded_operator_variants`, and `expanded_operator_instances_count`. Each expanded variant must have its own concrete per-axis `unit_identity`, `base_unit_identity` or `source_unit_identity`, atomic `axis_values` or `variant_axes`, `source_evidence`, and candidate public API or framework integration routes. Do not invent axes when source evidence does not define them. If source evidence enumerates multiple semantic target values, include every enumerated target value in `variant_axes` and cover every source-required target combination/value in `expanded_operator_variants`; do not list only a representative sample combination. Never use brace/pipe collapsed alternatives such as `{ndim=1|2|3}`, `ndim=1|2`, `float|double`, or combined values inside `variant_axes`, `axis_values`, `fine_grained_operator_units`, or `expanded_operator_variants`; enumerate one row per concrete axis combination. Implementation details must not activate expanded variant mode: block size, grid/thread heuristics, launch wrappers, check macros, runtime dispatch coverage, and performance-tuning template specializations are regular implementation details of the owning public/native operator, not expanded operator variants. CPU, backend/reference/baseline, host, ctypes, symbol-loader, and source-loader tokens may be recorded as evidence or baseline context, but they are not target Ascend OPP/custom-op expanded variants.

## Required Actions
1. Map the top-level layout of `{project_dir}` and identify source, config, scripts, and docs directories.
2. Inspect dependency declarations such as `requirements*.txt`, `environment*.yml`, `pyproject.toml`, `setup.py`, and shell launch scripts.
3. Detect CUDA-related code distribution by checking for patterns like `torch.cuda`, `.cuda()`, `device='cuda'`, `device=\"cuda\"`, `nccl`, custom CUDA extensions, or GPU-only launch flags.
4. Identify the best entry script candidate by combining README instructions, CLI files, launcher scripts, and common entry names such as `train.py`, `main.py`, `run.py`, `infer.py`, or `app.py`.
5. Keep the dependency list concise and useful; prefer direct project dependencies over transitive noise.
6. If custom operators are present, report the discovered fine-grained operator units, the exact source evidence that proves them, and the source-visible search/probe trail that led to discovery. Do not assume a fixed inventory shape across projects; surface whatever the project actually contains.
7. For C/C++/CUDA custom-op projects, enumerate every source-discovered public/native boundary unit needed for migration preparation, including CUDA/native helper exports that are independently source-required and only reached through framework integration routes. Family-only rows are invalid when the source exposes multiple public/native boundary functions or helper symbols. Raw kernels, kernel wrappers, check macros, block/thread helpers, and tuning specializations are evidence for those rows, not rows themselves.

## Hard Rules
- Do not modify the project during this phase.
- Do not call `task()`, launch background/sub-agent work, create todos, or wait for background task notifications in this phase. Inspect files directly and return the phase JSON in this same response.
- Use README guidance and project files as evidence; do not invent an entry point.
- If multiple entry candidates exist, choose the most likely executable path and make the choice deterministic.
- If no strong CUDA evidence exists, set `cuda_detected` to `false`.
- If `custom_op_detected` is `true`, set `discovery_complete` to `true` only when every discovered unit is linked to source evidence, candidate public API route or framework integration route evidence, and the search/probe trail is source-visible.
- If `custom_op_detected` is `true` and `discovery_complete` is `true`, keep `unresolved_source_groups` empty.
- If C/C++/CUDA sources contain native CUDA/GPU exports, macro-generated functions, or independently source-required helper units that are used by the GPU/custom-op path, each such public/native boundary unit must appear in `fine_grained_operator_units`, `native_operator_symbols`, source evidence, and per-unit evidence. Missing helper exports make discovery incomplete. Do not list raw kernels, launch wrappers, check macros, block/thread helper functions, or tuning specializations as units; attach them to the owning unit as launch/source evidence.
- Expanded variant identities and axis values must be concrete and atomic: no `{...}`, no `|` alternative lists, no comma-combined axis assignments, and no values like `ndim=1d|2d|3d` or `float|double`. If an axis has multiple values, `expanded_operator_variants` must contain separate rows for the concrete source-required combinations, not one collapsed row per base unit; do not invent a full per-base Cartesian product unless the source explicitly requires every combination. When source loaders, generated-symbol templates, macro expansion, or build loops construct one native symbol per base unit across source-required axes such as dimension/accuracy/dtype, that is explicit per-base combination evidence: enumerate the full concrete combination set for each affected `base_unit_identity`, not just representative rows that cover each axis value somewhere globally.
- Do not silently downgrade source-required semantic/macro/template variants to only base units. If source evidence or generated native symbols mention semantic axes such as `$NDIM`, `$ACCURACY`, `$DTYPE`, generated backend symbol templates, or source enumeration of dimension/accuracy/dtype/layout/mode/device values, set `variant_axes_detected` to `true` and enumerate concrete per-axis `expanded_operator_variants` for target custom-op values only. When evidence says values such as `ndim 1, 2, 3`, `accuracy 2, 4, 6, 8`, or `dtype float and double`, `variant_axes` must include all those values and `expanded_operator_variants` must observe all source-required target values/combinations, not just the first or most common sample. Do not include `cpu`, `torch_cpu`, `python_cpu`, `reference`, `baseline`, `host`, `ctypes`, or `symbols` as target values in axis-like fields such as `device`, `backend`, `reference`, `baseline`, or `comparison`; mention them only in source evidence, baseline evidence, loading checks, or negative evidence.
- Do not hide variant evidence in the search trail. If any file in `searched_source_paths` or the user-provided requirements shows source-required semantic generated axes, generated backend symbol templates, or loops/lists over `ndim`, `accuracy`, `dtype`, `layout`, `mode`, or target device values, the output must use the custom-op+variant fields above. Requirements may impose variant obligations, but source files still provide the operator and axis evidence; do not put requirements markers into `discovery_sources_checked`.
- Do not include external or out-of-scope benchmark operators in target `fine_grained_operator_units`; mention them only as `out_of_scope_source_groups` or negative/evidence notes unless project-local source evidence proves they are source-required target units.
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
    "native_operator_symbols": ["custom_family_alpha_signature_x", "custom_family_alpha_signature_y", "custom_family_beta_mode_z"],
    "kernel_launch_sites": ["csrc/custom_alpha.cu:alpha_x_kernel<<<...>>>", "csrc/custom_beta.cu:beta_z_kernel<<<...>>>", "csrc/custom_helpers.cu:helper_cuda launches helper_kernel<<<...>>>"] ,
    "source_evidence": ["csrc/custom_alpha.cpp:signature_x", "csrc/custom_alpha.cpp:signature_y", "csrc/custom_beta.cpp:mode_z"],
    "negative_evidence": ["grep under src/ and tests/ found no additional operator families"],
    "dynamic_loading_checks": ["import torch.ops.custom_family_alpha succeeded"],
    "build_load_checks": ["python setup.py build_ext --inplace completed"],
    "unresolved_source_groups": [],
    "out_of_scope_source_groups": [],
    "variant_axes_detected": true,
    "variant_axes": {"axis_name": ["value_a", "value_b"]},
    "expanded_operator_variants": [
      {"unit_identity": "custom_family_alpha:signature_x:axis_name=value_a", "base_unit_identity": "custom_family_alpha:signature_x", "axis_values": {"axis_name": "value_a"}, "source_evidence": ["csrc/custom_alpha.cu:macro expansion axis_name=value_a"], "candidate_public_api_routes": ["pkg.ops.alpha_x"], "candidate_framework_integration_routes": ["pkg.layers.Alpha.forward"]}
    ],
    "expanded_operator_instances_count": 1,
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
- `custom_op_surface`: optional, only present when custom operators are discovered. Use it to describe the source-discovered fine-grained custom-op inventory shape, candidate public API/framework integration routes per unit, native CUDA/GPU/helper symbols, kernel launch sites, the source-visible search trail, negative evidence, source roots/paths searched, dynamic loading/build/load checks, unresolved source groups, and per-unit source evidence without hard-coding a fixed family or variant list.
