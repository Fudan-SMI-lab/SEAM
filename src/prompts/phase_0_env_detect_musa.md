# Phase 0 - Environment Detection (MUXI Accelerator Family)

You are executing `{phase_name}` for the target project at `{project_dir}`.

{execution_environment_context}

## Target Runtime Container Context
- Execution backend mode: `{execution_backend_mode}`
- Container name or ID: `{container_name_or_id}`
- Container workdir: `{container_workdir}`
- Host project directory: `{host_project_dir}`
- Container project directory: `{container_project_dir}`
- Read-only probe command prefix: `{container_probe_command_prefix}`

When `execution_backend_mode` is `container`, the target runtime is the container above. Use `container_env_facts` and read-only probes inside that container as evidence. Host-side file tool command output is setup context only; it is not authoritative for Python, torch, accelerator availability, device count, SDK paths, or runtime libraries used by the target runtime. If a container probe is incomplete or failed, try safe read-only container probes with the command prefix above before reporting unknowns. Do not substitute host Python or host torch facts for missing container facts.

## Goal
Detect facts about the target runtime for the validation workflow. The workflow targets the MUXI accelerator family, but the observed vendor stack may be native MUSA, MACA/MetaX, mcPyTorch, or another CUDA-compatible vendor PyTorch distribution.

## Runtime Selection Rule
- If the execution context says `execution_backend_mode: container`, probe inside the framework target container/base image when commands are available.
- If the execution context says `execution_backend_mode: local`, probe the local host environment directly and do not mention container-only paths.
- File tools see the host filesystem; command probes must still target the runtime described above.

## Required Actions
1. Read README/setup notes under `{project_dir}` only for setup clues; do not infer device availability from source code.
2. Probe available Python interpreters and identify the preferred interpreter that can import the vendor torch stack.
3. Probe `torch`, `torch.cuda`, `torch_musa`, `torch.musa`, `torch_maca`, MACA/MetaX, MUSA SDK/compiler/runtime, and device nodes.
4. Report observed facts only: package presence, API mode, device count/name, SDK/runtime/compiler paths, and driver/runtime strings when discoverable.
5. Record whether CUDA APIs are vendor-compatible rather than assuming every `torch.cuda` use must become `torch.musa`.

## Hard Rules
- Do not modify files, install packages, start extra containers, or use stale pre-existing containers.
- Do not claim `torch_musa`, `torch.musa`, MACA, or MetaX support unless directly observed.
- Do not report CPU as the target platform; CPU can only be mentioned as non-target baseline context.
- Final response must be exactly one JSON object. Start with `{` and end with `}`.
- Do not include Markdown fences, analysis text, validation-error discussion, or extra JSON objects.

## Output Format
Return one JSON object with this shape:

```json
{
  "platform": "musa",
  "accelerator_detected": true,
  "observed_vendor": "maca_metax",
  "api_mode": "cuda_compatible",
  "python_version": "3.10.12",
  "preferred_python": "observed_container_or_local_python",
  "device_count": 1,
  "device_name": "MetaX C550",
  "device_nodes": ["/dev/mxcd"],
  "torch_available": true,
  "torch_version": "observed_or_unknown",
  "torch_cuda_available": true,
  "torch_cuda_device_count": 1,
  "torch_musa_available": false,
  "torch_maca_available": false,
  "musa_sdk_available": false,
  "maca_sdk_available": true,
  "musa_compiler_available": false,
  "vendor_compiler_available": true,
  "runtime_libraries": ["libmcruntime.so"],
  "driver_version": "observed_or_unknown"
}
```
