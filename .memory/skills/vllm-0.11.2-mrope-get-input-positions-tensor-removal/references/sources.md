# References

- Upstream reference: vllm/v1/worker/gpu_model_runner.py lines 955-964 — GPUModelRunner._init_mrope_positions() using model.get_mrope_input_positions()
- vllm_ascend/worker/model_runner_v1.py line 944 — MRotaryEmbedding.get_next_input_positions_tensor() still used for completion on-the-fly mrope computation; do NOT remove this import
