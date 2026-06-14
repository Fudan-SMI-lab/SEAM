# MinerU vLLM Experiment Constraints

- Prefer local MinerU model use when available, without triggering downloads.
- Treat `model/vlm` and `model/pipeline` as useful local model directories to check.
- `MINERU_MODEL_SOURCE=local` is a good default preference for this experiment.
- Prefer vLLM or OpenAI-compatible local serving for model access.
- Use a small bundled demo input, such as `demo/pdfs/small_ocr.pdf`, or another local sample.
- Validate with a lightweight local parse or smoke result written to disk.
