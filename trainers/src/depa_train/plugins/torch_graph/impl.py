from __future__ import annotations

import json
from pathlib import Path

import torch

from ...artifacts import Predictions
from ...context import StepContext
from ...errors import PrivacyError, SafetyError
from ...safety.files import reject_unsafe_files
from ...tier2.build import GraphModel, build_graph_model, trained_weights
from ...torchkit.data import load_arrays
from ...torchkit.dpsgd import train_functional_dpsgd
from ...torchkit.losses import build_loss
from ...torchkit.train import predictions, report_losses, train_native
from . import GraphRef, GraphTrainConfig


def _graph_dir(ctx: StepContext, ref: GraphRef) -> Path:
    root = ctx.input_root(ref.input)
    directory = (root / ref.path).resolve()
    if not directory.is_relative_to(root.resolve()) or not directory.is_dir():
        raise SafetyError("graph path must be a directory inside its input dataset")
    reject_unsafe_files(directory)
    return directory


def run(cfg: GraphTrainConfig, ctx: StepContext) -> Predictions | None:
    torch.manual_seed(ctx.seed)
    model, doc = build_graph_model(_graph_dir(ctx, cfg.graph), cfg.max_parameters)
    if cfg.loss_graph is not None:
        loss_model, _ = build_graph_model(_graph_dir(ctx, cfg.loss_graph), cfg.max_parameters)
        if any(True for _ in loss_model.parameters()):
            raise SafetyError("a loss graph must not have parameters")

        def loss_fn(outputs: torch.Tensor, targets: torch.Tensor) -> torch.Tensor:
            return loss_model(outputs, targets)
    else:
        assert cfg.loss is not None
        loss_fn = build_loss(cfg.loss)
    ctx.log("graph_loaded", nodes=len(doc["nodes"]), parameters=sum(p.numel() for p in model.parameters()))

    arrays = load_arrays(ctx, cfg.data, cfg.task)
    if ctx.dp:
        if isinstance(model, GraphModel) and model.has_buffer_mutations:
            raise PrivacyError("graphs that update buffers (e.g. BatchNorm statistics) are not DP-compatible")
        X, y = (torch.from_numpy(a) for a in arrays["train"])
        ctx.metric(
            "train_loss",
            train_functional_dpsgd(ctx, model=model, loss_fn=loss_fn, X=X, y=y, spec=cfg.training),
        )
    else:
        model = train_native(ctx, model, loss_fn, arrays, cfg.training)
    report_losses(ctx, model, loss_fn, arrays, cfg.training.batch_size)

    from safetensors.torch import save_file

    out_dir = ctx.output_dir(cfg.save.output)
    save_file(trained_weights(model, doc), str(out_dir / "weights.safetensors"))
    (out_dir / "graph.json").write_text(json.dumps(doc))
    return predictions(model, arrays, cfg.task, cfg.output_activation, cfg.training.batch_size)
