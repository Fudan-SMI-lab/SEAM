"""Example test: NPU kernel output vs CPU reference implementation.

This is a Python/PyTorch-style example template. It fits adapters that
returns tensors that can be driven from Python. For native C++ projects,
framework plugins, or non-PyTorch runtimes, keep the same CPU-reference and
diff-comparison idea but replace npu_call() with an equivalent project driver.

Uses subprocess per shape to avoid CANN runtime binary caching issues
(CANN caches compiled kernel binaries per process; different shapes need
separate processes to get fresh compilations).

Customization points:
  - SHAPES: list of (shape_tuple, attr_dict) to test
  - THRESHOLD: max acceptable absolute difference
  - cpu_reference(): your CPU reference implementation
  - npu_call(): your NPU kernel invocation
  - Environment variables for CANN paths
"""
import os
import re
import subprocess
import sys
from collections.abc import Callable
from typing import Protocol, cast

import numpy as np
import numpy.typing as npt
import torch


ArrayF32 = npt.NDArray[np.float32]
Attrs = dict[str, float]
Inputs = dict[str, ArrayF32]
Shape = tuple[int, ...]
ShapeCase = tuple[Shape, Attrs]
DEBUG_TAIL_LINES = 20
PATH_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_{}$])(?:/[^\s`'\"]+|[A-Za-z]:\\[^\s`'\"]+)")
GATE_SCOPE = "kernel_cpu_reference"


def print_local_gate() -> None:
    print(f"gate_scope={GATE_SCOPE}")
    print("evidence_scope=local_check")


class NpuModule(Protocol):
    def synchronize(self) -> None: ...


def get_npu_module() -> NpuModule | None:
    return getattr(torch, "npu", None)


def synchronize_npu() -> None:
    npu_module = get_npu_module()
    if npu_module is not None:
        npu_module.synchronize()


def seed_torch(seed: int) -> None:
    manual_seed = cast(Callable[[int], object], getattr(torch, "manual_seed"))
    _ = manual_seed(seed)


def sanitize_debug_text(text: str) -> str:
    return PATH_TOKEN_RE.sub("<path>", text)


def format_debug_tail(label: str, text: str) -> str:
    stripped = sanitize_debug_text(text).strip()
    if not stripped:
        return f"{label}: <empty>"
    lines = stripped.splitlines()
    omitted = max(0, len(lines) - DEBUG_TAIL_LINES)
    tail = lines[-DEBUG_TAIL_LINES:]
    prefix = f"{label}: sanitized local-debug tail"
    if omitted:
        prefix += f" ({omitted} earlier lines omitted)"
    return prefix + "\n" + "\n".join(tail)


def tensor_from_array(array: ArrayF32) -> torch.Tensor:
    from_numpy = cast(Callable[[ArrayF32], torch.Tensor], getattr(torch, "from_numpy"))
    return from_numpy(array).to("npu")


def tensor_to_array(tensor: torch.Tensor) -> ArrayF32:
    cpu_tensor = tensor.detach().cpu()
    numpy_fn = cast(Callable[[], object], getattr(cpu_tensor, "numpy"))
    return np.asarray(numpy_fn(), dtype=np.float32)

# ============================================================================
# CONFIGURATION: customize these for your operator
# ============================================================================

MODULE_NAME = "{{MODULE_NAME}}"  # e.g., "custom_ops_lib"
OP_NAME = "{{op_name}}"          # e.g., "custom_op_entry"

# Test matrix: (shape_tuple, attributes_dict)
# Customize shapes and attributes for your operator
SHAPES: list[ShapeCase] = [
    ((16, 16), {"attr1": 10.0, "attr2": 10.0}),
    ((32, 32), {"attr1": 10.0, "attr2": 10.0}),
    ((64, 64), {"attr1": 10.0, "attr2": 10.0}),
    ((128, 128), {"attr1": 15.0, "attr2": 15.0}),
    ((256, 256), {"attr1": 20.0, "attr2": 20.0}),
]

# Maximum acceptable absolute difference between NPU and CPU outputs
THRESHOLD = 1e-3

# Environment variables for CANN runtime (customize paths)
CANN_ENV: dict[str, str] = {
    # "CANN_INSTALL_PATH": "{{CANN_INSTALL_PATH}}",
    # "ASCEND_CUSTOM_OPP_PATH": "{{CUSTOM_OPP_PATH}}",
    # "LD_LIBRARY_PATH": "{{CUSTOM_OPP_PATH}}/op_api/lib",
}

# ============================================================================
# CPU REFERENCE: replace with your operator's CPU implementation
# ============================================================================


def cpu_reference(inputs: Inputs, attrs: Attrs) -> ArrayF32:
    """{{CPU_REFERENCE_FUNC}}

    Args:
        inputs: dict of numpy arrays (your operator's inputs)
        attrs: dict of scalar attributes

    Returns:
        numpy array with expected output
    """
    _ = (inputs, attrs)
    raise NotImplementedError(
        "Replace this with your CPU reference implementation. "
        + "Example: a pure-numpy loop that computes the same result as your kernel."
    )


# ============================================================================
# NPU CALL: replace with your operator's NPU invocation
# ============================================================================


def npu_call(inputs: Inputs, attrs: Attrs) -> ArrayF32:
    """{{NPU_CALL}}

    Args:
        inputs: dict of numpy arrays (will be moved to NPU tensors)
        attrs: dict of scalar attributes

    Returns:
        numpy array (result moved back to CPU)
    """
    import importlib
    mod = importlib.import_module(MODULE_NAME)
    op_fn = cast(Callable[..., torch.Tensor], getattr(mod, OP_NAME))

    tensors: dict[str, torch.Tensor] = {k: tensor_from_array(v) for k, v in inputs.items()}
    result = op_fn(**tensors, **attrs)
    synchronize_npu()
    return tensor_to_array(result)


# ============================================================================
# TEST RUNNER
# ============================================================================


def run_single(shape: Shape, attrs: Attrs) -> int:
    """Run a single shape test in this process."""
    np.random.seed(42)
    seed_torch(42)

    inputs: Inputs = {
        "input_0": np.random.randn(*shape).astype(np.float32) * 0.01,
    }

    cpu_out = cpu_reference(inputs, attrs)
    npu_out = npu_call(inputs, attrs)

    abs_diff = np.abs(npu_out - cpu_out)
    max_diff = float(cast(float, abs_diff.max()))
    mean_diff = float(cast(float, abs_diff.mean()))
    rel_diff = max_diff / (float(cast(float, np.abs(cpu_out).max())) + 1e-10)

    check_ok = max_diff < THRESHOLD
    status = "CHECK_OK" if check_ok else "CHECK_FAILED"
    attr_str = " ".join(f"{k}={v}" for k, v in attrs.items())
    print_local_gate()
    message = (
        f"Shape {shape} {attr_str}: "
        + f"max|diff|={max_diff:.6e} mean|diff|={mean_diff:.6e} "
        + f"rel_err={rel_diff:.6e} [{status}]"
    )
    print(message)

    if not check_ok:
        print(f"  CPU range: [{cpu_out.min():.6f}, {cpu_out.max():.6f}]")
        print(f"  NPU range: [{npu_out.min():.6f}, {npu_out.max():.6f}]")

    return 0 if check_ok else 1


def test_all_shapes() -> int:
    """Launch each shape in a separate subprocess (avoids CANN binary caching)."""
    print("=" * 60)
    print(f"Kernel CPU Reference Validation: {MODULE_NAME}.{OP_NAME}")
    print_local_gate()
    print("=" * 60)

    failed = 0
    for shape, attrs in SHAPES:
        args = [sys.executable, __file__, "--run-single"]
        args += [str(d) for d in shape]
        args += [f"{k}={v}" for k, v in attrs.items()]

        env = {**os.environ, **CANN_ENV}
        if "LD_LIBRARY_PATH" in CANN_ENV:
            env["LD_LIBRARY_PATH"] = f'{CANN_ENV["LD_LIBRARY_PATH"]}:{os.environ.get("LD_LIBRARY_PATH", "")}'

        result = subprocess.run(args, capture_output=True, text=True, env=env)
        print(format_debug_tail("STDOUT", result.stdout))
        if result.returncode != 0:
            failed += 1
            print(format_debug_tail("STDERR", result.stderr))

    print_local_gate()
    print(f"\nDirect CPU-reference gate complete. failed_shapes={failed}")
    print(f"kernel_cpu_reference_check={'CHECK_OK' if failed == 0 else 'CHECK_FAILED'}")
    print("direct kernel parity evidence; pair with adapter/caller wiring, same-run coverage, project tests, report parity, validation, and measured speedup or slowdown as needed")
    return 0 if failed == 0 else 1


def parse_single_args(argv: list[str]) -> tuple[Shape, Attrs]:
    """Parse --run-single shape... key=value... from command line."""
    shape_parts: list[int] = []
    attrs: Attrs = {}
    for arg in argv:
        if "=" in arg:
            k, v = arg.split("=", 1)
            attrs[k] = float(v)
        else:
            shape_parts.append(int(arg))
    return tuple(shape_parts), attrs


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--run-single":
        shape, attrs = parse_single_args(sys.argv[2:])
        sys.exit(run_single(shape, attrs))
    else:
        sys.exit(test_all_shapes())
