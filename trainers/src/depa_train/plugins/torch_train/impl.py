from __future__ import annotations

import torch

from ...artifacts import Predictions
from ...context import StepContext
from ...errors import StepError
from ...torchkit.data import load_arrays
from ...torchkit.losses import build_loss
from ...torchkit.model_factory import build_arch_model, build_factory_model, count_parameters
from ...torchkit.train import predictions, report_losses, save_model, train_lightning, train_native
from . import FactoryModel, TorchTrainConfig


def _load_init(ctx: StepContext, cfg: TorchTrainConfig, model: torch.nn.Module) -> None:
    from safetensors.torch import load_file

    assert cfg.init is not None
    paths = ctx.files(cfg.init.input, cfg.init.file, suffixes=frozenset({".safetensors"}))
    if len(paths) != 1:
        raise StepError("init.file must match exactly one safetensors file")
    try:
        model.load_state_dict(load_file(str(paths[0])), strict=cfg.init.strict)
    except RuntimeError:
        raise StepError("initial weights do not match the model architecture") from None


def run(cfg: TorchTrainConfig, ctx: StepContext) -> Predictions | None:
    torch.manual_seed(ctx.seed)
    if isinstance(cfg.model, FactoryModel):
        config = cfg.model.config

        def build() -> torch.nn.Module:
            return build_factory_model(config)

        public_config = {"kind": "factory", "config": config}
    else:
        name, params = cfg.model.name, cfg.model.params

        def build() -> torch.nn.Module:
            return build_arch_model(name, params)

        public_config = {"kind": "arch", "name": name, "params": params}
    n_params = count_parameters(build)
    if n_params > cfg.max_parameters:
        raise StepError(f"model has {n_params} parameters, above max_parameters={cfg.max_parameters}")
    model = build()
    if cfg.init is not None:
        _load_init(ctx, cfg, model)
    ctx.log("model_built", parameters=n_params)

    arrays = load_arrays(ctx, cfg.data, cfg.task)
    loss_fn = build_loss(cfg.loss)
    trainer = train_lightning if cfg.backend == "lightning" else train_native
    model = trainer(ctx, model, loss_fn, arrays, cfg.training)
    report_losses(ctx, model, loss_fn, arrays, cfg.training.batch_size)
    save_model(
        ctx,
        model=model,
        fmt=cfg.save.format,
        out_name=cfg.save.output,
        example=arrays["train"][0],
        config=public_config,
    )
    return predictions(model, arrays, cfg.task, cfg.output_activation, cfg.training.batch_size)
