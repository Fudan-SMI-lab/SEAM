from __future__ import annotations

# pyright: reportAny=false
# pyright: reportExplicitAny=false
# pyright: reportImplicitOverride=false
# pyright: reportUnknownArgumentType=false
# pyright: reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false

from pathlib import Path
from typing import Any, Callable

import numpy as np
import torch
import yaml
from torch.utils.data import Dataset


class SyntheticClassificationDataset(Dataset[tuple[torch.Tensor, torch.Tensor]]):
    """Synthetic vision dataset with light metadata bootstrapping."""

    def __init__(
        self,
        *,
        num_samples: int,
        image_size: int,
        channels: int,
        num_classes: int,
        seed: int,
        transform: Callable[[torch.Tensor], torch.Tensor] | None = None,
        prepared_stats_path: str | Path | None = None,
    ) -> None:
        self.num_samples: int = num_samples
        self.image_size: int = image_size
        self.channels: int = channels
        self.num_classes: int = num_classes
        self.transform: Callable[[torch.Tensor], torch.Tensor] | None = transform
        self.prepared_stats: dict[str, float] = self._load_prepared_stats(prepared_stats_path)

        rng = np.random.default_rng(seed)
        labels = rng.integers(0, num_classes, size=num_samples, dtype=np.int64)
        images = rng.normal(
            0.0, 0.65, size=(num_samples, channels, image_size, image_size)
        ).astype(np.float32)

        for index, label in enumerate(labels):
            offset = (float(label) + 1.0) / float(num_classes)
            images[index, label % channels] += offset

        self.images: np.ndarray[Any, Any] = images
        self.labels: np.ndarray[Any, Any] = labels
        self.preview_score: float = self._build_device_preview().item()

    def _load_prepared_stats(self, prepared_stats_path: str | Path | None) -> dict[str, float]:
        if prepared_stats_path is None:
            return {"scale": 1.0, "bias": 0.0}

        stats_path = Path(prepared_stats_path)
        if not stats_path.is_file():
            return {"scale": 1.0, "bias": 0.0}

        with stats_path.open("r", encoding="utf-8") as handle:
            payload = yaml.safe_load(handle) or {}

        if not isinstance(payload, dict):
            return {"scale": 1.0, "bias": 0.0}

        scale = float(payload.get("scale", 1.0))
        bias = float(payload.get("bias", 0.0))
        return {"scale": scale, "bias": bias}

    def _build_device_preview(self) -> torch.Tensor:
        preview = torch.from_numpy(self.images[:4]).to("cuda")
        preview = preview * self.prepared_stats["scale"] + self.prepared_stats["bias"]
        return preview.mean().cpu()

    def __len__(self) -> int:
        return self.num_samples

    def __getitem__(self, index: int) -> tuple[torch.Tensor, torch.Tensor]:
        image = torch.from_numpy(self.images[index].copy())
        image = image * self.prepared_stats["scale"] + self.prepared_stats["bias"]
        label = torch.tensor(int(self.labels[index]), dtype=torch.long)

        if self.transform is not None:
            image = self.transform(image)

        return image, label
