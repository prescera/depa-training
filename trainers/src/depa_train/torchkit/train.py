"""Harness-owned PyTorch training: optimizers, schedulers, Opacus DP-SGD, predictions, saving."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import numpy as np
import torch
from torch import nn
from torch.utils.data import DataLoader, TensorDataset

from ..artifacts import Predictions
from ..errors import PrivacyError, SafetyError, StepError
from . import allowlists as al
from .config import OptimizerSpec, SchedulerSpec, TrainingSpec
from .data import Arrays

if TYPE_CHECKING:
    from ..context import StepContext

LossCallable = Callable[[torch.Tensor, torch.Tensor], torch.Tensor]
OPACUS_ACCOUNTANT = "opacus-rdp"


def build_optimizer(spec: OptimizerSpec, params) -> torch.optim.Optimizer:
    if spec.name not in al.OPTIMIZERS:
        raise SafetyError(f"optimizer '{spec.name}' is not allowlisted")
    try:
        return getattr(torch.optim, spec.name)(params, **spec.params)
    except (TypeError, ValueError) as exc:
        raise StepError(f"invalid optimizer parameters: {exc}") from None


def build_scheduler(spec: SchedulerSpec | None, optimizer: torch.optim.Optimizer):
    if spec is None:
        return None
    if spec.name not in al.SCHEDULERS:
        raise SafetyError(f"scheduler '{spec.name}' is not allowlisted")
    try:
        return getattr(torch.optim.lr_scheduler, spec.name)(optimizer, **spec.params)
    except (TypeError, ValueError) as exc:
        raise StepError(f"invalid scheduler parameters: {exc}") from None


def tensor_dataset(arrays: Arrays, split: str) -> TensorDataset:
    X, y = arrays[split]
    return TensorDataset(torch.from_numpy(X), torch.from_numpy(y))


def mean_loss(model: nn.Module, loss_fn: LossCallable, ds: TensorDataset, batch_size: int) -> float:
    model.eval()
    total, count = 0.0, 0
    with torch.no_grad():
        for xb, yb in DataLoader(ds, batch_size=batch_size):
            total += float(loss_fn(model(xb), yb)) * len(xb)
            count += len(xb)
    return total / max(count, 1)


def secret_generator(ctx: StepContext) -> torch.Generator:
    return torch.Generator().manual_seed(ctx.secure_seed())


def check_delta(delta: float, n: int) -> None:
    if delta >= 1.0 / n:
        raise PrivacyError(f"delta={delta:g} must be below 1/n_train={1.0 / n:.3g}")


def train_native(
    ctx: StepContext, model: nn.Module, loss_fn: LossCallable, arrays: Arrays, spec: TrainingSpec
) -> nn.Module:
    train_ds = tensor_dataset(arrays, "train")
    optimizer = build_optimizer(spec.optimizer, [p for p in model.parameters() if p.requires_grad])
    privacy_engine = None
    if ctx.dp:
        from opacus import PrivacyEngine
        from opacus.validators import ModuleValidator

        share = ctx.share
        assert share is not None
        assert spec.max_grad_norm is not None
        check_delta(share.delta, len(train_ds))
        errors = ModuleValidator.validate(model, strict=False)
        if errors:
            raise PrivacyError(
                f"model is not DP-SGD compatible: {type(errors[0]).__name__} (e.g. use GroupNorm)"
            )
        # Sampling and noise randomness must be secret, never derived from the public recipe seed.
        loader = DataLoader(
            train_ds, batch_size=spec.batch_size, shuffle=True, generator=secret_generator(ctx)
        )
        privacy_engine = PrivacyEngine(accountant="rdp")
        model, optimizer, loader = privacy_engine.make_private_with_epsilon(
            module=model,
            optimizer=optimizer,
            data_loader=loader,
            target_epsilon=share.epsilon,
            target_delta=share.delta,
            epochs=spec.epochs,
            max_grad_norm=spec.max_grad_norm,
            poisson_sampling=True,
            noise_generator=secret_generator(ctx),
        )
        ctx.log("dp_sgd", noise_multiplier=optimizer.noise_multiplier, sample_rate=1 / len(loader))
    else:
        g = torch.Generator().manual_seed(ctx.seed)
        loader = DataLoader(train_ds, batch_size=spec.batch_size, shuffle=True, generator=g)
    scheduler = build_scheduler(spec.scheduler, optimizer)

    train_loss = float("nan")
    for epoch in range(spec.epochs):
        model.train()
        total, count = 0.0, 0
        for xb, yb in loader:
            # Poisson sampling can draw an empty batch; Opacus still takes a (noise-only) step.
            optimizer.zero_grad()
            loss = loss_fn(model(xb), yb)
            loss.backward()
            optimizer.step()
            if scheduler is not None and spec.scheduler and spec.scheduler.interval == "step":
                scheduler.step()
            total += float(loss.detach()) * len(xb)
            count += len(xb)
        if scheduler is not None and spec.scheduler and spec.scheduler.interval == "epoch":
            scheduler.step()
        train_loss = total / max(count, 1)
        fields: dict[str, Any] = {"epoch": epoch + 1, "train_loss": train_loss}
        if privacy_engine is not None:
            fields["epsilon"] = privacy_engine.get_epsilon(ctx.share.delta)  # type: ignore[union-attr]
        ctx.log("epoch_finished", **fields)
    ctx.metric("train_loss", train_loss)
    if privacy_engine is not None:
        share = ctx.share
        assert share is not None
        ctx.spend(privacy_engine.get_epsilon(share.delta), share.delta, OPACUS_ACCOUNTANT)
        model = model.to_standard_module()  # removes Opacus hooks and unwraps GradSampleModule
    return model


def train_lightning(
    ctx: StepContext, model: nn.Module, loss_fn: LossCallable, arrays: Arrays, spec: TrainingSpec
) -> nn.Module:
    import lightning as L

    class Module(L.LightningModule):
        def __init__(self) -> None:
            super().__init__()
            self.net = model
            self.epoch_losses: list[float] = []

        def training_step(self, batch, batch_idx):
            xb, yb = batch
            loss = loss_fn(self.net(xb), yb)
            self.epoch_losses.append(float(loss.detach()))
            return loss

        def configure_optimizers(self):
            optimizer = build_optimizer(spec.optimizer, self.net.parameters())
            scheduler = build_scheduler(spec.scheduler, optimizer)
            if scheduler is None:
                return optimizer
            interval = spec.scheduler.interval if spec.scheduler else "epoch"
            return {"optimizer": optimizer, "lr_scheduler": {"scheduler": scheduler, "interval": interval}}

    module = Module()
    g = torch.Generator().manual_seed(ctx.seed)
    loader = DataLoader(
        tensor_dataset(arrays, "train"), batch_size=spec.batch_size, shuffle=True, generator=g
    )
    trainer = L.Trainer(
        max_epochs=spec.epochs,
        accelerator="cpu",
        devices=1,
        logger=False,
        enable_checkpointing=False,
        enable_progress_bar=False,
        enable_model_summary=False,
        default_root_dir=str(ctx.scratch),
        deterministic=True,
    )
    trainer.fit(module, train_dataloaders=loader)
    if module.epoch_losses:
        ctx.metric("train_loss", float(np.mean(module.epoch_losses[-len(loader) :])))
    return module.net


def report_losses(
    ctx: StepContext, model: nn.Module, loss_fn: LossCallable, arrays: Arrays, batch_size: int
) -> None:
    for split in ("val", "test"):
        if split in arrays:
            ctx.metric(f"{split}_loss", mean_loss(model, loss_fn, tensor_dataset(arrays, split), batch_size))


def predictions(
    model: nn.Module, arrays: Arrays, task: str, activation: str, batch_size: int
) -> Predictions | None:
    split = "test" if "test" in arrays else ("val" if "val" in arrays else None)
    if split is None:
        return None
    model.eval()
    outs = []
    with torch.no_grad():
        for (xb,) in DataLoader(TensorDataset(torch.from_numpy(arrays[split][0])), batch_size=batch_size):
            out = model(xb)
            if activation == "sigmoid":
                out = torch.sigmoid(out)
            elif activation == "softmax":
                out = torch.softmax(out, dim=1)
            outs.append(out.float().numpy())
    scores = np.concatenate(outs)
    y = arrays[split][1]
    if task in ("binary", "regression"):
        scores, y = scores.reshape(len(scores), -1)[:, 0], y.reshape(len(y), -1)[:, 0]
    return Predictions(task, split, y, scores)  # type: ignore[arg-type]


class _SingleInput(nn.Module):
    """Gives every model the same ``forward(input)`` signature for export with a dynamic batch."""

    def __init__(self, model: nn.Module):
        super().__init__()
        self.model = model

    def forward(self, input: torch.Tensor) -> torch.Tensor:
        return self.model(input)


def save_model(
    ctx: StepContext,
    *,
    model: nn.Module,
    fmt: str,
    out_name: str,
    example: np.ndarray,
    config: dict[str, Any] | None,
) -> None:
    out_dir = ctx.output_dir(out_name)
    model.eval()
    if fmt == "safetensors":
        from safetensors.torch import save_model as st_save_model

        st_save_model(model, str(out_dir / "model.safetensors"))
        if config is not None:
            (out_dir / "config.json").write_text(json.dumps(config, indent=2, sort_keys=True))
        return
    sample = torch.from_numpy(np.ascontiguousarray(example[:2]))
    batch = torch.export.Dim("batch", min=1)
    torch.onnx.export(
        _SingleInput(model),
        (sample,),
        str(out_dir / "model.onnx"),
        dynamo=True,
        external_data=False,
        dynamic_shapes=({0: batch},),
        input_names=["input"],
        output_names=["output"],
    )
