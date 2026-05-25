from __future__ import annotations

# pyright: reportAny=false, reportExplicitAny=false, reportImplicitOverride=false, reportUnknownMemberType=false

from typing import Any

import torch
from torch import nn


class ConvBackbone(nn.Module):
    """Compact convolutional backbone with explicit CUDA assumptions."""

    def __init__(self, input_channels: int, channels: list[int], dropout: float) -> None:
        super().__init__()
        blocks: list[nn.Module] = []
        in_channels = input_channels
        for out_channels in channels:
            blocks.extend(
                [
                    nn.Conv2d(in_channels, out_channels, kernel_size=3, padding=1),
                    nn.BatchNorm2d(out_channels),
                    nn.ReLU(inplace=True),
                    nn.MaxPool2d(kernel_size=2),
                ]
            )
            in_channels = out_channels

        self.features: nn.Sequential = nn.Sequential(*blocks)
        self.dropout: nn.Dropout = nn.Dropout(p=dropout)
        self.pool: nn.AdaptiveAvgPool2d = nn.AdaptiveAvgPool2d((1, 1))
        self.runtime_device: torch.device = torch.device("cuda")
        self.output_dim: int = channels[-1]

    def forward(self, inputs: torch.Tensor) -> torch.Tensor:
        features: torch.Tensor = self.features(inputs)
        features = self.pool(features)
        features = torch.flatten(features, start_dim=1)
        return self.dropout(features)


def build_backbone(config: dict[str, Any]) -> ConvBackbone:
    return ConvBackbone(
        input_channels=int(config["input_channels"]),
        channels=[int(value) for value in config["channels"]],
        dropout=float(config["dropout"]),
    )
