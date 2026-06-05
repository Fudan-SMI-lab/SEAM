"""Accelerator package context extraction for env context building."""

from __future__ import annotations

import os
import re
from typing import cast

# ── Recognized accelerator families ──────────────────────────────────────
# The full set of recognized accelerator package prefixes is controlled
# by the ``SEAM_ACCELERATOR_PACKAGE_PREFIXES`` environment variable so
# that each platform deployment can supply its own list.  The default
# value covers PPU, XPU, inference engines, kernel languages, and the
# CUDA ecosystem alongside the base torch framework.
_ACCELERATOR_PREFIXES: list[str] = sorted(
    {
        pfx.strip().lower()
        for pfx in os.environ.get(
            "SEAM_ACCELERATOR_PACKAGE_PREFIXES",
            "torch_ppu,ppukernel,ppuccl,ppu_,ppu,"
            "xpu,alixpu,vllm,triton,"
            "cuda,cudnn,nccl,"
            "torch,pytorch",
        ).split(",")
        if pfx.strip()
    },
    key=lambda x: ("z" + x) if x in ("torch", "pytorch") else x,
)


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

        * ``accelerator_packages`` — ``list[str]`` of normalized accelerator
          package names (lowercase, underscores), e.g.
          ``["torch_npu", "ppukernel", "vllm"]``.
        * ``accelerator_package_versions`` — ``dict[str, str]`` mapping
          normalized name to version string for recognized accelerator packages.

    Examples
    --------
    >>> extract_accelerator_context(["torch-npu==2.1.0", "torch==2.0.1", "ppukernel==1.0.0"])
    {'accelerator_packages': ['torch_npu', 'torch', 'ppukernel'],
     'accelerator_package_versions': {'torch_npu': '2.1.0', 'torch': '2.0.1', 'ppukernel': '1.0.0'}}
    """
    result: dict[str, object] = {}
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
