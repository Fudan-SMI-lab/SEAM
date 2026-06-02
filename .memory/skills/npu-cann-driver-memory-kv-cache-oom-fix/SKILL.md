---
name: npu-cann-driver-memory-kv-cache-oom-fix
description: CANN Driver Memory Invisibility Causes vLLM KV Cache OOM on Ascend NPU
tags: ["torch-npu", "vllm", "memory-management", "kv-cache", "oom-prevention", "cann-driver", "ascend-npu", "determine-available-memory"]
category: code_adaptation
subtype: cann_driver_memory_invisible_kv_cache_oom
confidence: 0.95
occurrence_count: 1
---

# CANN Driver Memory Invisibility Causes vLLM KV Cache OOM on Ascend NPU

## When to Use
- vLLM serving fails with OOM during KV cache allocation on Ascend NPU, especially for large multimodal models (>8B parameters). torch_npu.npu.memory_stats() reports significantly less allocated memory than the mem_get_info() delta, and the KV cache budget computed by vLLM's determine_available_memory() silently overestimates available memory because CANN driver allocations (20-43 GB) are invisible to PyTorch's memory tracking APIs.

## Root Cause
CANN (Ascend Computing Architecture for Neural Networks) driver allocates substantial persistent memory for device context initialization and runtime buffers that is not reflected in torch_npu.npu.memory_stats(). The standard vLLM memory budget formula (total_memory * gpu_memory_utilization - peak_memory) uses only memory_stats() peak for the subtraction term, ignoring the untracked CANN allocations. This causes OOM during model serving, even when gpu_memory_utilization is set conservatively (e.g., 0.5).

## How to Use
1. In the NPU worker's determine_available_memory() method (typically vllm_ascend/worker/worker_v1.py), after calling profile_run() and computing peak_memory via memory_stats(), compute non_torch_allocations as the difference between total_allocated_bytes (from mem_get_info() delta) and torch_allocated_bytes (from memory_stats() current).
2. Add a fallback check: if peak_memory equals 0 (torch_npu bug on some platforms) OR non_torch_allocations exceeds 30% of total NPU memory (CANN driver memory dominance), replace peak_memory with profiling_peak — the raw delta between init_npu_memory (before profile) and free_npu_memory (after profile), and reset non_torch_allocations to 0.
3. In the normal case (non_torch_allocations ≤ 30%), add non_torch_allocations to peak_memory so the budget formula accounts for CANN driver memory.
4. After computing available_kv_cache_memory = int(total_npu_memory * gpu_memory_utilization - peak_memory), apply a free-based safety cap: compute current_free = total_npu_memory - total_allocated_bytes, and clamp available_kv_cache_memory to max(0, min(budget, int(current_free * 0.9))).
5. Add diagnostic logging (DEBUG level) that prints peak_memory, non_torch_allocations, total_npu_memory, gpu_memory_utilization, current_free, and the final available_kv_cache_memory for inspectability.
6. On the application side (model serving wrapper), set gpu_memory_utilization conservatively at 0.5 for Ascend NPU (0.7 if VRAM ≤ 8GB on vLLM ≥ 0.11.0) to provide additional headroom for CANN driver overhead.

## Code Examples
[
  {
    "file": ".venv/lib/python3.10/site-packages/vllm_ascend/worker/worker_v1.py (NPUWorker.determine_available_memory)",
    "before": "        peak_memory = torch_npu.npu.memory_stats()[\"allocated_bytes.all.peak\"]\n        available_kv_cache_memory = int(\n            total_npu_memory * self.cache_config.gpu_memory_utilization -\n            peak_memory)",
    "after": "        peak_memory = torch_npu.npu.memory_stats()[\"allocated_bytes.all.peak\"]\n        torch_allocated_bytes = torch_npu.npu.memory_stats(\n        )[\"allocated_bytes.all.current\"]\n        total_allocated_bytes = torch_npu.npu.mem_get_info(\n        )[1] - torch_npu.npu.mem_get_info()[0]\n        profiling_peak = self.init_npu_memory - free_npu_memory\n        non_torch_allocations = total_allocated_bytes - torch_allocated_bytes\n        if peak_memory == 0 or non_torch_allocations > total_npu_memory * 0.3:\n            peak_memory = profiling_peak\n            non_torch_allocations = 0\n        else:\n            peak_memory += non_torch_allocations\n        available_kv_cache_memory = int(\n            total_npu_memory * self.cache_config.gpu_memory_utilization -\n            peak_memory)\n        current_free = total_npu_memory - total_allocated_bytes\n        free_based = int(current_free * 0.9)\n        available_kv_cache_memory = min(available_kv_cache_memory, free_based)\n        available_kv_cache_memory = int(max(available_kv_cache_memory, 0))"
  },
  {
    "file": "mineru/backend/vlm/utils.py (set_default_gpu_memory_utilization)",
    "before": "DEFAULT: vllm default (0.9) or project hardcoded value",
    "after": "    def set_default_gpu_memory_utilization() -> float:\n        from vllm import __version__ as vllm_version\n        device = get_device()\n        gpu_memory = get_vram(device)\n        default_gpu_memory_utilization = 0.5\n        if version.parse(vllm_version) >= version.parse(\"0.11.0\") and gpu_memory <= 8:\n            default_gpu_memory_utilization = 0.7\n        return default_gpu_memory_utilization"
  }
]

## Do Not
- Do NOT rely solely on torch_npu.npu.memory_stats() for memory budgeting on Ascend NPU — CANN driver memory is untracked.
- Do NOT use gpu_memory_utilization > 0.7 on Ascend NPU without the non_torch_allocations fallback and free-based safety cap — OOM is nearly guaranteed for large models.
- Do NOT skip the free_based safety cap (current_free * 0.9) — the budget formula can still overestimate even after the profiling_peak fallback.
- Do NOT remove the NPUPlatform.empty_cache() call after memory profiling — it frees temporary allocations that would otherwise fragment the KV cache budget.

## References
- https://gitee.com/ascend/vllm-ascend — official vllm_ascend project
- https://www.hiascend.com/document/detail/en/CANNCommunityEdition/80RC1alpha003/developmentguide/devtools/atlasprofiling_16_0111.html — CANN profiling documentation
- vllm v1 worker GPU reference: vllm/v1/worker/gpu_worker.py:determine_available_memory()

## Evidence
- Source runs: e2e-v3-547d820bb11b
