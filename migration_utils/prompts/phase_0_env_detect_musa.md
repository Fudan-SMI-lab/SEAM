# Phase 0 - Environment Detection (MUSA/MUXI)

You are executing `{phase_name}` for the target project at `{project_dir}`.

## Goal
- Detect whether the target execution environment exposes MUSA/MUXI accelerator devices. If this MUXI deployment exposes MACA/MetaX or `torch_maca`/vendor-compatible APIs instead of `torch_musa`, report the observed stack accurately rather than inventing `torch_musa`.
- Prefer `python3.10` and the target container/base environment described by the execution backend.
- Report observable facts only; do not infer a working MUSA stack from CUDA source code alone.

## Required Actions
1. Read README/setup notes under `{project_dir}` before returning a result.
2. Probe Python and package availability with commands such as `python3.10 --version` and `python3.10 -c "import torch; print(torch.__version__)"`.
3. Probe MUSA runtime with `python3.10 -c "import torch, torch_musa; print(hasattr(torch, 'musa')); print(torch.musa.is_available() if hasattr(torch, 'musa') else False); print(torch.musa.device_count() if hasattr(torch, 'musa') else 0)"` when possible.
4. Probe SDK/compiler/runtime facts without installing host packages: `command -v musacc`, `command -v mcc`, `command -v mxcc`, `ls /usr/local/musa*`, `ls /usr/local/maca*`, and candidate device nodes such as `/dev/musa*`, `/dev/mxcd`, `/dev/dri/renderD*`, or vendor-documented nodes.
5. Record whether `torch_musa` or vendor-equivalent packages, MUSA/MACA SDK headers/libraries, compiler, and runtime libraries are present.

## Hard Rules
- Do not modify the project or install anything.
- Do not claim MUSA is available unless a device/runtime/package signal is directly observed.
- Do not report CPU as the target platform.
- End with exactly one JSON object and no other JSON.

## Output Format
```json
{
  "platform": "musa",
  "musa_detected": true,
  "accelerator_detected": true,
  "musa_api_available": true,
  "torch_musa_available": true,
  "torch_maca_available": false,
  "python_version": "3.10.12",
  "device_count": 1,
  "device_name": "MUSA device name or not_found",
  "musa_sdk_available": true,
  "maca_sdk_available": false,
  "musa_compiler_available": true,
  "musa_runtime_libraries": ["libmusa.so"],
  "device_nodes": ["/dev/musa0"],
  "driver_version": "observed_or_unknown"
}
```
