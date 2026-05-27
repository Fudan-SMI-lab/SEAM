# Constraints: Normal Entry 057 Experiment

Project: deepwave_upstream_fwi_original
Experiment: Normal-entry E2E with 057_example_fwi.py
Purpose: Validate normal application demo behavior

## Binding Rules

1. **Entry script**: MUST use `057_example_fwi.py` as the sole entry point. Do not substitute with any other script.

2. **Headless-safe plotting**: `plt.show()` calls in `057_example_fwi.py` are headless blockers. Before running, patch or wrap the script to:
   - Set `matplotlib.use('Agg')` before any matplotlib import, OR
   - Comment out `plt.show()` and add `plt.savefig('loss_plot.png')` for the loss curve plot
   - Do NOT remove or modify the `plt.savefig('example_simple_fwi.jpg')` and `plt.savefig('example_increasing_freq_fwi.jpg')` calls -- those are already headless-safe

3. **Dependencies**: The following packages must be available. If missing, install via pip during repair:
   - `matplotlib` (with Agg backend for headless)
   - `scikit-image` (provides `skimage.metrics`)
   - `lpips` (perceptual similarity; AlexNet model download to cache is ACCEPTABLE on first run)
   - `scipy` (signal processing, `scipy.signal.butter`, `scipy.ndimage.gaussian_filter`)
   - `torchaudio` (provides `torchaudio.functional.biquad`)
   - `deepwave` (vendor wave propagation library)

4. **Data files -- REAL FILES, NOT SYMLINKS (CRITICAL)**: The following files MUST be present as REAL files (not symlinks) at `/workspace` (container workdir):
   - `marmousi_vp.bin` -- true velocity model (2301 x 751 float32 binary)
   - `marmousi_data.bin` -- observed seismic shot data

   **Symlink problem**: The E2E harness `copy_project_light()` skips `.bin` files and `symlink_large_files()` creates host-absolute symlinks (e.g. `marmousi_vp.bin -> /home/zihang/.../marmousi_vp.bin`). These symlinks are BROKEN inside the container because the host-absolute target path does not exist in the container filesystem.

   **Required fix**: In Phase 3, BEFORE returning the JSON output, check each `.bin` file at `{project_dir}`. If it is a symlink, resolve the symlink on the host side and copy the actual file content in place, replacing the symlink with a real file:
   - `os.path.islink('marmousi_vp.bin')` -> true -> resolve and copy real content
   - If the source `.bin` file is under `deepwave_upstream_fwi_original/`, locate it there and copy it
   - After materialization, `marmousi_vp.bin` and `marmousi_data.bin` must be real files (not symlinks) with actual binary content
   - This ensures the container mount exposes the actual file content

   If the files are missing entirely (not even as symlinks), copy them from the project source tree under `deepwave_upstream_fwi_original/` or from the user-provided source location.

5. **Container workdir**: Set to `/workspace`. All data files and the entry script must be accessible there. The container mount maps `{project_dir}` to `/workspace`, so any real file at `{project_dir}/marmousi_vp.bin` becomes `/workspace/marmousi_vp.bin` inside the container.

6. **Normal exit expected**: The script runs bounded optimization loops (`for epoch in range(n_epochs)`). It should exit with code 0 after completing all epochs and metrics computation. Exit code 0 is success.

7. **PPU-compatible APIs only**: `torch.cuda` calls are expected and correct in PPU environments. Do NOT convert `torch.cuda` to `torch.npu`. Do NOT install `torch_npu` or Ascend toolchains.

8. **Repair allowed**: If dependency installation fails, missing packages, or data file issues occur, dependency repair is explicitly allowed. Do NOT give up on first failure. LPIPS first-run model download time is acceptable.

9. **NO CPU FALLBACK (CRITICAL)**: This experiment MUST run on PPU CUDA-compatible hardware. The following actions are FORBIDDEN during any phase including repair:
   - Setting `CUDA_VISIBLE_DEVICES=''` or any empty value -- this forces PyTorch/DeepWave to CPU-only execution
   - Setting `CUDA_VISIBLE_DEVICES=-1` or any negative value
   - Forcing `CUDAToolkit_FOUND FALSE` in CMake or build configuration
   - Changing `device` variable from `'cuda'` to `'cpu'` in the entry script
   - Installing a CPU-only wheel or package when a CUDA-compatible package is available
   - Redirecting CUDA API calls to CPU fallback paths
   - Setting `TORCH_CUDA_ARCH_LIST` to empty or CPU-only targets

   **If the entry script fails with CUDA symbol errors**, the fix MUST be to install/build the CUDA/accelerator variant of the missing package, NOT to fall back to CPU. Undefined accelerator symbols in compiled shared libraries indicate a CPU-only build -- the correct fix is to rebuild the package with the appropriate accelerator SDK, not to switch to CPU mode.

   **The setup phase already handles deterministic dependency installation** -- repair agents should NOT undo or override the accelerator build performed there. If accelerator symbols are missing after setup, re-run the build with the appropriate SDK environment variables, do NOT delete the accelerator build artifacts.
