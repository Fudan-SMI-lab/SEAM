from __future__ import annotations

# pyright: reportAny=false, reportExplicitAny=false, reportMissingTypeStubs=false

from pathlib import Path
from typing import Any

import torch
from torch.utils.data import DataLoader
from torchvision import transforms

from .dataset import SyntheticClassificationDataset


def _build_train_transform() -> transforms.Compose:
    return transforms.Compose(
        [
            transforms.RandomHorizontalFlip(p=0.5),
            transforms.Normalize(mean=[0.5, 0.5, 0.5], std=[0.25, 0.25, 0.25]),
        ]
    )


def _build_eval_transform() -> transforms.Compose:
    return transforms.Compose(
        [
            transforms.Normalize(mean=[0.5, 0.5, 0.5], std=[0.25, 0.25, 0.25]),
        ]
    )


def create_dataloaders(
    config: dict[str, Any],
    project_root: str | Path,
) -> dict[str, DataLoader[tuple[torch.Tensor, torch.Tensor]]]:
    data_config = dict(config["data"])
    prepared_stats_path = Path(project_root) / str(data_config["prepared_stats_path"])
    image_size = int(data_config["image_size"])
    channels = int(data_config["channels"])
    num_classes = int(data_config["num_classes"])
    num_workers = int(data_config["num_workers"])
    persistent_workers = bool(data_config["persistent_workers"]) and num_workers > 0

    train_dataset = SyntheticClassificationDataset(
        num_samples=int(data_config["train_samples"]),
        image_size=image_size,
        channels=channels,
        num_classes=num_classes,
        seed=17,
        transform=_build_train_transform(),
        prepared_stats_path=prepared_stats_path,
    )
    val_dataset = SyntheticClassificationDataset(
        num_samples=int(data_config["val_samples"]),
        image_size=image_size,
        channels=channels,
        num_classes=num_classes,
        seed=29,
        transform=_build_eval_transform(),
        prepared_stats_path=prepared_stats_path,
    )

    train_loader = DataLoader(
        train_dataset,
        batch_size=int(data_config["batch_size"]),
        shuffle=True,
        num_workers=num_workers,
        persistent_workers=persistent_workers,
        pin_memory=False,
    )
    val_loader = DataLoader(
        val_dataset,
        batch_size=int(data_config["eval_batch_size"]),
        shuffle=False,
        num_workers=num_workers,
        persistent_workers=persistent_workers,
        pin_memory=False,
    )
    return {"train": train_loader, "val": val_loader}
