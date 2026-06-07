# Phase 0 - Environment Detection (PPU)

You are executing `{phase_name}` for the target project at `{project_dir}`.

## Goal
- Detect whether the host environment exposes PPU devices through CUDA-compatible APIs.
- In this environment, PPU devices are accessible via **`torch.cuda`**, not `torch.npu` or `torch_npu`.
- Read the project README before making conclusions.
- Report a minimal machine-readable result for the migration workflow.

## Required Actions
1. Inspect README files under `{project_dir}` first, especially `README*`, setup notes, and environment instructions.
2. Detect PPU signals:
   - `torch.cuda.is_available()` returning `True` on a PPU-equipped host.
   - Device names like `PPU-ZW810` in CUDA device queries or `nvidia-smi`-compatible probes adapted for PPU.
   - Environment variables or configuration that indicate PPU vendor toolchains.
3. Detect CUDA signals from tools such as actual NVIDIA GPUs, `nvidia-smi` showing NVIDIA hardware, or project instructions that clearly depend on NVIDIA-specific CUDA features (not just generic `torch.cuda` API).
4. **PPU Detection** (primary path):
   a. Check `python3 -c "import torch; print(torch.cuda.is_available()); print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'none')"` — if `True` and device name contains `PPU` or similar PPU identifier, this is PPU.
   b. Check for PPU-specific environment variables or vendor package presence.
5. Determine the active Python version from the system runtime.
6. Prefer observable facts over assumptions. If evidence conflicts, explain the tie-break in brief working notes, but keep the final answer schema-only.

## Hard Rules
- Stay inside `{project_dir}` and its direct environment context.
- Read the README before returning a result.
- Do not hallucinate hardware that you cannot verify.
- **CRITICAL: If PPU is detected, `platform` MUST be `"ppu"`, NOT `"npu"`.** PPU environments expose `torch.cuda` APIs — do not confuse this with actual NVIDIA GPU + CUDA environments.
- If both PPU and NVIDIA GPU signals exist, prefer `ppu` only when PPU-specific evidence (device name `PPU-*`, vendor toolchain) is directly observable.
- You may reason freely in your response, but end it with a single JSON object containing exactly the required keys for this phase. No other JSON objects should appear.
- If any package index lookup is needed, prefer PPU vendor index, PTG/t-head artifactory, or offline PPU wheelhouse. Public PyPI installs for `torch`, `vllm`, `sglang`, `flash_attn`, `triton`, or `xgrammar` can contaminate the PPU environment.

## IMPORTANT INSTRUCTIONS
- DO NOT explore or search project code. Your ONLY task is hardware/environment detection.
- Keep total tool calls under 15.
- Execute the required commands, then IMMEDIATELY respond with the JSON result.

## Output Format
Return exactly one JSON object with this shape:

```json
{
  "platform": "ppu",
  "ppu_detected": true,
  "cuda_api_available": true,
  "python_version": "3.10.12",
  "device_name": "PPU-ZW810",
  "npu_detected": false,
  "cann_version": "n/a",
  "ascendc_available": false,
  "driver_version": "not_applicable"
}
```

## Field Semantics
- `platform`: one of `ppu`, `cuda`, or `npu` only (never `cpu`). Use `ppu` when PPU is detected via CUDA-compatible APIs, `cuda` for actual NVIDIA GPU, or `npu` only for legacy Ascend/NPU outputs (not expected in this PPU workflow).
- `ppu_detected`: boolean based on direct environment evidence (device name, vendor env vars, PPU toolchain).
- `cuda_api_available`: whether `torch.cuda.is_available()` returns True. For PPU this is expected to be True.
- `python_version`: concrete interpreter version string.
- `device_name`: PPU device identifier such as `"PPU-ZW810"`. Set to `"not_found"` if not determinable.
- `npu_detected`: always `false` for PPU (compatibility field).
- `cann_version`: `"n/a"` for PPU (compatibility field; PPU does not use CANN toolchain).
- `ascendc_available`: always `false` for PPU (compatibility field; PPU does not use AscendC).
- `driver_version`: `"not_applicable"` for PPU (compatibility field).
