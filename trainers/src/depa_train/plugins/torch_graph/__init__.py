"""``torch/graph-train@1`` (tier 2, experimental): train a TDC-supplied ``depa-graph/v1`` model.

The consumer supplies only the model graph (and optionally a loss graph); the harness owns data
loading, optimizer, DP-SGD, checkpointing and output. Every op is checked against the allowlist.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from ...data import ArrayData
from ...registry import PREDICTIONS, Plugin, ValidationContext
from ...torchkit.config import (
    LossSpec,
    Task,
    TrainingSpec,
    check_data,
    check_loss,
    check_privacy_share,
    check_training,
)
from ...types import Alias, OutputTarget, PrivacyShare, RelGlob, Seed, Strict


class GraphRef(Strict):
    """Directory (inside an input dataset) holding ``graph.json`` and ``weights.safetensors``."""

    input: Alias
    path: RelGlob = "."


class GraphTrainConfig(Strict):
    data: ArrayData
    task: Task
    graph: GraphRef
    loss: LossSpec | None = None
    loss_graph: GraphRef | None = Field(
        default=None, description="graph mapping (prediction, target) to a scalar"
    )
    training: TrainingSpec
    output_activation: Literal["none", "sigmoid", "softmax"] = "none"
    max_parameters: int = Field(default=200_000_000, ge=1, le=2_000_000_000)
    privacy: PrivacyShare | None = None
    save: OutputTarget
    seed: Seed | None = None

    @model_validator(mode="after")
    def _one_loss(self) -> GraphTrainConfig:
        if (self.loss is None) == (self.loss_graph is None):
            raise ValueError("set exactly one of 'loss' or 'loss_graph'")
        return self


class TorchGraphTrain(Plugin):
    name = "torch/graph-train"
    version = "1.0.0"
    tier = 2
    families = frozenset({"torch"})
    Config = GraphTrainConfig
    produces = PREDICTIONS
    summary = "Tier 2: train an allowlisted ATen graph (depa-graph/v1); harness-owned loop and DP-SGD"
    wraps = "torch.fx + torch.func (functional DP-SGD, Opacus RDP accountant)"
    impl = "depa_train.plugins.torch_graph.impl:run"
    preload_modules = ("safetensors.torch", "torch.func", "opacus", "PIL.Image", "h5py", "nibabel")
    trains = True
    dp_capable = True
    experimental = True

    def check(self, cfg: GraphTrainConfig, v: ValidationContext) -> None:
        check_data(cfg.data, cfg.task, v, "data")
        check_training(cfg.training, v, "training")
        check_privacy_share(cfg.privacy, v)
        v.require_alias(cfg.graph.input, "graph.input")
        if cfg.loss is not None:
            check_loss(cfg.loss, v, "loss")
        if cfg.loss_graph is not None:
            v.require_alias(cfg.loss_graph.input, "loss_graph.input")
        v.require_output(cfg.save, "save", {"safetensors"})

    def metrics(self, cfg: GraphTrainConfig) -> list[str]:
        return ["train_loss", "val_loss", "test_loss"]
