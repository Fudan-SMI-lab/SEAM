"""Accelerator package context extraction for env context building.

Provides a platform-neutral helper that extracts accelerator-related
information from ``installed_packages``, preserving the legacy
``torch_npu_version`` field while adding generic ``accelerator_packages``
and ``accelerator_package_versions`` fields.
"""

from __future__ import annotations

import re
from typing import cast

# ── Recognized accelerator families ──────────────────────────────────────
# Lowercase, underscore-normalized prefixes for packages that signal
# a specific accelerator platform (NPU, PPU, XPU, CUDA, etc.).
_ACCELERATOR_PREFIXES: list[str] = [
    # Huawei Ascend NPU
    "torch_npu",
    "torch_npu_",
    # PPU and PPU ecosystem
    "torch_ppu",
    "ppukernel",
    "ppuccl",
    "ppu_",
    "ppu",
    # XPU / AliXPU
    "xpu",
    "alixpu",
    # Inference engine (often accelerator-specific)
    "vllm",
    # Kernel language (often accelerator-specific)
    "triton",
    # NVIDIA CUDA ecosystem
    "cuda",
    "cudnn",
    "nccl",
    # MUSA (Moore Threads) / MACA (Hygon DCU) / Metax (MetaX GPU)
    # Order matters: torch_musa/torch_maca/torch_muxi/torch_metax MUST come
    # before the "torch" catch-all below (first-prefix-match semantics).
    "torch_musa",
    "musa",
    "torch_maca",
    "maca",
    "torch_muxi",
    "muxi",
    "torch_metax",
    "metax",
    # Base torch (catch-all last)
    "torch",
    "pytorch",
]


def _normalize_name(raw: str) -> str:
    """Normalize a package name: lowercase, hyphens → underscores."""
    return raw.lower().replace("-", "_")


def _parse_package_spec(pkg_str: str) -> tuple[str, str | None]:
    """Parse a package specifier into (name, version_or_none).

    Handles common forms::

        tensorflow==2.12.0
        torch>=1.9.0
        numpy<=1.24.0
        requests                  (bare name, no version)
        ppukernel
    """
    m = re.match(
        r"^([a-zA-Z0-9_.-]+?)\s*(==|>=|<=|!=|~=|>|<)\s*(.+)$",
        pkg_str.strip(),
    )
    if m:
        return m.group(1), m.group(3)
    return pkg_str.strip(), None


def extract_accelerator_context(
    installed_packages: object,
) -> dict[str, object]:
    """Extract accelerator package information from an installed_packages list.

    Parameters
    ----------
    installed_packages : list of str or any
        The ``installed_packages`` field from phase 2 output or workflow state.
        If not a list, returns defaults.

    Returns
    -------
    dict
        Keys:

        * ``torch_npu_version`` — legacy field; ``"2.1.0"`` when torch-npu/torch_npu
          has a version, otherwise ``None``.
        * ``accelerator_packages`` — ``list[str]`` of normalized accelerator
          package names (lowercase, underscores), e.g.
          ``["torch_npu", "ppukernel", "vllm"]``.
        * ``accelerator_package_versions`` — ``dict[str, str]`` mapping
          normalized name to version string for recognized accelerator packages.

    Examples
    --------
    >>> extract_accelerator_context(["torch-npu==2.1.0", "torch==2.0.1", "ppukernel==1.0.0"])
    {'torch_npu_version': '2.1.0',
     'accelerator_packages': ['torch_npu', 'torch', 'ppukernel'],
     'accelerator_package_versions': {'torch_npu': '2.1.0', 'torch': '2.0.1', 'ppukernel': '1.0.0'}}
    """
    result: dict[str, object] = {"torch_npu_version": None}
    accelerator_packages: list[str] = []
    accelerator_package_versions: dict[str, str] = {}

    if not isinstance(installed_packages, list):
        result["accelerator_packages"] = accelerator_packages
        result["accelerator_package_versions"] = accelerator_package_versions
        return result

    for pkg in cast(list[object], installed_packages):
        if not isinstance(pkg, str):
            continue

        name, version = _parse_package_spec(pkg)
        normed = _normalize_name(name)

        # Legacy torch_npu_version extraction — keep first-match semantics
        # (`break` after first `torch-npu==` in original code)
        if normed == "torch_npu" and version and result["torch_npu_version"] is None:
            result["torch_npu_version"] = version

        # Check against recognized accelerator prefixes
        for prefix in _ACCELERATOR_PREFIXES:
            if normed == prefix or normed.startswith(prefix + "_"):
                if normed not in accelerator_packages:
                    accelerator_packages.append(normed)
                if version is not None and normed not in accelerator_package_versions:
                    accelerator_package_versions[normed] = version
                break

    result["accelerator_packages"] = accelerator_packages
    result["accelerator_package_versions"] = accelerator_package_versions
    return result


