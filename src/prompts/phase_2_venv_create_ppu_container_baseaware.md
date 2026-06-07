# Phase 2 - Environment Selection and Setup (PPU, Base-Env-Aware)

You are executing `{phase_name}` for `{project_dir}`.

{execution_environment_context}

## Goal
- Decide whether the target runtime's base Python environment or a project-local virtual environment best fits the migration workload.
- Install the required project dependencies plus any PPU-compatible packages in the selected environment.
- Use PPU vendor package index or offline wheelhouse by default.

## Migration Constraints (from Phase 1.5)
{constraint_summary}

*These constraints are binding. Keep them in mind when setting up the environment.*

## Decision Process (CRITICAL)

Before running any commands, inspect the target runtime to gather facts:

1. **Inspect the target runtime**:
   - When the backend is a framework-created container, examine the container base environment (Python interpreters, versions, preinstalled packages — especially `torch`, `vllm`, and related libraries).
   - When the backend is local/host, examine the host base environment directly. File tools and the target runtime both observe the same local environment.
   - Decision criterion: determine from evidence whether the base environment already satisfies the project's requirements.

2. **Check the project for an existing venv**: look for `.venv`, `venv/`, or similar directories under `{project_dir}`. If one exists, inspect its Python version and installed packages.

3. **Choose based on evidence**:
   - If the base environment has a modern Python (3.10+) and the preinstalled PPU stack already satisfies or nearly satisfies the project's dependency tree, **prefer the base environment**. Install only the missing pieces.
   - If the project demands strict isolation, conflicting dependency versions, or a different Python version than what the base environment provides, **create or reuse a project-local venv**.
   - Do not default to creating `.venv` when the base environment works. The `.venv` convention exists and is acceptable when justified, but the base environment is the first-class option here.
   - **`venv_path` is a legacy schema name, not a directive to create a virtual environment.** For `base_env`, set `venv_path` to the base environment prefix (e.g. `/usr/local`) and `python_path` to the verified base interpreter. Only create a `.venv` if strict isolation, conflicting dependencies, or different Python requirements actually demand it.

## Required Actions

1. Inspect available Python interpreters inside the target execution environment (e.g. `command -v python3`, `python3 --version`, `python3 -m pip list`) and any existing venvs under `{project_dir}`.
2. Decide between the base environment and a project-local venv. Record your reasoning briefly.
3. If using the base environment:
   - Use the interpreter that has (or can safely gain) the required PPU stack, as discovered above. Record its absolute path or the command (e.g. `python3`) if it is on PATH.
   - Run `pip install --dry-run` before any install to verify packages won't overwrite PPU-provided wheels.
4. If creating/using a project-local venv:
   - Create it or activate the existing one.
   - Record its path.
5. Install project dependencies from discovered dependency files.
6. Install PPU-compatible dependencies needed for execution. The PPU environment already provides `torch` with CUDA-compatible APIs, do NOT install `torch_npu` from public PyPI, as it can overwrite the PPU-provided `torch`.
7. Record the final environment root as `venv_path`, interpreter path, and a concise package list.

**Important for container mode**: File tools (read, grep, etc.) observe the host filesystem. The `python3` you find via file tooling on the host reflects the *host* Python, not necessarily the container's. Use the Execution Environment Context above and container probe facts as your authoritative source for the target execution environment.

## Hard Rules
- Do not ask the user or call the `question` tool. If a decision is required, choose the safest autonomous option that advances validation evidence.
- Use PPU vendor index, PTG/t-head artifactory, or offline PPU wheelhouse first for all installs.
- **Do NOT install `torch_npu` or any package that replaces the PPU-provided `torch`.**
- **Do NOT install `torch`, `vllm`, `sglang`, `sgl-kernel`, `flash_attn`, `flashinfer-python`, `deep_gemm`, `deep_ep`, `flash_mla`, `triton`, `xgrammar`, or `torchao` from public PyPI** unless explicitly confirmed as safe and pinned to vendor-compatible PPU versions. Public PyPI installs of these packages can overwrite PPU-vendor-built wheels.
- Use dry-run (`pip install --dry-run`) before installing packages to verify they won't contaminate existing PPU packages.
- Prefer reproducible commands and pinned versions when the project already specifies them.
- You may reason freely in your response, but end it with a single JSON object containing the required keys (`venv_path`, `python_path`, `installed_packages`). You may also include the optional `env_type` field. No other JSON objects should appear.

## Output Format
Return exactly one JSON object with this shape:

When using the base environment:

```json
{
  "env_type": "base_env",
  "venv_path": "/usr",
  "python_path": "/usr/bin/python3",
  "installed_packages": ["numpy==1.26.4"]
}
```

When a project-local venv is justified:

```json
{
  "env_type": "venv",
  "venv_path": "/workspace/project/.venv",
  "python_path": "/workspace/project/.venv/bin/python",
  "installed_packages": ["numpy==1.26.4"]
}
```

You may additionally include `"env_type": "base_env"` or `"env_type": "venv"` so later processing can read your choice in this same session, but `venv_path`, `python_path`, and `installed_packages` are the required keys.

## Field Semantics
- `venv_path`: absolute path to the active Python environment root. For a base environment, use the base prefix (e.g. `/usr/local`). For a project-local virtual environment, use the `.venv` directory path.
- `python_path`: absolute path to (or PATH command for) the Python interpreter callable in the **target runtime**. For container backends, this is the interpreter path or command valid inside the container, not necessarily the host Python discovered by file tools.
- `installed_packages`: concise package list that reflects the usable execution environment.
- `env_type` (optional): `"base_env"` or `"venv"`. Present so later processing can read your choice; the three keys above are the required ones.
