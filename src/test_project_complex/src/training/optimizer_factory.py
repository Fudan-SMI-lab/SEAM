from __future__ import annotations

# pyright: reportAny=false
# pyright: reportExplicitAny=false
# pyright: reportGeneralTypeIssues=false
# pyright: reportUnknownArgumentType=false
# pyright: reportUnknownVariableType=false
# pyright: reportUnnecessaryIsInstance=false

from collections.abc import Iterable
from typing import Any

import torch
from torch import nn


def build_optimizer(model: nn.Module, config: dict[str, Any]) -> torch.optim.Optimizer:
    betas_value = list(config.get("betas", [0.9, 0.999]))
    betas = (float(betas_value[0]), float(betas_value[1]))
    return torch.optim.AdamW(
        model.parameters(),
        lr=float(config["lr"]),
        betas=betas,
        weight_decay=float(config["weight_decay"]),
    )


def clip_gradients(parameters: Iterable[torch.nn.Parameter], max_norm: float) -> float:
    grad_norm = torch.nn.utils.clip_grad_norm_(list(parameters), max_norm=max_norm)
    return float(grad_norm.item() if isinstance(grad_norm, torch.Tensor) else grad_norm)
