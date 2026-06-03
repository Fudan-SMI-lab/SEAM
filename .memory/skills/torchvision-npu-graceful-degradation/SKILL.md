---
name: torchvision-npu-graceful-degradation
description: Torchvision Operator NPU Availability: Gracefully Degrade Metrics Instead of Crashing on Missing NPU Kernels
tags: ["ascend-npu", "torchvision", "nms", "lpips", "metrics", "graceful-degradation", "operator-compatibility"]
category: operator_incompat
subtype: torchvision_nms_no_npu_kernel
confidence: 0.80
occurrence_count: 1
---

# Torchvision Operator NPU Availability: Graceful Degrade Metrics Instead of Crashing on Missing NPU Kernels

## When to Use
- RuntimeError: 'torchvision::nms' not implemented for 'npu' when computing perceptual metrics (LPIPS) or running torchvision-based image processing on Ascend NPU. Deepwave FWI or other scientific computing workloads that call torchvision operators (nms, roi_align, etc.) during metric computation or validation stages fail because torchvision lacks NPU kernel backends.

## Root Cause
torchvision is primarily developed for CUDA and CPU backends. The torchvision::nms operator and related ops (roi_align, deform_conv2d, etc.) have no registered NPU kernel implementation in the torch_npu or CANN ecosystem. When code attempts to call these operators on an NPU tensor, PyTorch's dispatcher raises a RuntimeError with "'torchvision::nms' not implemented for 'npu'". Unlike standard PyTorch ops that torch_npu can intercept and remap, torchvision's custom C++ ops are dispatched at a level where the NPU fallback is unavailable.

## How to Use
1. Identify the torchvision operator call site — typically in metric computation code (e.g., LPIPS using NMS internally, or custom validation scripts like validate_custom_ops_full.py).
2. Wrap the torchvision operator call in a try/except RuntimeError block that checks for the "'torchvision::<op>' not implemented for 'npu'" error message pattern.
3. When the RuntimeError is caught, emit an informative skip message (e.g., `Logger.warning("torchvision::nms not available on NPU, skipping LPIPS; falling back to skimage metrics.")`) so the user knows which metric was degraded.
4. Fallback to scikit-image (skimage) for PSNR and SSIM computation, which run on CPU and do not depend on NPU kernel availability. Use `skimage.metrics.peak_signal_noise_ratio()` and `skimage.metrics.structural_similarity()` with data transferred to CPU via `.cpu().numpy()`.
5. Ensure the overall validation pipeline does not crash — continue with remaining metrics and report degraded status in the final output.

## Code Examples
[
  {
    "file": "validate_custom_ops_full.py or test scripts calling torchvision operators",
    "before": "    import torchvision.ops as ops\n    nms_result = ops.nms(boxes, scores, iou_threshold)",
    "after": "    import torchvision.ops as ops\n    try:\n        nms_result = ops.nms(boxes, scores, iou_threshold)\n        lpips_score = compute_lpips(pred, target)\n    except RuntimeError as e:\n        if \"torchvision::nms\" in str(e) and \"npu\" in str(e):\n            Logger.warning(\"torchvision::nms not available on NPU, skipping LPIPS; falling back to skimage metrics.\")\n            from skimage.metrics import peak_signal_noise_ratio, structural_similarity\n            psnr = peak_signal_noise_ratio(target.cpu().numpy(), pred.cpu().numpy(), data_range=1.0)\n            ssim = structural_similarity(target.cpu().numpy(), pred.cpu().numpy(), data_range=1.0, multichannel=True)\n            lpips_score = None  # gracefully skipped\n        else:\n            raise"
  }
]

## Do Not
- Do NOT attempt to force torchvision operators onto the NPU via device transfer hacks — the kernels simply do not exist.
- Do NOT silently skip metrics without logging — the user must know which metrics were degraded for trustworthiness.
- Do NOT import skimage unconditionally as a hard dependency — only import it within the RuntimeError catch block so CUDA-capable environments are unaffected.
- Do NOT let the RuntimeError propagate and crash the entire validation pipeline — graceful degradation is the goal.

## References
- https://pytorch.org/vision/stable/ops.html — torchvision operator documentation
- https://scikit-image.org/docs/stable/api/skimage.metrics.html — skimage metrics documentation
- https://www.hiascend.com/document/detail/en/CANNCommunityEdition/80RC1alpha003/apiref/appdevgapi/ascend_ops_list_0001.html — CANN operator support list

## Evidence
- Source runs: e2e-v3-8c8bf406dc7e
