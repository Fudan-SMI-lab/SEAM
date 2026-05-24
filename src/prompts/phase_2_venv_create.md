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
1. Probe the host Python before creating the venv: check whether `torch`, `torch_npu`, `torchvision`, `torchaudio`, and platform runtime packages are already importable and version-compatible for execution.
2. Create or reuse a virtual environment under `{project_dir}` in a predictable location such as `.venv`. When the host already provides a usable PyTorch/NPU stack, create the venv with `python -m venv --system-site-packages .venv` so that stack is reused without writing to global Python.
3. Inspect dependency files before installation and split packages into lightweight project dependencies versus heavyweight platform/runtime packages.
4. Upgrade base packaging tools inside the environment only when required.
5. Install only missing lightweight project dependencies into the venv. Use constraints or package exclusion flags as needed so dependency resolution does not replace the usable host PyTorch/NPU stack.
6. Install NPU-related dependencies needed for Ascend execution, including `torch_npu`, only when they are not already supplied by a compatible host stack.
7. Verify final imports using the venv interpreter for the reused or installed stack.
8. Record the final environment path, interpreter path, and a concise list of installed packages relevant to execution.

## Hard Rules
- Use domestic mirrors first for all installs, including sources such as 阿里云镜像, 清华镜像, or other reachable domestic mirrors.
- Do not use foreign package indexes unless domestic mirrors are unavailable; if a fallback is unavoidable, keep it explicit in intermediate notes.
- Do not install packages into the global Python environment.
- Do not reinstall or upgrade heavyweight platform packages that are already usable from the host stack, including `torch`, `torchvision`, `torchaudio`, `torch_npu`, CUDA/NVIDIA wheels, CANN/runtime packages, or accelerator runtime wheels.
- Avoid generic PyPI resolution that pulls CUDA PyTorch dependency wheels for NPU migrations. Prefer the existing host stack or CPU-base PyTorch plus the compatible NPU package constraints when installation is truly required.
- If a dependency file pins incompatible heavyweight platform packages, install the rest of the project dependencies locally and report the pin conflict instead of downloading unrelated CUDA wheels.
- Do not run broad `pip install -r requirements.txt` blindly when the file includes PyTorch/CUDA/NVIDIA/runtime pins; filter or constrain those packages first.
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
