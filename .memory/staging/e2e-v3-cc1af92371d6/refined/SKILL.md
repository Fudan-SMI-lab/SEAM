---
name: sglang-npu-triton-stub-bridge-adaptor
description: Disable broken NPU Triton kernel stubs with if-False bridge-adaptor pattern
tags: ["sglang", "ascend-npu", "triton-kernel", "bridge-adaptor", "sgl-kernel-npu", "fallback"]
category: operator_incompat
subtype: triton_kernel_stub
confidence: 0.8
occurrence_count: 1
---

# Disable broken NPU Triton kernel stubs with if-False bridge-adaptor pattern

## When to Use
- TypeError: 'function' object is not subscriptable at sglang runtime when invoking alloc_extend_kernel[(bs,)] — sgl_kernel_npu exports a plain Python placeholder stub for a Triton kernel that was never ported to the NPU backend. The Triton grid-launch bracket syntax [(bs,)] fails because Python cannot subscript a plain function.

## Root Cause
sgl_kernel_npu.mem_cache.allocator.alloc_extend_kernel is a stub that returns a plain Python function instead of a real Triton JIT-compiled kernel. The NPU Triton backend has not yet implemented this kernel — only the CUDA Triton implementation exists. When the sglang runtime evaluates the conditional (e.g., num_new_pages_item < 200) and enters the kernel path, it imports the stub and attempts grid-launch invocation, which crashes.

## How to Use
1. Identify the sglang NPU hardware-backend file containing the broken Triton kernel invocation. Search under <venv>/lib/python*/site-packages/sglang/srt/hardware_backend/npu/ for files importing from sgl_kernel_npu and using Triton grid-launch bracket syntax [(var,)].
2. Locate the conditional block (e.g., if num_new_pages_item < 200:) that gates the kernel invocation code path.
3. Verify that a working pure-Python fallback already exists in the else: or subsequent branch (in this case, alloc_extend_naive from sglang.srt.mem_cache.allocator).
4. Replace the condition with if False: to permanently disable the broken kernel path while preserving the original code in the dead branch.
5. Add a # NOTE(seam): comment immediately after the if False: line documenting why the kernel is disabled, the specific error it causes (e.g., TypeError: stub is not subscriptable), and that the pure-Python fallback is used instead.
6. Run the sglang serve command with validation imports (import torch, import torch_npu, import sglang) and a health-check probe to confirm the server starts and serves requests without TypeError.

## Code Examples
[
  {
    "file": "sglang/srt/hardware_backend/npu/allocator_npu.py",
    "before": "        if num_new_pages_item < 200:\n            from sgl_kernel_npu.mem_cache.allocator import alloc_extend_kernel\n\n            out_indices = torch.empty(\n                (extend_num_tokens,),\n                dtype=torch.int64,\n                device=self.device,\n            )\n            max_num_extend_tokens = next_power_of_2(extend_num_tokens)\n            bs = prefix_lens.shape[0]\n            alloc_extend_kernel[(bs,)](\n                prefix_lens,\n                seq_lens,\n                last_loc,\n                self.free_pages,\n                out_indices,\n                next_power_of_2(bs),\n                self.page_size,\n                max_num_extend_tokens,\n            )\n\n        else:",
    "after": "        if False:\n            # NOTE(seam): NPU kernel path disabled \u2014 stub returns plain function,\n            # not Triton kernel; Triton grid-launch syntax [(bs,)] fails with\n            # TypeError. Always use the pure-Python naive fallback instead.\n            from sgl_kernel_npu.mem_cache.allocator import alloc_extend_kernel\n\n            out_indices = torch.empty(\n                (extend_num_tokens,),\n                dtype=torch.int64,\n                device=self.device,\n            )\n            max_num_extend_tokens = next_power_of_2(extend_num_tokens)\n            bs = prefix_lens.shape[0]\n            alloc_extend_kernel[(bs,)](\n                prefix_lens,\n                seq_lens,\n                last_loc,\n                self.free_pages,\n                out_indices,\n                next_power_of_2(bs),\n                self.page_size,\n                max_num_extend_tokens,\n            )\n\n        else:"
  }
]

## Do Not
- Do NOT delete the original kernel code inside the if False: block — the bridge-adaptor pattern preserves it for future reference when the NPU Triton backend matures.
- Do NOT attempt to install, compile, or cross-compile CUDA Triton kernels on NPU — the backend must be implemented by the sgl_kernel_npu maintainers.
- Do NOT attempt to fix the stub by wrapping it in a Triton JIT or grid decorator — the underlying kernel logic does not exist.
- Do NOT leave the original conditional intact (e.g., num_new_pages_item < 200) — the conditional is exactly what triggers the broken code path when the condition evaluates True.
- Do NOT remove or modify the pure-Python fallback (alloc_extend_naive) — it is the production path and has already been validated as correct.

## Evidence
- Source runs: e2e-v3-cc1af92371d6
