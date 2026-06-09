# Workflow Selection

You are selecting the best SEAM migration workflow for a target project.

## Goal

Choose **exactly one** workflow from the provided candidate list.
The framework will use your selection to drive the migration process.

## Before You Select: Explore the Device / Accelerator Environment

**IMPORTANT**: You MUST explore the current machine's device and driver
environment BEFORE making your selection. The candidate workflows target
different accelerator platforms (Ascend NPU, PPU, MUSA/Muxi, etc.), and the
correct choice depends on what hardware and SDK is actually present.

Run the following device-discovery commands and consider their outputs as
the PRIMARY evidence for platform selection:

### Muxi / MetaX / MACA / MUSA signals
- `mx-smi` or `mx-smi -L` — if present and reports attached GPUs, this machine is a Muxi / MetaX platform.
- `ls /dev/mxcd` — if this device file exists, a Muxi kernel driver is loaded.
- `ls /opt/maca*` or `ls /opt/maca-*` — Muxi MACA SDK installations.
- `ls /usr/local/metax` — another common Muxi SDK path.
- `printenv | grep -E 'MUSA_VISIBLE_DEVICES|MACA_VISIBLE_DEVICES'` — Muxi environment variables.
- `pip list 2>/dev/null | grep -iE 'torch_musa|musa'` or `python -c "import torch_musa" 2>&1` — Muxi PyTorch bindings (not always installed; absence does NOT rule out Muxi).

### Ascend NPU / Huawei signals
- `npu-smi info` or `npu-smi info -l` — if present and reports chips, this machine is an Ascend NPU platform.
- `ls /dev/davinci*` — Ascend NPU device files.
- `ls /usr/local/Ascend` — Ascend CANN SDK installation.
- `printenv | grep -i ascend` — Ascend environment variables.

### PPU / AI-silicon signals
- `ppu-smi` or `smi` — PPU management tool.
- `ls /dev/ppu*` — PPU kernel devices.
- `ls /opt/ppu` or `/usr/local/ppu` — PPU SDK paths.

### NVIDIA (fallback / baseline) signals
- `nvidia-smi` — NVIDIA GPU management (may be absent on non-NVIDIA machines).
- `ls /dev/nvidia*` — NVIDIA kernel devices.

### Generic fallback probe
- Use `uname -m`, `lspci | grep -iE 'vga|3d|accelerat'`, or `lshw -c display 2>/dev/null` for a broad hardware overview if none of the above tools are found.

### How to interpret the results
- If Muxi/MACA signals (`mx-smi`, `/dev/mxcd`, `/opt/maca*`) are found, the machine is Muxi/MetaX — prefer a MUSA/Muxi workflow.
- If Ascend NPU signals (`npu-smi`, `/dev/davinci*`, `/usr/local/Ascend`) are found, the machine is Ascend — prefer an NPU workflow.
- If PPU signals are found, prefer a PPU workflow.
- If only NVIDIA signals are found, or no known accelerator is detected, the project likely targets CUDA — prefer a workflow that covers CUDA-to-accelerator migration or the most general-purpose workflow.
- DO NOT assume a platform based solely on the project's PyTorch/TensorFlow usage. Framework presence is secondary; discovered device evidence is PRIMARY.

## Project Context

{project_context}

## User-Provided Constraints (for awareness)

{user_constraints}

Use these raw user-provided constraints only as an additional reference signal
when refining selection among candidate workflows compatible with the same currently detected platform/environment. They must not override, replace, or
infer platform/environment selection. Actual platform/environment selection must
remain based on current real device/environment discovery.

## Candidate Workflows

{candidate_workflows}

## Selection Guidance

After exploring the device environment, inspect the target project path and
analyze the project files, repository structure, and available context. Combine
that with the device environment and workflow descriptions to select the
workflow that best matches:

- **Discovered device / accelerator platform** (PRIMARY): Which of the above device-discovery probes succeeded? Match the discovered platform to the candidate workflows' target accelerators.
- **Framework alignment** (SECONDARY): Does the project use PyTorch, TensorFlow, JAX, or other frameworks? Use this to narrow between workflows for the same discovered platform.
- **User-provided constraints** (REFINEMENT ONLY): Use constraints only to refine among candidates compatible with the same currently detected platform/environment; never use constraints to choose or infer the platform/environment.
- **Project complexity and migration scope**: Choose simpler workflows for small projects, and more comprehensive workflows when the inspected project structure and available context indicate broader migration needs.

## Hard Rules

- Select **exactly one** workflow from the candidate list above.
- Your selection MUST match one of the listed workflows verbatim (by path).
- Do NOT modify, combine, or invent workflow names.
- User-provided constraints MUST NOT override current real device/environment discovery for platform/environment selection.
- If device discovery fails to identify a platform, or no candidate matches the
  discovered platform, prefer the most versatile / general-purpose workflow.
- If insufficient project context is available, still base your decision on
  device discovery results.
- Return ONLY a single JSON object with the key `selected_workflow`.
  Do NOT include any other text, JSON, or markdown after this object.

## Required Output

```json
{"selected_workflow": "<exact workflow path from the candidate list>"}
```
