"""``keras/train@1``: Keras 3 model from a JSON config (allowlisted, ``safe_mode``), saved as ``.keras``."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from ...data import ArrayData
from ...registry import PREDICTIONS, Plugin, ValidationContext
from ...safety.keras_config import COMPILE_LOSSES, OPTIMIZERS, check_keras_config
from ...torchkit.config import Task, check_data
from ...types import OutputTarget, Seed, Strict


class KerasOptimizer(Strict):
    name: str
    params: dict[str, float | int | bool] = Field(default_factory=dict)


class KerasTrainConfig(Strict):
    data: ArrayData
    task: Task
    model: dict[str, Any] = Field(description="Keras model config (model.to_json())")
    loss: str
    optimizer: KerasOptimizer
    epochs: int = Field(ge=1, le=10_000)
    batch_size: int = Field(ge=1, le=65_536)
    backend: Literal["torch", "jax"] = "torch"
    output_activation: Literal["none", "sigmoid", "softmax"] = "none"
    save: OutputTarget
    seed: Seed | None = None


class KerasTrain(Plugin):
    name = "keras/train"
    version = "1.0.0"
    families = frozenset({"torch"})
    Config = KerasTrainConfig
    produces = PREDICTIONS
    summary = "Keras 3 model from an allowlisted JSON config (torch or JAX backend), saved as .keras"
    wraps = "Keras 3"
    impl = "depa_train.plugins.keras_train.impl:run"
    trains = True

    def preload(self, cfg: KerasTrainConfig) -> list[str]:
        import importlib.util
        import os

        if cfg.backend == "jax" and importlib.util.find_spec("jax") is None:
            return super().preload(cfg)  # the step fails with a clear message at run time
        os.environ["KERAS_BACKEND"] = cfg.backend  # must be set before keras is first imported
        return [*super().preload(cfg), "keras", "PIL.Image", "h5py"]

    def check(self, cfg: KerasTrainConfig, v: ValidationContext) -> None:
        check_data(cfg.data, cfg.task, v, "data")
        v.require_output(cfg.save, "save", {"keras"})
        for err in check_keras_config(cfg.model):
            v.error("model", err)
        if cfg.loss not in COMPILE_LOSSES:
            v.error("loss", f"'{cfg.loss}' is not an allowlisted Keras loss")
        if cfg.optimizer.name not in OPTIMIZERS:
            v.error("optimizer.name", f"'{cfg.optimizer.name}' is not an allowlisted Keras optimizer")

    def metrics(self, cfg: KerasTrainConfig) -> list[str]:
        return ["train_loss", "val_loss", "test_loss"]