# ---------------------------------------------------------------------------
# Platform-capability classification infrastructure (bug #8 + #9)
# ---------------------------------------------------------------------------
# NOTE (T8 scope): npu/cuda/ppu/cpu family detection. musa/maca/muxi/metax
# families are #9/T9's scope and were added by T11 (see _FAMILY_PREFIXES,
# _CAPABILITY_FAMILIES and the _ACCELERATOR_PREFIXES entries above).

# Family → accelerator prefixes derived from _ACCELERATOR_PREFIXES (T8 scope).
# ``torch``/``pytorch``/``numpy`` are the generic-CPU markers (catch-all).
# musa/maca/muxi/metax families added by #9 (T11): each includes the bare
# family token plus its torch_* wrapper so first-prefix-match resolves
# torch_musa → musa (not the torch catch-all) and musa/musa_* → musa.
_FAMILY_PREFIXES: dict[str, tuple[str, ...]] = {
    "npu": ("torch_npu", "torch_npu_"),
    "ppu": ("torch_ppu", "ppukernel", "ppuccl", "ppu_", "ppu"),
    "cuda": ("cuda", "cudnn", "nccl"),
    "cpu": ("torch", "pytorch", "numpy"),
    "musa": ("torch_musa", "musa"),
    "maca": ("torch_maca", "maca"),
    "muxi": ("torch_muxi", "muxi"),
    "metax": ("torch_metax", "metax"),
}

# gguf-style model backends that bind to CUDA and are NOT usable on other
# accelerator targets (npu/ppu). When such a backend is the only model-serving
# path for a non-CUDA target, the precheck must classify it as
# ``blocked_reason: "unsupported_backend"`` instead of silently running on CPU.
_CUDA_BOUND_BACKEND_PREFIXES: tuple[str, ...] = (
    "llama_cpp_python",
    "llama_cpp",
    "llamacpp",
    "gguf",
    "ctransformers",
)

# Families recognized for capability prechecking (T8 scope + #9/T11 families).
_CAPABILITY_FAMILIES: tuple[str, ...] = (
    "npu",
    "ppu",
    "cuda",
    "cpu",
    "musa",
    "maca",
    "muxi",
    "metax",
)


def _family_for_prefix(prefix: str) -> str | None:
    """Map a ``_ACCELERATOR_PREFIXES`` entry to a T8 capability family."""
    for family, prefixes in _FAMILY_PREFIXES.items():
        if prefix in prefixes:
            return family
    return None


def _package_matches_any(normed: str, candidates: tuple[str, ...]) -> bool:
    """True when *normed* equals or starts with any candidate prefix + '_'."""
    for candidate in candidates:
        if normed == candidate or normed.startswith(candidate + "_"):
            return True
    return False


