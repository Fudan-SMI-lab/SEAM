"""Example end-to-end pipeline test: project baseline vs custom-op path.

This is a Python/PyTorch-style pipeline template. For non-PyTorch projects,
replace the setup, synchronization, and training or inference loop with the
host framework's equivalent driver.

Runs N epochs or iterations of a real business pipeline and compares metrics
between {{BASELINE_MODE}} and {{CUSTOM_OP_MODE}}.

Customization points:
  - PIPELINE_SETUP: initialize model, optimizer, data
  - TRAIN_LOOP: one epoch of training
  - N_EPOCHS: number of epochs to run
  - LOSS_TOLERANCE: acceptable per-epoch loss difference
"""
import sys
from collections.abc import Callable
from typing import Protocol, cast

import torch


PipelineState = dict[str, object]
GATE_SCOPE = "end_to_end_pipeline"


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

# ============================================================================
# CONFIGURATION
# ============================================================================

N_EPOCHS = 5
LOSS_TOLERANCE = 1e-4

HAS_NPU = npu_is_available()
DEVICE = "npu" if HAS_NPU else "cpu"


# ============================================================================
# PIPELINE SETUP: customize for your project
# ============================================================================


def pipeline_setup(use_custom_op: bool) -> PipelineState:
    """{{PIPELINE_SETUP}}

    Initialize model, optimizer, data loader, and any state needed for training.

    Args:
        use_custom_op: if True, configure pipeline to use {{CUSTOM_OP_MODE}};
                       if False, use {{BASELINE_MODE}}.

    Returns:
        dict with keys: "model", "optimizer", "data", and any other state
    """
    _ = use_custom_op
    seed_torch(42)

    raise NotImplementedError(
        "Replace with your pipeline setup. Return a dict with model, "
        + "optimizer, data, and configuration for baseline vs custom-op mode."
    )


# ============================================================================
# TRAIN LOOP: customize for your project
# ============================================================================


def train_one_epoch(state: PipelineState) -> float:
    """{{TRAIN_LOOP}}

    Run one epoch of training and return the epoch loss.

    Args:
        state: dict returned by pipeline_setup()

    Returns:
        float: epoch loss value
    """
    _ = state
    raise NotImplementedError(
        "Replace with your training loop for one epoch. "
        + "Return the scalar loss value."
    )


# ============================================================================
# END-TO-END TEST
# ============================================================================


def run_pipeline(use_custom_op: bool) -> list[float]:
    """Run full training pipeline and collect per-epoch losses."""
    state = pipeline_setup(use_custom_op=use_custom_op)
    losses: list[float] = []
    for _ in range(N_EPOCHS):
        loss = train_one_epoch(state)
        losses.append(loss)
        synchronize_npu()
    return losses


def test_end_to_end() -> bool:
    """Compare per-epoch or per-iteration metrics: baseline vs custom op."""
    if not HAS_NPU:
        print("[FAIL] NPU not available for end-to-end custom-op test")
        return False

    print("Running baseline pipeline...")
    baseline_losses = run_pipeline(use_custom_op=False)

    print("Running custom-op pipeline...")
    custom_losses = run_pipeline(use_custom_op=True)

    print(f"\n{'Epoch':<8}{'Baseline':<16}{'Custom Op':<16}{'|Diff|':<14}{'Status'}")
    print("-" * 62)

    all_pass = True
    for epoch in range(N_EPOCHS):
        diff = abs(baseline_losses[epoch] - custom_losses[epoch])
        check_ok = diff < LOSS_TOLERANCE
        status = "CHECK_OK" if check_ok else "CHECK_FAILED"
        print_local_gate()
        if not check_ok:
            all_pass = False
        row = (
            f"{epoch:<8}{baseline_losses[epoch]:<16.6e}{custom_losses[epoch]:<16.6e}"
            + f"{diff:<14.6e}{status}"
        )
        print(row)

    # {{EXPECTED_LOSSES}}: optionally assert against known-good loss values
    print_local_gate()
    print(f"\nEnd-to-end pipeline check: {'CHECK_OK' if all_pass else 'CHECK_FAILED'}")
    return all_pass


if __name__ == "__main__":
    print("=" * 60)
    print("End-to-End Pipeline Test")
    print_local_gate()
    print("=" * 60)

    success = test_end_to_end()

    print_local_gate()
    print("end-to-end evidence; pair with manifest closure, same-run coverage, report parity, validation, and measured speedup or slowdown as needed")
    print("=" * 60)
    sys.exit(0 if success else 1)
