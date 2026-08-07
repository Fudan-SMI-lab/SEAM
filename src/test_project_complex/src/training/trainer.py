from __future__ import annotations

# pyright: reportAny=false
# pyright: reportDeprecated=false
# pyright: reportUnknownMemberType=false
# pyright: reportUnusedCallResult=false

import logging

import torch
from torch import nn
from torch.utils.data import DataLoader
from tqdm import tqdm

from .optimizer_factory import clip_gradients
from ..utils.metrics import AverageMeter, accuracy, gradient_signal


class Trainer:
    """Training and validation loop wrapper."""

    def __init__(
        self,
        *,
        model: nn.Module,
        optimizer: torch.optim.Optimizer,
        criterion: nn.Module,
        device: torch.device | None,
        logger: logging.Logger,
        use_amp: bool,
        grad_clip_norm: float,
        log_interval: int,
    ) -> None:
        self.model: nn.Module = model
        self.optimizer: torch.optim.Optimizer = optimizer
        self.criterion: nn.Module = criterion
        self.logger: logging.Logger = logger
        self.use_amp: bool = use_amp
        self.grad_clip_norm: float = grad_clip_norm
        self.log_interval: int = max(1, log_interval)
        self.device: torch.device = device if device is not None else torch.device("cuda")
        self.scaler: torch.cuda.amp.GradScaler = torch.cuda.amp.GradScaler(enabled=use_amp)

    def fit(
        self,
        train_loader: DataLoader[tuple[torch.Tensor, torch.Tensor]],
        val_loader: DataLoader[tuple[torch.Tensor, torch.Tensor]],
        epochs: int,
    ) -> dict[str, float]:
        final_metrics: dict[str, float] = {}
        for epoch in range(1, epochs + 1):
            train_metrics = self._run_epoch(train_loader, training=True, epoch=epoch)
            val_metrics = self._run_epoch(val_loader, training=False, epoch=epoch)
            final_metrics = {**train_metrics, **val_metrics}
            self.logger.info(
                "epoch=%s train_loss=%.4f train_acc=%.4f val_loss=%.4f val_acc=%.4f",
                epoch,
                train_metrics["train_loss"],
                train_metrics["train_acc"],
                val_metrics["val_loss"],
                val_metrics["val_acc"],
            )

        torch.cuda.empty_cache()
        return final_metrics

    def _run_epoch(
        self,
        loader: DataLoader[tuple[torch.Tensor, torch.Tensor]],
        *,
        training: bool,
        epoch: int,
    ) -> dict[str, float]:
        phase = "train" if training else "val"
        loss_meter = AverageMeter()
        acc_meter = AverageMeter()
        grad_meter = AverageMeter()

        if training:
            _ = self.model.train()
        else:
            _ = self.model.eval()

        progress = tqdm(loader, desc=f"{phase}-epoch-{epoch}", leave=False)
        for step, batch in enumerate(progress, start=1):
            images, labels = batch
            images = images.to("cuda")
            labels = labels.to("cuda")

            if training:
                self.optimizer.zero_grad(set_to_none=True)

            with torch.set_grad_enabled(training):
                with torch.cuda.amp.autocast(enabled=self.use_amp):
                    logits = self.model(images)
                    loss = self.criterion(logits, labels)

                if training:
                    self.scaler.scale(loss).backward()
                    grad_norm = clip_gradients(self.model.parameters(), self.grad_clip_norm)
                    self.scaler.step(self.optimizer)
                    self.scaler.update()
                else:
                    grad_norm = 0.0

            batch_size = int(labels.size(0))
            loss_meter.update(loss.item(), batch_size)
            acc_meter.update(accuracy(logits.detach(), labels.detach()), batch_size)
            grad_meter.update(gradient_signal(loss.detach()) + grad_norm, 1)

            progress.set_postfix(loss=f"{loss_meter.avg:.4f}", acc=f"{acc_meter.avg:.4f}")
            if step % self.log_interval == 0:
                self.logger.info(
                    "phase=%s epoch=%s step=%s loss=%.4f acc=%.4f grad_signal=%.4f device=%s",
                    phase,
                    epoch,
                    step,
                    loss_meter.avg,
                    acc_meter.avg,
                    grad_meter.avg,
                    self.device,
                )

        return {
            f"{phase}_loss": loss_meter.avg,
            f"{phase}_acc": acc_meter.avg,
            f"{phase}_grad_signal": grad_meter.avg,
        }
