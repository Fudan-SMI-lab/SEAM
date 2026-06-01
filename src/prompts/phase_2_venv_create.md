# Phase 2 - Virtual Environment Creation

You are executing `{phase_name}` for `{project_dir}`.

## Goal
- Create an isolated Python environment for NPU adaptation work.
- Install the required project dependencies plus `torch_npu` and related NPU packages.
- Use domestic mirror sources by default.

## Migration Constraints (from Phase 1.5)
{constraint_summary}

*These constraints are binding. Keep them in mind when setting up the environment.*

## Required Actions
1. Create or reuse a virtual environment under `{project_dir}` in a predictable location such as `.venv`.
2. Upgrade base packaging tools inside the environment only when required.
3. Install project dependencies from the discovered dependency files.
4. Install NPU-related dependencies needed for Ascend execution, including `torch_npu` when compatible with the Python and PyTorch versions.
5. Record the final environment path, interpreter path, and a concise list of installed packages relevant to execution.

## CRITICAL - Scope Boundary
- **DO NOT** create, edit, or generate ANY Python scripts, validation files, or documentation files.
- **DO NOT** analyze project source code beyond what is needed to identify and install dependencies.
- **DO NOT** create migration scripts, test scripts, custom operator validation scripts, or serving wrapper scripts.
- **DO NOT** modify any existing project files.
- Your ONLY mission: create the virtual environment, install all required dependencies, and report the result.
- Script creation belongs to later phases (Phase 3, Phase 5).  You are Phase 2 — venv only.

## Hard Rules
- Use domestic mirrors first for all installs, including sources such as 阿里云镜像, 清华镜像, or other reachable domestic mirrors.
- Do not use foreign package indexes unless domestic mirrors are unavailable; if a fallback is unavoidable, keep it explicit in intermediate notes.
- Do not install packages into the global Python environment.
- The virtual environment MUST be strictly isolated from system site-packages. When creating the venv, always use `--without-pip` (or the platform default) and verify that `include-system-site-packages = false` in `pyvenv.cfg`. Never pass `--system-site-packages` to `python -m venv`. If the tool reports `include-system-site-packages = true`, recreate the venv with isolation enforced.
- If the migration route is `vllm_serving`, install `vllm` into the project venv. If the migration route is `sglang_serving`, install `sglang` into the project venv. These serving frameworks are NOT optional for serving routes — without them the Phase 5 serving gate will fail because the generated validation script runs inside this venv. The current migration route for this project is `{migration_route}`.
- Prefer reproducible commands and pinned versions when the project already specifies them.
- If compatibility blocks `torch_npu` installation, stop and report the blocker rather than fabricating success.
- You may reason freely in your response, but end it with a single JSON object containing exactly the required keys for this phase. No other JSON objects should appear.

## Output Format
Return exactly one JSON object with this shape:

```json
{
  "venv_path": "/path/to/project/.venv",
  "python_path": "/path/to/project/.venv/bin/python",
  "installed_packages": ["torch==2.1.0", "torch_npu==2.1.0", "numpy==1.26.4"]
}
```

## Field Semantics
- `venv_path`: absolute path to the created or reused virtual environment.
- `python_path`: absolute path to the Python interpreter inside that environment.
- `installed_packages`: concise package list that reflects the usable execution environment.
