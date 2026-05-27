# Phase 2 - Environment Selection (MUXI, Base-Env-Aware)

You are executing `{phase_name}` for `{project_dir}`.

{execution_environment_context}

## Target Runtime Container Context
- Execution backend mode: `{execution_backend_mode}`
- Container name or ID: `{container_name_or_id}`
- Container workdir: `{container_workdir}`
- Host project directory: `{host_project_dir}`
- Container project directory: `{container_project_dir}`
- Read-only probe command prefix: `{container_probe_command_prefix}`

When `execution_backend_mode` is `container`, choose an environment valid inside that container. Use `container_env_facts` as a starting point, then prefer safe read-only verification inside the container when the facts are incomplete or surprising. Host-side file tool command output is not authoritative for `python_path`, package availability, torch location, accelerator runtime, or device visibility. If container evidence is unavailable after safe probing, report the container runtime as unknown or blocked rather than filling `python_path` with a host-only interpreter.

## Prior Phase Context
{previous_outputs}

## Migration Constraints
{constraint_summary}

## Goal
Choose the Python environment that the target runtime should actually use. Prefer the container/base environment or local base environment when it already contains vendor torch and the accelerator runtime.

## Runtime Mode Rules
- Container mode: choose an interpreter path or PATH command verified inside the container. Do not report a host-only interpreter as `python_path`.
- Local mode: choose an interpreter path valid on the host/local runtime. Do not mention `/workspace` unless that path exists locally.
- `venv_path` is a legacy schema field. It records the selected environment root; it is not an instruction to create `.venv`.

## Decision Process
1. Inspect the target runtime first: Python interpreters, `torch`, `torch.cuda`, `torch_musa`, `torch.musa`, `torch_maca`, MACA/MetaX packages, SDK/compiler/runtime libraries, and package locations.
2. If base env has compatible Python plus vendor torch/runtime, select base env.
3. Create or reuse project `.venv` only for a real isolation/version conflict and only when it can still access the vendor accelerator stack.
4. For conda vendor envs, do not assume Python venv `include-system-site-packages` exposes conda packages.
5. If installing pure-Python project dependencies is needed, dry-run or inspect resolution first and use `--no-deps` when it avoids replacing vendor packages.

## Package Safety
- Do not install on the host when the execution backend is a container.
- Do not install CPU-only torch or replace vendor torch/runtime packages.
- Do not install these from public PyPI unless explicitly safe and pinned to the vendor-compatible build: `torch`, `torchvision`, `torchaudio`, `torch_musa`, `torch_maca`, `vllm`, `vllm-metax`, `sglang`, `triton`, `flash_attn`, `flashinfer-python`, vendor runtime/compiler/kernel packages.
- Prefer vendor image packages, vendor/offline wheelhouses, or internal indexes for accelerator packages.
- CPU fallback packages or CPU fallback code are not valid fixes.

## Hard Rules
- End with exactly one JSON object containing at least `venv_path`, `python_path`, and `installed_packages`.
- `python_path` must be directly executable in the target runtime.
- Do not claim installed packages were installed unless the install actually happened.

## Output Format
```json
{
  "env_type": "base_env",
  "venv_path": "observed_container_env_root",
  "python_path": "observed_container_python",
  "installed_packages": ["torch==observed_vendor_build"],
  "vendor_stack": {
    "observed_vendor": "maca_metax",
    "api_mode": "cuda_compatible",
    "torch_available": true,
    "torch_location": "observed_container_torch_location",
    "torch_cuda_available": true,
    "torch_musa_available": false,
    "torch_maca_available": false,
    "musa_sdk_available": false,
    "maca_sdk_available": true,
    "vendor_runtime_preserved": true
  },
  "decision_reason": "base env already contains vendor torch and runtime"
}
```
