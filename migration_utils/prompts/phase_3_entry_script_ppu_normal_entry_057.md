# Phase 3 - Entry Script Confirmation (PPU, Normal Entry 057 Experiment)

You are executing `{phase_name}` for `{project_dir}`.

{execution_environment_context}

## Context
This is a CUDA - PPU migration workflow for a NORMAL APPLICATION DEMO. The selected command will become the target runtime validation surface after rule migration and repair. PPU exposes CUDA-compatible APIs (`torch.cuda`), so `torch.cuda` calls are expected and correct in this environment.

This workflow uses the normal-entry route. Custom-op contract fields (`entry_script_kind`, `reports_dir`, `required_report_paths`, `required_checks`, `operator_discovery_sources`, etc.) are omitted by route policy — the framework disables custom-op contract injection for this workflow, and the target runtime custom_op_final_gate auto-skips when no contract fields are present.

## Goal
- Identify the TRUE entry script or command that validates the project's full real-world migration target.
- Prefer documented project commands and existing launchers over generated scripts.
- Create a script only when no usable command exists, and then exercise all core project features.
- Do not choose a smoke, MVP, import-only, direct-only, or partial command when the project has a broader migration target.

## User Constraints
{user_constraints}

The above user constraints are **binding**. They take Priority #0: explicit user-mandated entry scripts and commands win over any heuristic choices. If the user specifies an entry script or command, you must select it regardless of other rules.

## Migration Constraints (from Phase 1.5)
{constraint_summary}

These constraints are binding. Consider them when selecting the entry script.

## Phase 2 Interpreter Choice (CRITICAL)

In this same session, Phase 2 has already recorded its execution environment decision. Use it as follows:

- **Use Phase 2's `python_path` as the preferred interpreter** from the Phase 2 environment decision. This field is always present and guaranteed.
- Use Phase 2's `venv_path` and optional `env_type` only to understand context (base env vs project-local venv). They are hints, not bindings.
- The `python_path` from Phase 2 is a preferred choice; your `run_command` must be directly executable by the target execution backend.

