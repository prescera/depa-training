"""Functional DP-SGD (per-sample gradients via ``torch.func``) for models Opacus cannot hook.

Tier-2 graphs call ATen ops directly, so there are no ``nn.Linear``/``nn.Conv2d`` modules for
Opacus' hooks. This implementation computes per-sample gradients with ``vmap(grad(...))``, clips
each to ``max_grad_norm``, adds Gaussian noise ``N(0, (sigma * C)^2)`` from a secret generator and
uses Poisson sampling; ``sigma`` is calibrated and the spend accounted with Opacus' RDP accountant.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import torch
from torch import nn

from .config import TrainingSpec
from .train import OPACUS_ACCOUNTANT, build_optimizer, build_scheduler, check_delta, secret_generator

if TYPE_CHECKING:
    from ..context import StepContext


def train_functional_dpsgd(
    ctx: StepContext,
    *,
    model: nn.Module,
    loss_fn: Callable[[torch.Tensor, torch.Tensor], torch.Tensor],
    X: torch.Tensor,
    y: torch.Tensor,
    spec: TrainingSpec,
) -> float:
    from opacus.accountants import RDPAccountant
    from opacus.accountants.utils import get_noise_multiplier
    from opacus.utils.uniform_sampler import UniformWithReplacementSampler
    from torch.func import functional_call, grad, vmap

    share = ctx.share
    assert share is not None
    assert spec.max_grad_norm is not None
    n = len(X)
    check_delta(share.delta, n)
    sample_rate = min(1.0, spec.batch_size / n)
    steps_per_epoch = max(1, round(1 / sample_rate))
    sigma = get_noise_multiplier(
        target_epsilon=share.epsilon,
        target_delta=share.delta,
        sample_rate=sample_rate,
        steps=spec.epochs * steps_per_epoch,
        accountant="rdp",
    )
    accountant = RDPAccountant()
    names = [k for k, p in model.named_parameters() if p.requires_grad]
    params = [p for _, p in model.named_parameters() if p.requires_grad]
    frozen = {k: p.detach() for k, p in model.named_parameters() if not p.requires_grad}
    buffers = dict(model.named_buffers())
    optimizer = build_optimizer(spec.optimizer, params)
    scheduler = build_scheduler(spec.scheduler, optimizer)
    sampler = UniformWithReplacementSampler(
        num_samples=n, sample_rate=sample_rate, generator=secret_generator(ctx)
    )
    noise_gen = secret_generator(ctx)
    clip = spec.max_grad_norm

    def sample_loss(trainable: dict[str, torch.Tensor], xb: torch.Tensor, yb: torch.Tensor) -> torch.Tensor:
        state = {**trainable, **frozen, **buffers}
        out = functional_call(model, state, (xb.unsqueeze(0),))
        return loss_fn(out, yb.unsqueeze(0))

    per_sample_grads = vmap(grad(sample_loss), in_dims=(None, 0, 0), randomness="different")
    ctx.log("dp_sgd_functional", noise_multiplier=sigma, sample_rate=sample_rate)
    last_loss = float("nan")
    model.train()
    for epoch in range(spec.epochs):
        for _ in range(steps_per_epoch):
            idx = next(iter(sampler))
            current = {k: p.detach() for k, p in zip(names, params, strict=True)}
            if len(idx):
                xb, yb = X[idx], y[idx]
                grads = per_sample_grads(current, xb, yb)
                flat = torch.cat([grads[k].reshape(len(idx), -1) for k in names], dim=1)
                factor = (clip / (flat.norm(dim=1) + 1e-6)).clamp(max=1.0)
                summed = {k: torch.einsum("i,i...->...", factor, grads[k]) for k in names}
                with torch.no_grad():
                    last_loss = float(loss_fn(model(xb), yb))
            else:
                summed = {k: torch.zeros_like(p) for k, p in zip(names, params, strict=True)}
            for k, p in zip(names, params, strict=True):
                noise = torch.normal(0.0, sigma * clip, size=p.shape, generator=noise_gen)
                p.grad = (summed[k] + noise) / (sample_rate * n)
            optimizer.step()
            optimizer.zero_grad()
            accountant.step(noise_multiplier=sigma, sample_rate=sample_rate)
            if scheduler is not None and spec.scheduler and spec.scheduler.interval == "step":
                scheduler.step()
        if scheduler is not None and spec.scheduler and spec.scheduler.interval == "epoch":
            scheduler.step()
        ctx.log(
            "epoch_finished",
            epoch=epoch + 1,
            batch_loss=last_loss,
            epsilon=accountant.get_epsilon(share.delta),
        )
    epsilon = accountant.get_epsilon(share.delta)
    ctx.spend(epsilon, share.delta, OPACUS_ACCOUNTANT)
    return last_loss
