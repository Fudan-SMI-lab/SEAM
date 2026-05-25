from __future__ import annotations

# pyright: reportAny=false, reportExplicitAny=false, reportImplicitRelativeImport=false

import torch

from src.models.classifier import build_classifier


def test_model_forward_pass() -> None:
    config: dict[str, object] = {
        "backbone": {
            "input_channels": 3,
            "channels": [8, 16, 32],
            "dropout": 0.1,
        },
        "classifier": {
            "hidden_dim": 16,
            "num_classes": 5,
        },
    }
    model = build_classifier(config)
    inputs = torch.randn(4, 3, 32, 32)

    outputs: torch.Tensor = model(inputs)

    assert outputs.shape == (4, 5)
