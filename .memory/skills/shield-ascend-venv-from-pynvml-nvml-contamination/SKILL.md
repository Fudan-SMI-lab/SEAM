---
name: shield-ascend-venv-from-pynvml-nvml-contamination
description: Shield Ascend-only venvs from inherited pynvml/NVML contamination
tags: ["torch-npu", "ascend", "pynvml", "system-site-packages", "runtime-contamination", "no-cuda-fallback"]
category: dependency_issue
subtype: nvml_contamination
confidence: 0.9
occurrence_count: 1
---

# Shield Ascend-only venvs from inherited pynvml/NVML contamination

## When to Use
- An Ascend-only validation venv that uses system site-packages emits pynvml/NVML CUDA or NVIDIA FutureWarning markers during torch or torch_npu import, causing false forbidden-runtime-marker evidence even though the intended runtime path is torch_npu/vllm-ascend.

## Root Cause
The venv inherits a deprecated system pynvml package and a _pynvml_redirector meta-path finder from system site-packages. During torch/torch_npu import, those inherited hooks make NVML bindings visible inside an Ascend-only runtime and emit CUDA/NVIDIA warning text unrelated to the actual NPU stack.

## How to Use
1. Confirm the validation target is an Ascend-only runtime using torch_npu/vllm-ascend and that CUDA/NVIDIA/NVML warnings appear during torch or torch_npu import.
2. If pynvml is installed inside the project venv, uninstall it with the venv Python: `<venv>/bin/python -m pip uninstall -y pynvml`.
3. Add a venv-local `<venv>/lib/python3.10/site-packages/sitecustomize.py` that removes any `_pynvml_redirector` finder from `sys.meta_path` at Python startup.
4. Add a venv-local `<venv>/lib/python3.10/site-packages/pynvml.py` shim that raises `ModuleNotFoundError` so inherited NVML bindings are treated as unavailable in the Ascend-only runtime.
5. Run an import preflight in the venv and verify `torch`, `torch_npu`, `tbe`, `te`, and `vllm` import successfully without the pynvml FutureWarning.
6. Run the Phase 5 or final serving validation and verify the runtime import fields report `cann_env_loaded=true`, `torch_npu_imported=true`, `tbe_imported=true`, `te_imported=true`, `vllm_imported=true`, and `forbidden_runtime_markers_absent=true`.
7. If validation still fails after the warning is removed, classify the remaining failure separately; in the source run the remaining blocker after this fix was an unrelated Python `SyntaxError` in `mineru/backend/vlm/utils.py`, not a dependency/package failure.

## Code Examples
[
  {
    "file": ".venv/lib/python3.10/site-packages/pynvml.py",
    "before": "# No venv-local pynvml shim; inherited system pynvml can be imported during torch/torch_npu startup.",
    "after": "\"\"\"Project-local Ascend runtime shim that hides inherited NVML bindings.\\n\\nThe project venv includes system site-packages for the Ascend torch_npu/vLLM stack.\\nA deprecated system pynvml package emits CUDA/NVIDIA warnings during torch import,\\nso expose pynvml as unavailable inside this Ascend-only venv.\\n\"\"\"\\n\\nraise ModuleNotFoundError(\"pynvml is disabled for the Ascend-only serving runtime\")\\n"
  },
  {
    "file": ".venv/lib/python3.10/site-packages/sitecustomize.py",
    "before": "# No venv-local startup filter; inherited _pynvml_redirector remains in sys.meta_path.",
    "after": "\"\"\"Project-local startup fixes for the Ascend serving environment.\"\"\"\\n\\nimport sys\\n\\n\\ndef _without_pynvml_redirector(finder: object) -> bool:\\n    return finder.__class__.__module__ != \"_pynvml_redirector\"\\n\\n\\nsys.meta_path[:] = [finder for finder in sys.meta_path if _without_pynvml_redirector(finder)]\\n"
  }
]

## Do Not
- Do NOT treat pynvml/NVML warnings as evidence that CUDA/NVIDIA runtime is required for an Ascend-only torch_npu/vllm-ascend validation path.
- Do NOT install or preserve pynvml inside the Ascend-only validation venv to silence the warning; make pynvml unavailable locally instead.
- Do NOT remove the system site-packages dependency blindly if the Ascend torch_npu, tbe, te, or vllm-ascend stack is being inherited from the environment.
- Do NOT classify later validation failures as dependency contamination once import preflight passes and forbidden runtime markers are absent; continue diagnosis on the new concrete failure.

## References
- validated/phase_fix_dependency_canonical.json
- raw/phase_fix_dependency_attempt0.json
- reports/SUMMARY_REPORT.md
- validated/phase_5_validation_canonical.json

## Evidence
- Source runs: e2e-v2-691a73de905b
