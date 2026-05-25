# Phase 1 - Project Analysis (PPU, Normal Entry 057 Experiment)

You are executing `{phase_name}` for `{project_dir}`.

## User-Provided Constraints (for awareness)
{user_constraints}

*Note: A detailed, project-specific constraint summary will be generated in Phase 1.5.*

## Goal
- Understand the project structure and likely execution path.
- Extract dependency signals relevant to CUDA/PPU migration.
- Identify the most likely entry script for training, inference, evaluation, or demo execution.
- This is a normal application demo route. The custom-op contract injection and final-gate route are disabled by workflow configuration. Analyze the project surface naturally and report findings truthfully.

## Required Actions
1. Map the top-level layout of `{project_dir}` and identify source, config, scripts, and docs directories.
2. Inspect dependency declarations such as `requirements*.txt`, `environment*.yml`, `pyproject.toml`, `setup.py`, and shell launch scripts.
3. Detect CUDA/PPU-related code distribution by checking for patterns like `torch.cuda`, `.cuda()`, `device='cuda'`, `device="cuda"`, `nccl`, custom CUDA extensions, or GPU-only launch flags.
4. Identify the best entry script candidate by combining README guidance, CLI files, launcher scripts, and common entry names such as `train.py`, `main.py`, `run.py`, `infer.py`, `app.py`, or demo scripts like `057_example_fwi.py`.
5. Keep the dependency list concise and useful; prefer direct project dependencies over transitive noise.
6. Include `matplotlib`, `scikit-image`, and `lpips` in dependencies if the entry script references them.

## Hard Rules
- Do not modify the project during this phase.
- Use README guidance and project files as evidence; do not invent an entry point.
- If multiple entry candidates exist, choose the most likely executable path and make the choice deterministic.
- If no strong CUDA evidence exists, set `cuda_detected` to `false`.
- Follow ordinary project analysis semantics for custom/native operator discovery. Report findings truthfully — if custom or native operators exist in the project surface, set `custom_op_detected` to `true` and populate discovery collections with the operators found. If none are found, set it to `false`.
- Set `discovery_complete` to `true` in the custom_op_surface block.
- The custom-op contract injection and final-gate route are disabled at the framework level for this workflow — you do not need to suppress custom-op findings.
- You may reason freely in your response, but end it with a single JSON object containing exactly the required keys for this phase. No other JSON objects should appear.
- If any package source or version lookup is needed, prefer PPU vendor index, PTG/t-head artifactory, or offline PPU wheelhouse. Public PyPI installs for key packages can contaminate the PPU environment.

## Output Format
Return exactly one JSON object with this shape:

```json
{
  "project_dir": "/path/to/project",
  "dependencies": ["torch", "numpy", "matplotlib", "scikit-image", "lpips", "deepwave"],
  "cuda_detected": true,
  "entry_script": "057_example_fwi.py",
  "custom_op_surface": {
    "custom_op_detected": false,
    "discovery_complete": true,
    "discovery_sources_checked": [],
    "searched_source_roots": [],
    "searched_source_paths": [],
    "operator_families": [],
    "fine_grained_operator_units": [],
    "discovered_operator_names": [],
    "source_evidence": [],
    "negative_evidence": [],
    "dynamic_loading_checks": [],
    "build_load_checks": [],
    "unresolved_source_groups": [],
    "out_of_scope_source_groups": [],
    "fine_grained_operator_unit_evidence": []
  }
}
```

## Field Semantics
- `project_dir`: normalized project root path.
- `dependencies`: short list of directly relevant dependencies.
- `cuda_detected`: whether CUDA-pattern code (including PPU-compatible `torch.cuda`) was found.
- `entry_script`: best relative or root-level script path candidate.
- `custom_op_surface`: **MUST** be present with `discovery_complete: true`. Populate all collection arrays with actual discovery results. The framework disables custom-op contract injection via global configuration, so truthful reporting does not trigger the custom-op route.
