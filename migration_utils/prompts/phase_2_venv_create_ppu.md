# Phase 2 - Virtual Environment Creation (PPU)

You are executing `{phase_name}` for `{project_dir}`.

## Goal
- Create an isolated Python environment for PPU adaptation work.
- Install the required project dependencies plus any PPU-compatible packages.
- Use PPU vendor package index or offline wheelhouse by default.

## Migration Constraints (from Phase 1.5)
{constraint_summary}

*These constraints are binding. Keep them in mind when setting up the environment.*

## Required Actions
1. Prefer the container base Python / base environment for all installs. Create a project-local `.venv` only when explicitly required (e.g., the project demands isolation that the base environment cannot provide). If reusing an existing venv or the base environment, record its path.
2. Upgrade base packaging tools inside the environment only when required.
3. Install project dependencies from the discovered dependency files.
4. Install PPU-compatible dependencies needed for execution. The PPU environment already provides `torch` with CUDA-compatible APIs — do NOT install `torch_npu` from public PyPI, as it can overwrite the PPU-provided `torch`.
5. Record the final environment path, interpreter path, and a concise list of installed packages relevant to execution.

## Hard Rules
- Use PPU vendor index, PTG/t-head artifactory, or offline PPU wheelhouse first for all installs.
- Do NOT install packages into the global Python environment.
- **Do NOT install `torch_npu` or any package that replaces the PPU-provided `torch`.**
- **Do NOT install `torch`, `vllm`, `sglang`, `sgl-kernel`, `flash_attn`, `flashinfer-python`, `deep_gemm`, `deep_ep`, `flash_mla`, `triton`, `xgrammar`, or `torchao` from public PyPI** unless explicitly confirmed as safe and pinned to vendor-compatible PPU versions. Public PyPI installs of these packages can overwrite PPU-vendor-built wheels.
- Prefer reproducible commands and pinned versions when the project already specifies them.
- Use dry-run (`pip install --dry-run`) before installing packages to verify they won't contaminate existing PPU packages.
- You may reason freely in your response, but end it with a single JSON object containing exactly the required keys for this phase. No other JSON objects should appear.

## Output Format
Return exactly one JSON object with this shape:

```json
{
  "venv_path": "/path/to/project/.venv",
  "python_path": "/path/to/project/.venv/bin/python",
  "installed_packages": ["torch==2.x.x+ppu", "numpy==1.26.4"]
}
```

## Field Semantics
- `venv_path`: absolute path to the active Python environment (container base env, reused venv, or newly created `.venv`).
- `python_path`: absolute path to the Python interpreter in that environment.
- `installed_packages`: concise package list that reflects the usable execution environment.
