"""``torch/train@1``: PyTorch training of config-built or allowlisted models, with Opacus DP-SGD."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field

from ...data import ArrayData
from ...registry import PREDICTIONS, Plugin, ValidationContext
from ...torchkit import allowlists as al
from ...torchkit.config import (
    InitWeights,
    LossSpec,
    Task,
    TrainingSpec,
    check_data,
    check_loss,
    check_privacy_share,
    check_training,
)
from ...types import JsonValue, OutputTarget, PrivacyShare, Seed, Strict


class FactoryModel(Strict):
    """Model built by the ModelFactory from layers, submodules and a forward graph (v1 format)."""

    kind: Literal["factory"]
    config: dict[str, Any]


class ArchModel(Strict):
    """An allowlisted architecture constructor, e.g. ``torchvision.resnet18`` or ``monai.UNet``."""

    kind: Literal["arch"]
    name: str
    params: dict[str, JsonValue] = Field(default_factory=dict)


ModelSpec = Annotated[FactoryModel | ArchModel, Field(discriminator="kind")]


class TorchTrainConfig(Strict):
    data: ArrayData
    task: Task
    model: ModelSpec
    init: InitWeights | None = None
    loss: LossSpec
    training: TrainingSpec
    output_activation: Literal["none", "sigmoid", "softmax"] = Field(
        default="none", description="applied to model outputs for held-out predictions only"
    )
    backend: Literal["native", "lightning"] = "native"
    max_parameters: int = Field(default=200_000_000, ge=1, le=2_000_000_000)
    privacy: PrivacyShare | None = None
    save: OutputTarget
    seed: Seed | None = None


class TorchTrain(Plugin):
    name = "torch/train"
    version = "1.0.0"
    families = frozenset({"torch"})
    Config = TorchTrainConfig
    produces = PREDICTIONS
    summary = "PyTorch training (ModelFactory/torchvision/timm/MONAI models), Opacus DP-SGD, Lightning option"
    wraps = "PyTorch + Opacus (+ PyTorch Lightning)"
    impl = "depa_train.plugins.torch_train.impl:run"
    trains = True
    dp_capable = True

    def preload(self, cfg: TorchTrainConfig) -> list[str]:
        modules = [*super().preload(cfg), "opacus", "safetensors.torch", "PIL.Image", "h5py", "nibabel"]
        if isinstance(cfg.model, ArchModel) and cfg.model.name in al.ARCHITECTURES:
            library = cfg.model.name.split(".", 1)[0]
            modules.append(
                {"torchvision": "torchvision.models", "timm": "timm", "monai": "monai.networks.nets"}[library]
            )
        if cfg.backend == "lightning":
            modules.append("lightning")
        if cfg.save.format == "onnx":
            modules += ["onnx", "onnxscript", "torch.onnx"]
        return modules

    def check(self, cfg: TorchTrainConfig, v: ValidationContext) -> None:
        check_data(cfg.data, cfg.task, v, "data")
        check_loss(cfg.loss, v, "loss")
        check_training(cfg.training, v, "training")
        check_privacy_share(cfg.privacy, v)
        v.require_output(cfg.save, "save", {"safetensors", "onnx"})
        if cfg.init is not None:
            v.require_alias(cfg.init.input, "init.input")
        if isinstance(cfg.model, FactoryModel):
            for err in al.check_factory_config(cfg.model.config):
                v.error("model.config", err)
        else:
            if cfg.model.name not in al.ARCHITECTURES:
                v.error("model.name", f"'{cfg.model.name}' is not an allowlisted architecture")
            denied = sorted(al.DENIED_ARCH_PARAMS & set(cfg.model.params))
            if denied:
                v.error(
                    "model.params", f"parameters {denied} are not allowed (weights come only from 'init')"
                )
        if cfg.backend == "lightning" and v.dp:
            v.error("backend", "the Lightning backend does not support DP-SGD; use 'native'")

    def metrics(self, cfg: TorchTrainConfig) -> list[str]:
        return ["train_loss", "val_loss", "test_loss"]
