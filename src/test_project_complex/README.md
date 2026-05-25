# Complex PyTorch Smoke Test Project

Realistic multi-file training project for end-to-end CUDA-to-NPU migration testing.
The main entry point lives in `src/training/runner.py`, while `scripts/prepare_data.py`
acts as a bootstrap utility for preparing synthetic metadata before delegating to the
runner.
