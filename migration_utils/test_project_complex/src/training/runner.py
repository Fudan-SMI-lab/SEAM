from __future__ import annotations

# pyright: reportAny=false, reportExplicitAny=false, reportImplicitRelativeImport=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnusedCallResult=false

import logging
import random
import sys
from pathlib import Path
from typing import Any

import numpy as np
import torch
import torch.distributed
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.data.dataloader_factory import create_dataloaders
from src.models.classifier import build_classifier
from src.training.optimizer_factory import build_optimizer
from src.training.trainer import Trainer
from src.utils.logger import setup_logger


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    return dict(data)


def _load_configs(project_root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    training_config = _load_yaml(project_root / "configs" / "training.yaml")
    model_config = _load_yaml(project_root / "configs" / "model.yaml")
    return training_config, model_config


def _seed_everything(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed(42)


def _maybe_init_distributed(project_root: Path, config: dict[str, Any], logger: logging.Logger) -> bool:
    dist_config = dict(config["distributed"])
    if not bool(dist_config["enabled"]):
        return False

    init_file = project_root / str(dist_config["init_file"])
    init_file.parent.mkdir(parents=True, exist_ok=True)
    init_file.touch(exist_ok=True)
    torch.distributed.init_process_group(
        backend="nccl",
        init_method=f"file://{init_file}",
        rank=0,
        world_size=1,
    )
    logger.info("distributed backend initialized: %s", dist_config["backend"])
    return True


def main() -> int:
    training_config, model_config = _load_configs(PROJECT_ROOT)
    runtime_config = dict(training_config["runtime"])
    trainer_config = dict(training_config["trainer"])
    optimizer_config = dict(training_config["optimizer"])

    output_dir = PROJECT_ROOT / str(dict(training_config["experiment"])["output_dir"])
    logger = setup_logger(output_dir=output_dir, name="complex_smoke")

    torch.backends.cudnn.benchmark = True
    device = torch.device("cuda")
    _seed_everything(int(runtime_config["seed"]))

    logger.info("building model on device=%s", device)
    model = build_classifier(model_config)
    model = model.cuda()

    device_count = torch.cuda.device_count()
    logger.info("visible_cuda_devices=%s", device_count)

    distributed_initialized = _maybe_init_distributed(PROJECT_ROOT, training_config, logger)
    dataloaders = create_dataloaders(training_config, PROJECT_ROOT)

    criterion = torch.nn.CrossEntropyLoss()
    optimizer = build_optimizer(model, optimizer_config)
    trainer = Trainer(
        model=model,
        optimizer=optimizer,
        criterion=criterion,
        device=device,
        logger=logger,
        use_amp=bool(runtime_config["use_amp"]),
        grad_clip_norm=float(optimizer_config["grad_clip_norm"]),
        log_interval=int(trainer_config["log_interval"]),
    )

    metrics = trainer.fit(
        train_loader=dataloaders["train"],
        val_loader=dataloaders["val"],
        epochs=int(trainer_config["epochs"]),
    )
    logger.info("training_complete metrics=%s", metrics)

    if distributed_initialized and torch.distributed.is_initialized():
        torch.distributed.destroy_process_group()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
