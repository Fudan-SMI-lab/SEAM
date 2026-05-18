# System-site-packages venv strategy eliminates CUDA variant downloads on pre-configured Ascend hosts

On Ascend hosts where the CANN toolchain and torch-npu stack are pre-installed in the system Python (confirmed via Phase 0 env detection: platform=npu, torch-npu 2.5.1 present), the default approach of creating a clean virtual environment and running `pip install -r requirements.txt` is problematic. Unpinned dependency declarations like `torch` and `torchvision` in requirements.txt will resolve to CUDA variants (e.g., torch==X.Y.Z+cuXXX) from PyPI mirrors, which conflicts with the pre-installed NPU stack and causes download timeouts on large wheel files.

The correct strategy is to use `python3 -m venv --system-site-packages` to create the virtual environment. This inherits the system-installed torch-npu stack (torch==2.5.1+cpu, torch-npu==2.5.1, torchvision==0.20.1+cpu) without requiring any additional pip downloads for declared dependencies. Only undeclared transitory dependencies that are missing from the system install need to be installed manually. In this migration run, only `ml-dtypes==0.5.4` required a separate pip install from the 阿里云 mirror, and the entire Phase 2 venv creation completed in 60 seconds.

The alternative approach of a clean venv without --system-site-packages would have required downloading the torch (torch-npu), torchvision, and potentially other CUDA or CPU variant wheels, which is significantly slower and risks mirror timeout errors.

Verification step after venv creation: `/path/to/.venv/bin/python -c "import torch_npu; print(torch.npu.is_available())"` should return `True`.

This pattern applies to any migration on a pre-configured Ascend host. The key signals that indicate this strategy should be used are: (1) `npu-smi info` succeeds, confirming NPU hardware; (2) system pip list shows `torch-npu` installed; (3) project requirements.txt contains unpinned torch or torchvision entries.
