# torch-npu 2.5.x requires pyyaml as undeclared runtime transitive dependency

torch-npu 2.5.1 contains an undeclared runtime dependency on pyyaml that is not specified in the wheel's pip metadata. The file torch_npu/npu/_memory_viz.py imports yaml at module load time, but since pyyaml is not declared as a dependency, pip install completes without warnings even though the package is non-functional.

The error manifests as: ModuleNotFoundError: No module named 'yaml' during torch's device backend loading phase (torch._import_device_backends). This makes the failure appear to originate from torch core rather than from a torch_npu packaging gap.

Detection pattern: After installing torch-npu in a fresh venv, running `python -c 'import torch_npu'` immediately exposes the failure before any project code executes.

Resolution: Install pyyaml>=6.0 in the target venv. PyYAML 6.0.3 was added manually during this migration to resolve the import failure. The execution journal recorded an extended duration during Phase 2 venv creation attributable to diagnosing and installing this missing dependency.

Preventive measure: Add pyyaml>=6.0 as a standard dependency in any venv that includes torch-npu — do not wait for the import failure to occur.

This is a packaging bug in the torch-npu 2.5.x wheel that may be fixed in future releases but should be guarded against in all torch-npu-based migrations.