def get_platform_capabilities(installed_packages: object) -> dict[str, bool]:
    """Per-family capability booleans for an installed_packages list.

    Returns a dict with at least the ``"npu"`` key (guaranteed present) plus
    ``ppu``/``cuda``/``cpu``, each mapping to a bool. Detection reuses
    ``_ACCELERATOR_PREFIXES`` (first-prefix-match semantics identical to
    ``extract_accelerator_context``) and maps each matched prefix to its family.
    """
    capabilities: dict[str, bool] = {family: False for family in _CAPABILITY_FAMILIES}
    if not isinstance(installed_packages, list):
        return capabilities

    for pkg in cast(list[object], installed_packages):
        if not isinstance(pkg, str):
            continue
        name, _version = _parse_package_spec(pkg)
        normed = _normalize_name(name)

        # First-prefix-match against _ACCELERATOR_PREFIXES (same loop as
        # extract_accelerator_context, so torch_npu wins over the torch catch-all).
        matched_family: str | None = None
        for prefix in _ACCELERATOR_PREFIXES:
            if normed == prefix or normed.startswith(prefix + "_"):
                matched_family = _family_for_prefix(prefix)
                break

        if matched_family is not None:
            capabilities[matched_family] = True
        elif _package_matches_any(normed, _FAMILY_PREFIXES["cpu"]):
            # Generic CPU marker (numpy etc.) not in _ACCELERATOR_PREFIXES.
            capabilities["cpu"] = True
    return capabilities


def _detect_cuda_bound_backends(installed_packages: object) -> list[str]:
    """Return normalized names of CUDA-bound (gguf-style) inference backends."""
    detected: list[str] = []
    if not isinstance(installed_packages, list):
        return detected
    for pkg in cast(list[object], installed_packages):
        if not isinstance(pkg, str):
            continue
        name, _version = _parse_package_spec(pkg)
        normed = _normalize_name(name)
        if _package_matches_any(normed, _CUDA_BOUND_BACKEND_PREFIXES):
            detected.append(normed)
    return detected


def precheck_platform_capability(
    installed_packages: object, target_family: str
) -> dict[str, object]:
    """Capability classifier for a target accelerator family.

    Returns a dict shaped like the ``blocked_reason: "unsupported_backend"``
    pattern in workflow_executor.py ``_maybe_recreate_execution_environment``::

        {
            "supported": bool,
            "blocked_reason": "unsupported_backend" | None,
            "usable_backend": str | None,
            "degraded_fallback": bool,
            "platform_degraded": bool,   # explicit degraded/降级 marker (CPU fallback)
        }

    Semantics:

    * (a) target family has a usable backend → ``supported=True``,
      ``blocked_reason=None``.
    * (b) target family present but no USABLE backend for it (e.g. a
      CUDA-bound gguf backend like llama-cpp-python when target is npu) →
      ``supported=False``, ``blocked_reason="unsupported_backend"``,
      ``degraded_fallback=False`` (NOT silent CPU success).
    * (c) CPU-only environment targeting an accelerator family → CPU is
      available as an explicit *degraded* fallback:
      ``platform_degraded=True`` and ``degraded_fallback=True`` (a reportable
      降级 outcome, never silent success).
    """
    target = str(target_family or "").strip().lower()
    capabilities = get_platform_capabilities(installed_packages)

    def _result(
        supported: bool,
        blocked_reason: str | None,
        usable_backend: str | None,
        degraded_fallback: bool,
        platform_degraded: bool,
    ) -> dict[str, object]:
        return {
            "supported": supported,
            "blocked_reason": blocked_reason,
            "usable_backend": usable_backend,
            "degraded_fallback": degraded_fallback,
            "platform_degraded": platform_degraded,
        }

    if target not in capabilities:
        return _result(False, "unsupported_backend", None, False, False)

    if capabilities[target]:
        cuda_bound_backends = _detect_cuda_bound_backends(installed_packages)
        if target != "cuda" and cuda_bound_backends:
            return _result(False, "unsupported_backend", None, False, False)
        return _result(True, None, target, False, False)

    if capabilities.get("cpu"):
        return _result(True, None, "cpu", True, True)

    return _result(False, "unsupported_backend", None, False, False)
