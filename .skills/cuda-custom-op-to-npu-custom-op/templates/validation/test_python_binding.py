"""Optional example PyTorch pybind11 adapter smoke tests for NPU custom operator.

Tests:
  1. Import succeeds
  2. Basic call with random input produces valid output
  3. Repeated calls (5x) with synchronize, catching stream sync or zero-output bugs
  4. Input validation (wrong device, wrong dtype, wrong shape)

Customization points:
  - MODULE_NAME / OP_NAME: your module and function
  - make_valid_inputs(): returns valid tensors for your operator
  - EXPECTED_SHAPE: expected output shape given valid inputs
"""
import os
import re
import sys
import traceback
from collections.abc import Callable
from typing import Protocol, cast

import torch


DEBUG_TAIL_LINES = 20
PATH_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_{}$])(?:/[^\s`'\"]+|[A-Za-z]:\\[^\s`'\"]+)")
SHOW_LOCAL_TRACEBACK = os.environ.get("CUSTOM_OP_VALIDATION_DEBUG_TRACEBACK") == "1"
GATE_SCOPE = "python_binding_smoke"


def print_local_gate() -> None:
    print(f"gate_scope={GATE_SCOPE}")
    print("evidence_scope=local_check")


class NpuModule(Protocol):
    def is_available(self) -> bool: ...

    def synchronize(self) -> None: ...


def get_npu_module() -> NpuModule | None:
    return getattr(torch, "npu", None)


def npu_is_available() -> bool:
    npu_module = get_npu_module()
    return bool(npu_module is not None and npu_module.is_available())


def synchronize_npu() -> None:
    npu_module = get_npu_module()
    if npu_module is not None:
        npu_module.synchronize()


def sanitize_debug_text(text: str) -> str:
    return PATH_TOKEN_RE.sub("<path>", text)


def format_exception(exc: BaseException) -> str:
    summary = f"{type(exc).__name__}: {sanitize_debug_text(str(exc))}"
    if not SHOW_LOCAL_TRACEBACK:
        return summary + " (set CUSTOM_OP_VALIDATION_DEBUG_TRACEBACK=1 for a sanitized local-debug traceback)"
    traceback_text = sanitize_debug_text("".join(traceback.format_exception(exc)))
    lines = traceback_text.strip().splitlines()
    tail = lines[-DEBUG_TAIL_LINES:]
    if len(lines) > DEBUG_TAIL_LINES:
        tail.insert(0, f"<{len(lines) - DEBUG_TAIL_LINES} earlier traceback lines omitted>")
    return summary + "\n" + "\n".join(tail)

# ============================================================================
# CONFIGURATION
# ============================================================================

MODULE_NAME = "{{MODULE_NAME}}"
OP_NAME = "{{op_name}}"

HAS_NPU = npu_is_available()
DEVICE = "npu" if HAS_NPU else "cpu"

EXPECTED_SHAPE: tuple[int, ...] = (32, 32)  # Customize: expected output shape


def make_valid_inputs() -> dict[str, torch.Tensor]:
    """Return a dict of valid input tensors on NPU for your operator.

    Customize this for your operator's signature.
    """
    return {
        "input_0": torch.randn(32, 32, device=DEVICE),
    }


VALID_ATTRS: dict[str, int | float | bool | str] = {}  # Customize keyword arguments for your op.


# ============================================================================
# TESTS
# ============================================================================


def get_op_fn() -> Callable[..., torch.Tensor | None]:
    """Import and return the operator function."""
    import importlib
    mod = importlib.import_module(MODULE_NAME)
    return cast(Callable[..., torch.Tensor | None], getattr(mod, OP_NAME))


def test_import() -> bool:
    """Test 1: Module imports without error."""
    try:
        import importlib
        _ = importlib.import_module(MODULE_NAME)
        print_local_gate()
        print("[CHECK_OK] Import succeeded")
        return True
    except Exception as e:
        print(f"[FAIL] Import failed: {format_exception(e)}")
        return False


def test_basic_call() -> bool:
    """Test 2: Basic call with random input returns valid tensor."""
    if not HAS_NPU:
        print("[FAIL] NPU not available for custom-op binding test")
        return False

    op_fn = get_op_fn()
    inputs = make_valid_inputs()
    result = op_fn(**inputs, **VALID_ATTRS)
    synchronize_npu()

    if result is None:
        print("[FAIL] Operator returned None")
        return False

    if result.shape != EXPECTED_SHAPE:
        print(f"[FAIL] Expected shape {EXPECTED_SHAPE}, got {result.shape}")
        return False

    if bool(result.isnan().any().item()):
        print("[FAIL] Output contains NaN")
        return False

    if bool(result.isinf().any().item()):
        print("[FAIL] Output contains Inf")
        return False

    print_local_gate()
    print(
        f"[CHECK_OK] Basic call: shape={result.shape}, range="
        + f"[{result.min().item():.6e}, {result.max().item():.6e}]"
    )
    return True


def test_repeated_calls() -> bool:
    """Test 3: Repeated calls (5x) with identical inputs, catching nondeterminism."""
    if not HAS_NPU:
        print("[FAIL] NPU not available for repeated-call test")
        return False

    op_fn = get_op_fn()
    base_inputs = make_valid_inputs()
    results: list[torch.Tensor] = []

    for _ in range(5):
        inputs = {name: tensor.clone() for name, tensor in base_inputs.items()}
        result = op_fn(**inputs, **VALID_ATTRS)
        synchronize_npu()
        if result is None:
            print("[FAIL] Operator returned None")
            return False
        results.append(result.cpu())

    all_nonzero = all(r.abs().sum().item() > 0 for r in results)
    if not all_nonzero:
        zero_runs = [i for i, r in enumerate(results) if r.abs().sum().item() == 0]
        print(f"[FAIL] Zero output on calls: {zero_runs} (stream sync bug?)")
        return False

    reference = results[0]
    for index, result in enumerate(results[1:], start=1):
        if not bool((reference == result).all().item()):
            max_diff = (reference - result).abs().max().item()
            print(f"[FAIL] Repeated call {index} differed from call 0: max_abs_diff={max_diff:.6e}")
            return False

    print_local_gate()
    print("[CHECK_OK] Repeated calls (5x): identical inputs produced identical non-zero output")
    return True


def test_wrong_device() -> bool:
    """Test 4a: CPU tensor should raise an error or be handled gracefully."""
    op_fn = get_op_fn()
    cpu_input = torch.randn(*EXPECTED_SHAPE, device="cpu")
    try:
        _ = op_fn(input_0=cpu_input, **VALID_ATTRS)
        print("[FAIL] No error on CPU input, operator silently accepted wrong device")
        return False
    except (RuntimeError, TypeError) as e:
        print_local_gate()
        print(f"[CHECK_OK] Wrong device rejected: {type(e).__name__}")
        return True


def test_wrong_dtype() -> bool:
    """Test 4b: Wrong dtype should raise an error."""
    if not HAS_NPU:
        print("[FAIL] NPU not available for dtype validation test")
        return False

    op_fn = get_op_fn()
    bad_input = torch.randn(*EXPECTED_SHAPE, device=DEVICE).double()
    try:
        _ = op_fn(input_0=bad_input, **VALID_ATTRS)
        print("[FAIL] No error on float64 input, operator silently accepted wrong dtype")
        return False
    except (RuntimeError, TypeError) as e:
        print_local_gate()
        print(f"[CHECK_OK] Wrong dtype rejected: {type(e).__name__}")
        return True


def test_wrong_shape() -> bool:
    """Test 4c: Wrong shape should raise an error."""
    if not HAS_NPU:
        print("[FAIL] NPU not available for shape validation test")
        return False

    op_fn = get_op_fn()
    bad_input = torch.randn(1, device=DEVICE)
    try:
        _ = op_fn(input_0=bad_input, **VALID_ATTRS)
        print("[FAIL] No error on 1D scalar input, operator silently accepted wrong shape")
        return False
    except (RuntimeError, TypeError) as e:
        print_local_gate()
        print(f"[CHECK_OK] Wrong shape rejected: {type(e).__name__}")
        return True


if __name__ == "__main__":
    print("=" * 60)
    print(f"Python Binding Tests: {MODULE_NAME}.{OP_NAME}")
    print_local_gate()
    print("=" * 60)

    tests: list[Callable[[], bool]] = [
        test_import,
        test_basic_call,
        test_repeated_calls,
        test_wrong_device,
        test_wrong_dtype,
        test_wrong_shape,
    ]

    ok_checks = 0
    failed = 0
    for test_fn in tests:
        print(f"\n--- {test_fn.__doc__ or test_fn.__name__} ---")
        try:
            if test_fn():
                ok_checks += 1
            else:
                failed += 1
        except Exception as e:
            print(f"[FAIL] Unexpected exception: {format_exception(e)}")
            failed += 1

    print(f"\n{'=' * 60}")
    print(f"Check results: {ok_checks} ok, {failed} failed")
    print_local_gate()
    print(f"python_binding_smoke_check={'CHECK_OK' if failed == 0 else 'CHECK_FAILED'}")
    print("adapter smoke evidence; pair with manifest closure, same-run coverage, project tests, report parity, validation, and measured speedup or slowdown as needed")
    print("=" * 60)
    sys.exit(0 if failed == 0 else 1)
