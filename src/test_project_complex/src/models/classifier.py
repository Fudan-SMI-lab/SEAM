from __future__ import annotations

# pyright: reportAny=false, reportExplicitAny=false,
# reportImplicitOverride=false, reportUnknownMemberType=false
from typing import Any

import torch  # pylint: disable=import-error; silent
from torch import nn  # pylint: disable=import-error; silent

from .backbone import build_backbone


class ImageClassifier(nn.Module):  # pylint: disable=too-few-public-methods; silent
    """Backbone + classification head wrapper."""

    def __init__(
        self, backbone: nn.Module, feature_dim: int, hidden_dim: int, num_classes: int
    ) -> None:
        super().__init__()
        self.backbone: nn.Module = backbone
        self.head: nn.Sequential = nn.Sequential(
            nn.Linear(feature_dim, hidden_dim),
            nn.ReLU(inplace=True),
            nn.Linear(hidden_dim, num_classes),
        )

    def forward(self, inputs: torch.Tensor) -> torch.Tensor:
        features: torch.Tensor = self.backbone(inputs)
        return self.head(features)


def build_classifier(config: dict[str, Any]) -> ImageClassifier:
    backbone_config = dict(config["backbone"])
    classifier_config = dict(config["classifier"])
    backbone = build_backbone(backbone_config)
    return ImageClassifier(
        backbone=backbone,
        feature_dim=backbone.output_dim,
        hidden_dim=int(classifier_config["hidden_dim"]),
        num_classes=int(classifier_config["num_classes"]),
    )
