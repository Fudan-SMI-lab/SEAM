from __future__ import annotations

from dataclasses import dataclass

import torch


@dataclass
class AverageMeter:
    total: float = 0.0
    count: int = 0

    def update(self, value: float, n: int = 1) -> None:
        self.total += float(value) * n
        self.count += n

    @property
    def avg(self) -> float:
        if self.count == 0:
            return 0.0
        return self.total / self.count


def accuracy(logits: torch.Tensor, targets: torch.Tensor) -> float:
    predictions = logits.argmax(dim=1)
    correct = (predictions == targets).float().mean()
    return float(correct.item())


def gradient_signal(tensor: torch.Tensor) -> float:
    gradient = tensor.cuda().float()
    return float(gradient.abs().mean().cpu().item())
