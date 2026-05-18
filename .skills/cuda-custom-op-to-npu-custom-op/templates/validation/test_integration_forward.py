"""Example integration test comparing baseline and custom-op paths.

This is a Python/PyTorch-style integration template. For other frameworks,
replace setup(), baseline_call(), custom_op_call(), synchronization, and
gradient checks with the framework's equivalent driver while keeping the same
baseline/custom-op comparison idea.

Tests:
  1. Forward output difference between {{BASELINE_MODE}} and {{CUSTOM_OP_MODE}}
  2. Backward: loss and grad_max comparison

Customization points:
  - SETUP_CODE section: initialize model, inputs, parameters
  - baseline_call(): run the original/reference forward pass
  - custom_op_call(): run the custom Ascend op forward pass
"""
import sys
from collections.abc import Callable
from typing import Protocol, cast

import numpy as np
import numpy.typing as npt
import torch


ArrayF32 = npt.NDArray[np.float32]
TensorDict = dict[str, torch.Tensor]
SetupResult = tuple[TensorDict, list[torch.Tensor]]
GATE_SCOPE = "integration_forward_backward"


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


def seed_torch(seed: int) -> None:
    manual_seed = cast(Callable[[int], object], getattr(torch, "manual_seed"))
    _ = manual_seed(seed)


def tensor_to_array(tensor: torch.Tensor) -> ArrayF32:
    cpu_tensor = tensor.detach().cpu()
    numpy_fn = cast(Callable[[], object], getattr(cpu_tensor, "numpy"))
    return np.asarray(numpy_fn(), dtype=np.float32)


def backward_tensor(tensor: torch.Tensor) -> None:
    backward = cast(Callable[[], object], tensor.backward)
    _ = backward()


def zero_grads(params: list[torch.Tensor]) -> None:
    for param in params:
        grad = param.grad
        if grad is not None:
            _ = grad.zero_()


def clone_grads(params: list[torch.Tensor]) -> list[torch.Tensor]:
    grads: list[torch.Tensor] = []
    for param in params:
        grad = param.grad
        if grad is None:
            raise RuntimeError("Expected gradient after backward pass")
        grads.append(grad.clone().cpu())
    return grads

# ============================================================================
# CONFIGURATION
# ============================================================================

HAS_NPU = npu_is_available()
DEVICE = "npu" if HAS_NPU else "cpu"

FORWARD_THRESHOLD = 1e-5
BACKWARD_LOSS_THRESHOLD = 1e-5
BACKWARD_GRAD_THRESHOLD = 1e-4


# ============================================================================
# SETUP: customize for your project
# ============================================================================

def setup() -> SetupResult:
    """{{SETUP_CODE}}

    Returns:
        tuple: (inputs_dict, params_requiring_grad)
        - inputs_dict: all tensors needed for forward pass
        - params_requiring_grad: list of tensors to check gradients on
    """
    seed_torch(42)

    raise NotImplementedError(
        "Replace with your project setup: create model inputs, "
        + "parameters, and any state needed for forward/backward."
    )


# ============================================================================
# FORWARD PATHS
# ============================================================================


def baseline_call(inputs_dict: TensorDict) -> torch.Tensor:
    """{{BASELINE_CALL}}

    Run the original/reference forward pass for {{BASELINE_MODE}}.
    Returns: output tensor
    """
    _ = inputs_dict
    raise NotImplementedError(
        "Replace with your project's baseline/reference forward call."
    )


def custom_op_call(inputs_dict: TensorDict) -> torch.Tensor:
    """{{CUSTOM_OP_CALL}}

    Run the Ascend custom-op forward pass for {{CUSTOM_OP_MODE}}.
    Returns: output tensor
    """
    _ = inputs_dict
    raise NotImplementedError(
        "Replace with your custom-op forward call."
    )


# ============================================================================
# TEST: FORWARD COMPARISON
# ============================================================================