## Decision Priority
1. **User-mandated entry scripts (Priority #0)**: If the user explicitly specifies an entry script or command via constraints, that takes absolute precedence over all other rules.
2. Use the project's documented non-interactive full validation command, adjusted only for absolute paths or the active Python interpreter chosen by Phase 2 in this session.
3. Use an existing launcher (`train.py`, `run.py`, `generate.py`, `main.py`, `demo.py`, `057_example_fwi.py`, project test/benchmark/e2e runner) with arguments that cover the full target.
4. Create `{project_dir}/smoke_test.py` only as a last resort with no existing command.

## Headless Execution Compliance
The entry command is executed automatically in the target runtime:
- No `input()`, `getpass()`, REPL/debugger stops, blocking GUI calls, or unbounded loops in the execution path.
- **`plt.show()` calls are blocking in headless environments.** The source script `057_example_fwi.py` contains `plt.show()` at line ~141. You are allowed to create a lightweight wrapper that sets `matplotlib.use('Agg')` before importing the source module OR patch the script to comment out/replace `plt.show()` with `plt.savefig()`. Do not change the core computation logic.
- If the existing launcher is interactive, prefer documented non-interactive flags or environment variables. Otherwise create a wrapper that calls the real entry point with safe defaults.
- Do not invent unsupported CLI flags.
- If you create or select a generated/wrapper script, physically write it under `{project_dir}` before returning JSON. Never return an `entry_script_path` for a file that does not exist.
- **Verification requirement**: Before returning the final JSON, confirm the selected or created script file exists by reading its contents or listing it. Do NOT execute the full migration workload during Phase 3. You are selecting and verifying the entry script path, not running validation.

## Execution Backend Prohibition (CRITICAL)
The framework backend handles execution and lifecycle for the target execution environment. The `run_command` you return will be executed by that backend automatically.
- **Do NOT** include `docker exec`, `podman exec`, `docker run`, `podman run`, `podman create`, `podman start`, `podman stop`, `podman rm`, container names/IDs, or any host-level container lifecycle invocations in `run_command`.
- **Do NOT** reference pre-existing or shared containers; the framework manages execution/lifecycle.
- **Do**: return the direct command that runs the entry script in the target execution environment, e.g. `python3 /workspace/057_example_fwi.py`.
- Execution and lifecycle are handled entirely by the framework backend. You must not attempt to manage containers or execution environment lifecycle yourself.

Example good: `python3 /workspace/057_example_fwi.py`
Example bad: `podman exec zihang_vllm_ppu python3 /home/.../057_example_fwi.py`

## Dependency Awareness
The entry script may require these packages. A **deterministic setup phase** (`phase_4_5_normal_entry_setup`) runs before the target runtime phase and installs/builds all dependencies with PPU CUDA support. Do NOT suggest CPU fallback in your run_command or recommendations.

Required packages (installed by setup phase):
- `matplotlib` -- plotting; must use non-interactive `Agg` backend in headless mode
- `scikit-image` -- PSNR/SSIM metrics
- `lpips` -- perceptual similarity metric (AlexNet backend); LPIPS model download to cache is acceptable on first run
- `scipy` -- signal processing, filters
- `torchaudio` -- audio signal processing (biquad filter)
- `deepwave` -- wave propagation library; **MUST be built with accelerator support** (accelerator SDK env vars set by setup phase). CPU-only build is UNACCEPTABLE -- the compiled shared library must contain accelerator-enabled binaries.

Data files expected at `/workspace` (the container workdir):
- `marmousi_vp.bin` -- true velocity model
- `marmousi_data.bin` -- observed seismic data

If these data files are missing, the repair loop should copy them from the project source tree.

## Pre-Flight Data File Check (CRITICAL -- execute before returning JSON)

The E2E harness creates a lightweight project copy where `.bin` files may be **symlinks** rather than real files, and those symlinks use **host-absolute paths** that are broken inside the container. You MUST resolve this BEFORE returning the Phase 3 JSON.

**Step-by-step:**

1. List `.bin` files under `{project_dir}`:
   - Check `{project_dir}/marmousi_vp.bin`
   - Check `{project_dir}/marmousi_data.bin`

2. For each `.bin` file, run `os.path.islink(path)` from a Python one-liner or use `ls -la` to inspect. If the file is a symlink, follow the resolution procedure below.

3. **Resolve symlinks to real files**: If a `.bin` file is a symlink:
   - Read the symlink target with `os.readlink(path)` or `ls -l`
   - The target is a host-absolute path like `/home/zihang/opencode_test/deepwave_upstream_fwi_original/marmousi_vp.bin`
   - The actual file content lives at that host path, but that path WILL NOT EXIST inside the container
   - On the **host side** (where file tools run), copy the real file content over the symlink:
     ```python
     import os, shutil
     target = os.readlink(symlink_path)
     os.unlink(symlink_path)
     shutil.copy2(target, symlink_path)
     ```
   - Alternatively, locate the `.bin` file in the original source tree (e.g. `deepwave_upstream_fwi_original/`) and copy it into `{project_dir}`, overwriting the symlink
   - After this step, `file marmousi_vp.bin` should report a real file (e.g. "data" or "binary"), NOT "symbolic link"

4. **Verify**: After materialization, confirm each `.bin` file is a real file with non-zero size:
   - `os.path.isfile(path)` -> `True`
   - `os.path.islink(path)` -> `False`
   - `os.path.getsize(path)` -> positive number (e.g. ~6.9 MB for marmousi_vp.bin)

5. If any `.bin` file is missing entirely (not even a symlink), locate and copy it from the original project tree under `deepwave_upstream_fwi_original/` or a known source location provided via constraints.

**This is a mandatory pre-flight check.** The container mount maps `{project_dir}` -> `/workspace`. If the files are symlinks at mount time, they will be broken inside the container and `torch.from_file('marmousi_vp.bin', ...)` will fail with "file not found" or read 0 bytes.

## Output Format
Return exactly one JSON object. **Do NOT include any custom-op contract fields** — these are omitted by route policy for this workflow.

```json
{
  "entry_script_path": "{project_dir}/057_example_fwi.py",
  "run_command": "python3 /workspace/057_example_fwi.py",
  "phase5_entry_script_revision_allowed": true
}
```

## Field Semantics
- `entry_script_path`: absolute host-visible path to the selected or created script. This path is readable by file tools (such as `read`), by Phase 3.5 (static validator), and by the target execution backend after any path mapping.
- `run_command`: exact non-interactive command the target execution backend will execute in the target execution environment. Use visible paths or paths that the backend can map. Use the interpreter from Phase 2's `python_path` as the preferred choice in this session. Do NOT include `docker exec`, `podman exec`, container names/IDs, or host-level container lifecycle invocations.
- `phase5_entry_script_revision_allowed`: `true` (default) means the target runtime phase may revise the entry script or command if validation finds the selected command or path is incorrect. Revision is bounded to finding a working entry that matches the project's actual migration target.

## MUST NOT INCLUDE
- Do NOT output `entry_script_kind`, `reports_dir`, `required_report_paths`, `required_checks`, `operator_discovery_sources`, `operator_inventory_schema`, `performance_report_schema`, `validation_obligations`, or any other custom-op contract fields — these are omitted by route policy.
