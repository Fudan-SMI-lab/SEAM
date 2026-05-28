# GLM-OCR vLLM Smoke Constraints

## Binding Rules

1. **Model**: Use `models/GLM-OCR` (project-relative). No MaaS, cloud API, or external endpoint fallback.

2. **Entry**: Generate a self-contained Python smoke script named `smoke_glmocr_vllm.py` that starts vLLM with the model, sends one image+text request, and verifies the response. The script must write its own vLLM server subprocess — do NOT assume a pre-existing server.

3. **GPU**: GPU 5 only. Script must set `CUDA_VISIBLE_DEVICES=5` or equivalent before importing torch/vllm.

4. **vLLM runtime flags**: Use these conservative settings:
   - `max_model_len=2048`
   - `max_num_batched_tokens=1024`
   - `max_num_seqs=1`
   - `limit_mm_per_prompt={"image": 1}`
   - `enforce_eager=True`

5. **Output**: Write inference result to `results/glmocr_vllm_smoke.json` with at least `status`, `response_text`, and `elapsed_seconds` fields.

6. **Success**: Exit code 0 after producing a valid JSON output file. Transient download/model-load time is expected; do not treat load-time slowness as failure.
