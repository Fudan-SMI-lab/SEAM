---
name: cann-tbe-dependency-preflight
description: CANN TBE Dependency Pre-flight: Install decorator, attrs, psutil, cffi, protobuf Before CANN Operator Execution
tags: ["ascend-npu", "cann", "tbe", "dependency-management", "venv-setup", "transitive-deps"]
category: dependency_issue
subtype: cann_tbe_missing_transitive_deps
confidence: 0.95
occurrence_count: 1
---

# CANN TBE Dependency Pre-flight: Install decorator, attrs, psutil, cffi, protobuf Before CANN Operator Execution

## When to Use
- Any project that imports CANN Tensor Boost Engine (TBE) or TE packages (`te`, `tbe`, `topi`, `auto_schedule`, `tik_lib`, `schedule_search`, `dsl_lib`) on Ascend NPU. CANN TBE packages import `decorator`, `attrs`, `psutil`, `cffi`, `protobuf` at runtime WITHOUT declaring them as `install_requires`. This causes cryptic `ImportError` or `AttributeError` tracebacks during CANN operator execution — errors that look like missing operator implementations or toolkit misconfiguration but are in fact just missing transitive Python dependencies. The signature: a Python traceback pointing into `site-packages/te/`, `site-packages/tbe/`, or `site-packages/topi/` with `ModuleNotFoundError: No module named 'decorator'` (or `attrs`, `psutil`, `cffi`, `protobuf`). Without pre-flight installation, each missing dep requires a separate `pip install` + retry cycle, costing 5+ iterations in the Phase 5 validation loop.

## Root Cause
CANN TBE (Tensor Boost Engine) and TE (Tik Engine) packages (`te`, `tbe`, `topi`, and their subpackages) import `decorator`, `attrs`, `psutil`, `cffi`, and `protobuf` at runtime during operator compilation and execution. However, the CANN `.whl` packages do not declare these as `install_requires` in their `setup.py` or `pyproject.toml`. The CANN installation guide assumes these packages are pre-installed in the system Python or installed as transitive dependencies of the CANN toolkit's own environment — an assumption that breaks in user-managed virtual environments created by SEAM's Phase 2 (`phase_2_venv_create`). When `torch_npu` or `torch_npu.npu` triggers a TBE operator, the runtime import fails with a `ModuleNotFoundError` that cascades into operator registration failures, producing misleading error messages about operator availability rather than the true root cause (missing transitive dependency).

## How to Use
1. **Pre-flight installation (Phase 2, after pip install torch_npu):** Run a single pre-flight pip install command to install all known CANN TBE transitive dependencies in one shot, eliminating the need for iterative `pip install` + retry cycles during Phase 5 validation.
   ```bash
   .venv/bin/pip install decorator attrs psutil cffi protobuf
   ```
2. **One-liner checker command (Phase 2 or Phase 5, before running import tests):** Verify all transitive deps are importable in a single Python invocation:
   ```bash
   .venv/bin/python -c "[__import__(m) for m in ['decorator','attrs','scipy','psutil','cffi','protobuf']]"
   ```
   The `scipy` check is included because some CANN TBE operator implementations (`topi`) also import `scipy` at runtime for numerical computations. A non-zero exit code means a dependency is missing.
3. **Integration into requirements.txt:** Add the transitive deps to the project's `requirements.txt` or a dedicated `cann_tbe_deps.txt` so they are installed during Phase 2 venv setup:
   ```
   # CANN TBE transitive dependencies (not declared by CANN whl packages)
   decorator>=5.1.1
   attrs>=23.1.0
   psutil>=5.9.5
   cffi>=1.16.0
   protobuf>=4.24.0
   scipy>=1.11.0
   ```

## Code Examples
[
  {
    "file": "requirements.txt (project root, addition for CANN TBE transitive deps)",
    "before": "# Project dependencies (no CANN TBE transitive deps listed)",
    "after": "# CANN TBE transitive dependencies (not declared by CANN whl packages, imported at runtime)\ndecorator>=5.1.0\nattrs>=23.1.0\npsutil>=5.9.0\ncffi>=1.15.0\nprotobuf>=4.21.0\nscipy>=1.11.0"
  },
  {
    "file": ".venv pre-flight verdict checker (one-liner, Phase 2 or Phase 5)",
    "before": "# No pre-flight check — missing deps discovered one at a time via ImportError retry cycles",
    "after": "# Pre-flight checker: verify all CANN TBE transitive deps are importable in one shot\n.venv/bin/python -c \"[__import__(m) for m in ['decorator','attrs','scipy','psutil','cffi','protobuf']]\""
  }
]

## Do Not
- Do NOT assume that installing `torch_npu` or `ascend-toolkit` via pip will pull in `decorator`, `attrs`, `psutil`, `cffi`, or `protobuf` — these are NOT declared as install_requires by CANN whl packages.
- Do NOT treat the first `ModuleNotFoundError` from a TBE import as an operator or toolkit issue — always run the pre-flight checker first to rule out missing transitive deps before diving into operator debugging.
- Do NOT install these deps one at a time in a reactive retry loop — pre-flight installation eliminates 5+ unnecessary pip install + retry cycles in Phase 5 validation.
- Do NOT skip `scipy` in the pre-flight check — some `topi` operator implementations import `scipy` at runtime and will fail silently with misleading error messages.

## References
- https://www.hiascend.com/document/detail/en/CANNCommunityEdition/80RC1alpha003/developmentguide/operatordevelopment/atlasoperatordev_10_0007.html — CANN TBE operator development guide
- https://www.hiascend.com/document/detail/en/CANNCommunityEdition/80RC1alpha003/developmentguide/devtools/atlasprofiling_16_0111.html — CANN profiling documentation
- https://gitee.com/ascend/ModelZoo-PyTorch — Ascend official PyTorch model zoo (reference for dependency patterns)

## Evidence
- Source runs: e2e-v3-8c8bf406dc7e
