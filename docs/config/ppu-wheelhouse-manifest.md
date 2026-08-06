# PPU Wheelhouse Manifest

Bug #14 Gap C: the phase-2 dependency snapshot must be replayable and pinned.
This manifest records the pinned build tooling that must be present in every
PPU offline wheelhouse so a recreated execution environment reinstalls exactly
the recorded dependency set.

## Pinned build tooling

| package   | pinned version | requirement source              | wheelhouse sync requirement                |
|-----------|----------------|---------------------------------|--------------------------------------------|
| setuptools | ==77.0.3       | `src/pyproject.toml` build-system `setuptools>=68` | wheel must be bundled in the offline wheelhouse |

Rationale: `src/pyproject.toml` declares `setuptools>=68` as a build
requirement, but the host pip environment resolves it to the latest (e.g.
82.0.1). A recreated PPU execution environment that installs from the offline
wheelhouse must resolve the same build tooling every time; the manifest pins
`setuptools==77.0.3` so the recorded phase-2 `installed_packages` snapshot and
the replayable dependency plan stay deterministic.

## Private mirror sync notes

- PPU vendor index / PTG t-head artifactory / offline PPU wheelhouse are the
  preferred package sources for accelerator packages (see
  `src/prompts/phase_2_venv_create_ppu_container_baseaware.md`). Public PyPI
  installs for `torch`, `vllm`, `sglang`, `flash_attn`, `triton`, or `xgrammar`
  can contaminate the PPU environment.
- Sync the pinned `setuptools==77.0.3` wheel into the offline wheelhouse
  alongside the image-local accelerator stack.
- Domestic mirrors (阿里云, 清华) are only acceptable for non-critical,
  non-vendor pure-Python packages.
- When the execution environment is recreated mid-repair, the replayable
  dependency plan is persisted by the engine to
  `<project_dir>/.seam/dependency_plan.json` and reinstalled by the
  `dependency_reinstall` pre-step before `run_entry_script` (bug #14 Gap B).
