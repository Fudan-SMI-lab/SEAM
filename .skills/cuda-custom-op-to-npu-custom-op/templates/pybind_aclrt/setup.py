# pyright: reportImplicitOverride=false, reportUnknownVariableType=false
"""
Setup script for building the optional PyTorch pybind11 NPU custom operator adapter.

This template builds a C++ extension that links against:
  - Ascend CANN libraries (ascendcl, opapi, cust_opapi)
  - torch-npu, when current-stream access or PyTorch tensor integration is needed

Build-time lookup may use ASCEND_CUSTOM_OPP_PATH for generated headers and
link-time libcust_opapi.so lookup. At runtime, ASCEND_CUSTOM_OPP_PATH can help
the adapter find generated custom-op resources and kernel binaries, but it does
not make libcust_opapi.so discoverable to the dynamic loader. Runtime shared-
library resolution needs LD_LIBRARY_PATH, a package-relative rpath/runpath such
as $ORIGIN when packaging, or an explicit loader policy.

Placeholders:
{{MODULE_NAME}}        : Python module name, for example custom_ops_lib
{{VENDOR_NAME}}        : Vendor name registered in CANN
{{CANN_INSTALL_PATH}}  : Active CANN install root
{{CUSTOM_OPP_PATH}}    : Active generated vendor package path
{{TORCH_NPU_ROOT}}     : torch-npu package root

Usage:
  python setup.py build_ext --inplace
"""
import os
from pathlib import Path

# Disable torch device backend autoload before importing torch so torch_npu does
# not interfere with the build process.
_ = os.environ.setdefault("TORCH_DEVICE_BACKEND_AUTOLOAD", "0")

from setuptools import setup
from torch.utils.cpp_extension import BuildExtension, CppExtension


THIS_DIR = Path(__file__).resolve().parent

# ============================================================================
# Path Configuration
# ============================================================================
# Adjust ASCEND_ROOT to match your CANN installation path.
ASCEND_ROOT = Path(os.environ.get("CANN_INSTALL_PATH", "{{CANN_INSTALL_PATH}}"))


def _split_path_list(value: str) -> list[Path]:
    return [Path(part) for part in value.split(os.pathsep) if part]


def _candidate_vendor_root(path: Path) -> Path | None:
    if (path / "op_api" / "include").is_dir() and (path / "op_api" / "lib").is_dir():
        return path
    if path.name == "lib" and path.parent.name == "op_api":
        vendor_root = path.parent.parent
        if (vendor_root / "op_api" / "include").is_dir():
            return vendor_root
    return None


def _resolve_custom_opp_path() -> Path:
    values = _split_path_list(os.environ.get("ASCEND_CUSTOM_OPP_PATH", ""))
    ascend_opp_path = os.environ.get("ASCEND_OPP_PATH")
    if ascend_opp_path:
        opp_root = Path(ascend_opp_path)
        values.extend([
            opp_root / "vendors" / "{{VENDOR_NAME}}",
            opp_root / "opp" / "vendors" / "{{VENDOR_NAME}}",
        ])
    for value in values:
        candidate = _candidate_vendor_root(value)
        if candidate is not None:
            return candidate
    raise RuntimeError(
        "No validated custom OPP vendor root found; build/install the project OPP package "
        + "or set ASCEND_CUSTOM_OPP_PATH to a vendor root with op_api/include and op_api/lib. "
        + "This is implementation work, not completion."
    )


CUSTOM_OPP_PATH = _resolve_custom_opp_path()

# torch-npu package location. You can find it with:
# python -c "import torch_npu; print(torch_npu.__path__[0])"
TORCH_NPU_ROOT = Path(os.environ.get("TORCH_NPU_ROOT", "{{TORCH_NPU_ROOT}}"))

