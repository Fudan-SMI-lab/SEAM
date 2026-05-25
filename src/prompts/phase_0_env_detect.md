# Phase 0 - Environment Detection

You are executing `{phase_name}` for the target project at `{project_dir}`.

## Goal
- Detect whether the host environment is primarily Ascend NPU or CUDA-oriented.
- If NPU is detected, probe the system-level CANN toolchain version, AscendC compiler availability, and driver/firmware version.
- Read the project README before making conclusions.
- Report a minimal machine-readable result for downstream phases.

## Required Actions
1. Inspect README files under `{project_dir}` first, especially `README*`, setup notes, and environment instructions.
2. Detect Ascend or NPU signals from tools such as `npu-smi`, Ascend runtime packages, `torch_npu`, or device-related environment variables.
3. Detect CUDA signals from tools such as `nvidia-smi`, CUDA packages, or project instructions that clearly depend on CUDA.
4. **CANN Toolchain Detection** (only if NPU is detected in step 2):
   a. Check CANN version: `cat /usr/local/Ascend/ascend-toolkit/latest/.env` or `/usr/local/Ascend/ascend-toolkit/latest/ascend-toolkit/set_env.sh` version line.
   b. Check AscendC compiler: run `which ccec` or check if `/usr/local/Ascend/ascend-toolkit/latest/compiler/ccec_compiler/` directory exists. Also check `/usr/local/Ascend/ascend-toolkit/latest/compiler/ascendc/` for SDK headers.
   c. Check driver/firmware version: from `npu-smi info` output version line.
5. Determine the active Python version from the system runtime.
6. Prefer observable facts over assumptions. If evidence conflicts, explain the tie-break in brief working notes, but keep the final answer schema-only.

## Hard Rules
- Stay inside `{project_dir}` and its direct environment context.
- Read the README before returning a result.
- Do not hallucinate hardware that you cannot verify.
- If both NPU and CUDA signals exist, prefer `npu` only when Ascend/NPU evidence is directly observable on the host.
- If CANN toolchain is NOT detected on an NPU host, set `cann_version` to `"not_found"` and `ascendc_available` to `false`.
- If AscendC compiler is not found, set `ascendc_available` to `false` — do NOT guess or assume its presence.
- You may reason freely in your response, but end it with a single JSON object containing exactly the required keys for this phase. No other JSON objects should appear.
- If any package index lookup is needed, prefer domestic mirrors such as 阿里云镜像 or 清华镜像, and note any fallback only in intermediate work, not in the final JSON.

## IMPORTANT INSTRUCTIONS
- DO NOT explore or search project code. Your ONLY task is hardware/environment detection.
- Keep total tool calls under 15.
- Execute the required commands, then IMMEDIATELY respond with the JSON result.

## Output Format
Return exactly one JSON object with this shape:

```json
{
  "platform": "npu",
  "npu_detected": true,
  "python_version": "3.10.12",
  "cann_version": "8.0.RC1",
  "ascendc_available": false,
  "driver_version": "25.0.rc1.1"
}
```

## Field Semantics
- `platform`: `npu` when Ascend/NPU is directly detected, otherwise `cuda`.
- `npu_detected`: boolean based on direct environment evidence.
- `python_version`: concrete interpreter version string.
- `cann_version`: CANN toolkit version string (e.g. `"8.0.RC1"`). Set to `"not_found"` if NPU detected but CANN cannot be determined. Set to `"n/a"` if platform is cuda. This is a system-level toolchain version, NOT a Python package version.
- `ascendc_available`: boolean — `true` if AscendC compiler (`ccec` command or SDK path at `/usr/local/Ascend/ascend-toolkit/latest/compiler/`) is present, `false` otherwise. This is critical for `operator_fixer` to know whether custom kernel compilation is possible.
- `driver_version`: NPU driver/firmware version from `npu-smi info`. Set to `"not_found"` if not available.
