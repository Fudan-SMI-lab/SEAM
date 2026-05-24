# Phase 2 - Environment Selection and Setup (MUSA/MUXI, Base-Env-Aware)

You are executing `{phase_name}` for `{project_dir}`.

{execution_environment_context}

## Goal
- Select the Python environment that will actually run Phase 5.
- Prefer the container/base environment when it already provides `torch`, `torch_musa` or vendor-equivalent `torch_maca`, MUSA/MACA SDK/compiler/runtime, and compatible accelerator packages.
- Install only missing project dependencies without overwriting vendor MUSA packages.

## Migration Constraints
{constraint_summary}

## Required Actions
1. Inspect target execution environment Python interpreters, `torch`, `torch_musa`, `torch.musa`, vendor-equivalent `torch_maca`/MACA signals when present, MUSA/MACA SDK paths, compiler (`musacc`, `mxcc`, or vendor compiler), and runtime libraries.
2. Prefer `python3.10` when available. Use the base environment if it already contains the MUSA stack.
3. Create a project-local `.venv` only when isolation is required and it can still see vendor MUSA packages; do not hide or replace the MUSA stack.
4. Before installing dependencies, run a dry-run or inspect dependency resolution. Use `--no-deps` when needed to avoid replacing `torch`, `torch_musa`, accelerator kernels, compiler bindings, or runtime wheels.
5. Use vendor/offline/internal indexes for MUSA packages. Public PyPI is acceptable only for non-accelerator pure-Python packages after checking it will not replace vendor packages.

## Hard Rules
- Do not install host packages.
- Do not install CPU-only `torch` or replace vendor `torch`/`torch_musa`.
- Do not add CPU fallback packages or CPU fallback code.
- End with exactly one JSON object containing `venv_path`, `python_path`, and `installed_packages`.

## Output Format
```json
{
  "env_type": "base_env",
  "venv_path": "/usr/local",
  "python_path": "python3.10",
  "installed_packages": ["numpy==1.26.4"],
  "musa_stack": {
    "torch_musa_available": true,
    "torch_maca_available": false,
    "musa_sdk_available": true,
    "maca_sdk_available": false,
    "musa_compiler_available": true
  }
}
```