# Library directories needed for build-time linking.
# - devlib/lib64: core ACL runtime libraries
# - {{CUSTOM_OPP_PATH}}/op_api/lib: generated custom operator ACLNN wrapper library
# - torch_npu/lib: torch_npu shared libraries
LIBRARY_DIRS = [
    ASCEND_ROOT / "x86_64-linux" / "devlib",
    ASCEND_ROOT / "x86_64-linux" / "lib64",
    CUSTOM_OPP_PATH / "op_api" / "lib",
    TORCH_NPU_ROOT / "lib",
]

# Runtime rpath is intentionally limited to stable CANN and torch-npu library
# locations. Avoid embedding CUSTOM_OPP_PATH/op_api/lib here: custom OPP packages
# are environment-selected artifacts, and a build-time absolute path can make the
# adapter load stale or site-specific libcust_opapi.so at runtime. At import time,
# the dynamic loader resolves libcust_opapi.so via LD_LIBRARY_PATH, an embedded
# package-relative rpath/runpath such as $ORIGIN, or an explicit loader policy.
# ASCEND_CUSTOM_OPP_PATH is still useful for custom-op resource/kernel discovery,
# but it does not make libcust_opapi.so discoverable to the dynamic loader and
# is not a dynamic-linker search path for linked shared libraries.
RUNTIME_RPATH_DIRS = [
    ASCEND_ROOT / "x86_64-linux" / "devlib",
    ASCEND_ROOT / "x86_64-linux" / "lib64",
    TORCH_NPU_ROOT / "lib",
]


class CustomBuildExtension(BuildExtension):
    """Override to produce a flat .so file (no ABI suffix)."""
    def get_ext_filename(self, ext_name: str) -> str:
        return f"{ext_name.replace('.', os.sep)}.so"


_ = setup(
    name="{{MODULE_NAME}}",
    ext_modules=[
        CppExtension(
            name="{{MODULE_NAME}}",
            sources=[
                # pytorch_npu_helper.cpp: aclCreateTensor wrapper + ConvertType utility
                str(THIS_DIR / "pytorch_npu_helper.cpp"),
                # python_bind_op.cpp: main operator binding with ACLNN + ACLRT fallback
                str(THIS_DIR / "python_bind_op.cpp"),
            ],
            include_dirs=[
                str(THIS_DIR),
                # ACL headers (acl.h, acl_rt.h, etc.)
                str(ASCEND_ROOT / "include"),
                # ACLNN headers (acl_meta.h, aclnn status types)
                str(ASCEND_ROOT / "include" / "aclnn"),
                # Generated custom-op ACLNN headers.
                str(CUSTOM_OPP_PATH / "op_api" / "include"),
                # torch_npu headers (NPUStream.h, etc.)
                str(TORCH_NPU_ROOT / "include"),
            ],
            library_dirs=[str(path) for path in LIBRARY_DIRS],
            # Libraries to link:
#   ascendcl: ACL runtime (aclrtLaunchKernel, aclrtBinaryLoadFromFile, etc.)
#   cust_opapi: Your custom ACLNN operator wrapper (aclnn{{OP_NAME}}GetWorkspaceSize, etc.)
#   opapi: Base ACLNN operator API
#   torch_npu: torch_npu runtime (NPU stream management)
#   dl: POSIX dynamic-loader helpers for module-relative resource discovery
            libraries=["ascendcl", "cust_opapi", "opapi", "torch_npu", "dl"],
            extra_compile_args={"cxx": ["-std=c++17", "-Wno-unused-variable"]},
            # Embed only stable runtime library locations. CUSTOM_OPP_PATH is
            # used for build-time include/link lookup above, but it is excluded
            # from default rpath/runpath. Runtime libcust_opapi.so discovery uses
            # LD_LIBRARY_PATH, a package-relative $ORIGIN runpath when
            # packaging, or an explicit loader policy. ASCEND_CUSTOM_OPP_PATH is
            # for custom-op resources and kernel binaries, not DT_NEEDED lookup.
            extra_link_args=[
                *(f"-Wl,-rpath,{path}" for path in RUNTIME_RPATH_DIRS),
            ],
        )
    ],
    cmdclass={"build_ext": CustomBuildExtension},
)