def test_forward() -> bool:
    """Compare forward output: baseline vs custom op."""
    if not HAS_NPU:
        print("[FAIL] NPU not available for forward integration test")
        return False

    inputs_dict, _ = setup()

    seed_torch(42)
    out_baseline = baseline_call(inputs_dict)

    seed_torch(42)
    out_custom = custom_op_call(inputs_dict)

    synchronize_npu()

    baseline_np = tensor_to_array(out_baseline)
    custom_np = tensor_to_array(out_custom)

    abs_diff = np.abs(baseline_np - custom_np)
    max_diff = float(cast(float, abs_diff.max()))
    mean_diff = float(cast(float, abs_diff.mean()))

    forward_ok = max_diff < FORWARD_THRESHOLD
    status = "CHECK_OK" if forward_ok else "CHECK_FAILED"
    print_local_gate()
    print("Forward comparison:")
    print(f"  Baseline range:  [{baseline_np.min():.6e}, {baseline_np.max():.6e}]")
    print(f"  Custom range:    [{custom_np.min():.6e}, {custom_np.max():.6e}]")
    print(f"  max|diff|:    {max_diff:.6e}")
    print(f"  mean|diff|:   {mean_diff:.6e}")
    print(f"  [{status}]")

    return forward_ok


# ============================================================================
# TEST: BACKWARD COMPARISON
# ============================================================================


def test_backward() -> bool:
    """Compare backward: loss value and max gradient between baseline and custom op."""
    if not HAS_NPU:
        print("[FAIL] NPU not available for backward integration test")
        return False

    inputs_dict, params = setup()

    # Baseline backward
    for param in params:
        _ = param.requires_grad_(True)
    zero_grads(params)

    seed_torch(42)
    out_baseline = baseline_call(inputs_dict)
    loss_baseline = (out_baseline ** 2).sum()
    backward_tensor(loss_baseline)
    baseline_grads = clone_grads(params)
    loss_baseline_val = float(loss_baseline.item())

    # Reset grads
    zero_grads(params)

    # Custom-op backward
    seed_torch(42)
    out_custom = custom_op_call(inputs_dict)
    loss_custom = (out_custom ** 2).sum()
    backward_tensor(loss_custom)

    synchronize_npu()

    custom_grads = clone_grads(params)
    loss_custom_val = float(loss_custom.item())

    # Compare losses
    loss_diff = abs(loss_baseline_val - loss_custom_val)
    loss_ok = loss_diff < BACKWARD_LOSS_THRESHOLD
    loss_status = "CHECK_OK" if loss_ok else "CHECK_FAILED"
    print_local_gate()
    print("Backward loss comparison:")
    print(f"  Baseline loss: {loss_baseline_val:.6e}")
    print(f"  Custom loss:   {loss_custom_val:.6e}")
    print(f"  |diff|:       {loss_diff:.6e}")
    print(f"  [{loss_status}]")

    # Compare gradients
    grad_pass = True
    for index, (baseline_grad, custom_grad) in enumerate(zip(baseline_grads, custom_grads)):
        grad_diff = float((baseline_grad - custom_grad).abs().max().item())
        grad_ok = grad_diff < BACKWARD_GRAD_THRESHOLD
        g_status = "CHECK_OK" if grad_ok else "CHECK_FAILED"
        print_local_gate()
        print(f"  Param[{index}] grad max|diff|: {grad_diff:.6e} [{g_status}]")
        if not grad_ok:
            grad_pass = False

    return loss_ok and grad_pass


if __name__ == "__main__":
    print("=" * 60)
    print("Integration Forward/Backward Tests")
    print_local_gate()
    print("=" * 60)

    results: list[bool] = []

    print("\n--- Test 1: Forward comparison ---")
    results.append(test_forward())

    print("\n--- Test 2: Backward comparison ---")
    results.append(test_backward())

    ok_checks = sum(results)
    failed = len(results) - ok_checks
    print(f"\n{'=' * 60}")
    print(f"Check results: {ok_checks} ok, {failed} failed")
    print_local_gate()
    print(f"integration_forward_backward_check={'CHECK_OK' if failed == 0 else 'CHECK_FAILED'}")
    print("integration comparison evidence; pair with per-manifest closure, positive same-run runtime coverage, project/e2e tests, report parity, validation, and measured speedup or slowdown as needed")
    print("=" * 60)
    sys.exit(0 if failed == 0 else 1)
