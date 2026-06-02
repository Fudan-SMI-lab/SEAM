---
name: vllm-0.11.2-mrope-get-input-positions-tensor-removal
description: vLLM 0.11.2 MRotaryEmbedding.get_input_positions_tensor API removal on Ascend NPU — replace with model.get_mrope_input_positions()
tags: ["torch-npu", "vllm", "ascend", "mrope", "rotary-embedding", "vllm-0.11.2", "multimodal-vlm"]
category: operator_incompat
subtype: vllm_mrope_api_migration_0_11_2
confidence: 0.9
occurrence_count: 1
---

# vLLM 0.11.2 MRotaryEmbedding.get_input_positions_tensor API removal on Ascend NPU — replace with model.get_mrope_input_positions()

## When to Use
- AttributeError: type object 'MRotaryEmbedding' has no attribute 'get_input_positions_tensor' — vLLM 0.11.2 removed this static method. The vllm_ascend NPUModelRunner._init_mrope_positions() still calls the deprecated API, causing runtime errors when serving multimodal Vision-Language Models (VLLMs) like Qwen2-VL, Qwen2.5-VL, or MinerU2.5-Pro on Ascend NPU.

## Root Cause
vLLM 0.11.2 restructured M-RoPE position initialization: the static method MRotaryEmbedding.get_input_positions_tensor(prompt_token_ids, hf_config=..., image_grid_thw=..., video_grid_thw=..., second_per_grid_ts=..., audio_feature_lengths=..., use_audio_in_video=...) was removed and replaced by the instance method model.get_mrope_input_positions(prompt_token_ids, mm_features) that consumes multimodal features directly. The vllm_ascend plugin (model_runner_v1.py) retained the old static-method call with manual mm_feature field extraction, making it incompatible with vLLM ≥0.11.2. The upstream reference implementation is at vllm/v1/worker/gpu_model_runner.py lines 955-964.

## How to Use
1. Detect the error signature: AttributeError on MRotaryEmbedding.get_input_positions_tensor in vllm_ascend/worker/model_runner_v1.py:_init_mrope_positions during multimodal VLM serving on Ascend NPU.
2. Open the upstream reference: vllm/v1/worker/gpu_model_runner.py lines 955-964 — observe the concise 8-line pattern using model.get_mrope_input_positions() directly on req_state fields.
3. Add supports_mrope to the import from vllm.model_executor.models.interfaces in model_runner_v1.py (used in the assert guard).
4. Replace the _init_mrope_positions method (remove the obsolete mm_feature field extraction loop that manually unpacks image_grid_thw, video_grid_thw, second_per_grid_ts, audio_feature_lengths, use_audio_in_video from mm_features, and remove the static MRotaryEmbedding.get_input_positions_tensor() call with its long keyword-argument list). Replace the entire method with: get the model via self.get_model(), assert supports_mrope(model), then call model.get_mrope_input_positions(req_state.prompt_token_ids, req_state.mm_features) and unpack the returned tuple into req_state.mrope_positions and req_state.mrope_position_delta.
5. Retain the MRotaryEmbedding import — do NOT remove it — because get_next_input_positions_tensor() is still used elsewhere in the file (line ~944) for on-the-fly completion mrope position computation.
6. Verify by running the vLLM serving entry script with a multimodal VLM model — confirm no AttributeError on M-RoPE position initialization and verify serving health-check passes.

## Code Examples
[
  {
    "file": ".venv/lib/python3.10/site-packages/vllm_ascend/worker/model_runner_v1.py",
    "before": "from vllm.model_executor.models.interfaces import (\n    supports_transcription,\n)",
    "after": "from vllm.model_executor.models.interfaces import (\n    supports_mrope,\n    supports_transcription,\n)"
  },
  {
    "file": ".venv/lib/python3.10/site-packages/vllm_ascend/worker/model_runner_v1.py",
    "before": "    def _init_mrope_positions(self, req_state: CachedRequestState):\n        # Extract mm_feature fields for the static method call\n        hf_config = self.vllm_config.model_config.hf_config\n        mm_features = req_state.mm_features\n        image_grid_thw = []\n        video_grid_thw = []\n        second_per_grid_ts = []\n        audio_feature_lengths = []\n        use_audio_in_video = []\n        for feature in mm_features:\n            if isinstance(feature, dict):\n                image_grid_thw.extend(feature.get('image_grid_thw', []))\n                video_grid_thw.extend(feature.get('video_grid_thw', []))\n                second_per_grid_ts.extend(feature.get('second_per_grid_ts', []))\n                audio_feature_lengths.extend(feature.get('audio_feature_lengths', []))\n                use_audio_in_video.extend(feature.get('use_audio_in_video', []))\n        req_state.mrope_positions, req_state.mrope_position_delta = \\\n            MRotaryEmbedding.get_input_positions_tensor(\n                req_state.prompt_token_ids,\n                hf_config=hf_config,\n                image_grid_thw=image_grid_thw,\n                video_grid_thw=video_grid_thw,\n                second_per_grid_ts=second_per_grid_ts,\n                audio_feature_lengths=audio_feature_lengths,\n                use_audio_in_video=use_audio_in_video,\n            )",
    "after": "    def _init_mrope_positions(self, req_state: CachedRequestState):\n        model = self.get_model()\n        assert supports_mrope(model), \"M-RoPE support is not implemented.\"\n\n        req_state.mrope_positions, req_state.mrope_position_delta = (\n            model.get_mrope_input_positions(\n                req_state.prompt_token_ids,\n                req_state.mm_features,\n            )\n        )"
  }
]

## Do Not
- Do NOT blindly remove the MRotaryEmbedding import from model_runner_v1.py — get_next_input_positions_tensor() is still actively used at line ~944 for completion mrope position computation.
- Do NOT attempt to downgrade vLLM to restore the old API — vLLM 0.11.2 is the target version and API migration is the correct path.
- Do NOT manually unpack mm_features into image_grid_thw/video_grid_thw/etc. — the new get_mrope_input_positions() consumes mm_features directly and handles all modalities internally.

## References
- Upstream reference: vllm/v1/worker/gpu_model_runner.py lines 955-964 — GPUModelRunner._init_mrope_positions() using model.get_mrope_input_positions()
- vllm_ascend/worker/model_runner_v1.py line 944 — MRotaryEmbedding.get_next_input_positions_tensor() still used for completion on-the-fly mrope computation; do NOT remove this import

## Evidence
- Source runs: e2e-v3-547d820bb11b
