"""Config models shared by ``torch/train`` and ``torch/graph-train`` (import-light)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from ..data import ArrayData, ImageFolder, ImagePairs, TabularRef, TensorFile
from ..registry import TABULAR_SPLITS, ValidationContext
from ..types import Alias, JsonValue, PrivacyShare, RelGlob, StepRef, Strict
from . import allowlists as al

Task = Literal["binary", "multiclass", "regression", "segmentation"]


class LossSpec(Strict):
    """Atomic loss (``class`` + ``params``) or an arithmetic ``expression`` over named components."""

    class_: str | None = Field(default=None, alias="class")
    params: dict[str, JsonValue] = Field(default_factory=dict)
    expression: str | None = Field(default=None, max_length=512)
    components: dict[str, LossSpec] = Field(default_factory=dict, max_length=16)
    variables: dict[str, float] = Field(default_factory=dict, max_length=16)
    reduction: Literal["mean", "sum", "none"] = "mean"

    @model_validator(mode="after")
    def _one_kind(self) -> LossSpec:
        if (self.class_ is None) == (self.expression is None):
            raise ValueError("a loss needs exactly one of 'class' or 'expression'")
        return self


class OptimizerSpec(Strict):
    name: str
    params: dict[str, float | int | bool | list[float]] = Field(default_factory=dict)


class SchedulerSpec(Strict):
    name: str
    params: dict[str, float | int | bool | str | list[int] | list[float]] = Field(default_factory=dict)
    interval: Literal["step", "epoch"] = "epoch"


class TrainingSpec(Strict):
    epochs: int = Field(ge=1, le=10_000)
    batch_size: int = Field(ge=1, le=65_536)
    optimizer: OptimizerSpec
    scheduler: SchedulerSpec | None = None
    max_grad_norm: float | None = Field(default=None, gt=0, description="per-sample clipping norm (DP)")


class InitWeights(Strict):
    """Initial weights from a safetensors file in an input dataset (e.g. a TDC-provided checkpoint)."""

    input: Alias
    file: RelGlob
    strict: bool = True


def check_loss(spec: LossSpec, v: ValidationContext, field: str, depth: int = 0) -> None:
    if depth > 4:
        v.error(field, "loss components are nested too deeply")
        return
    if spec.class_ is not None and al.normalize_loss(spec.class_) is None:
        v.error(f"{field}.class", f"'{spec.class_}' is not an allowlisted loss")
    for name, comp in spec.components.items():
        check_loss(comp, v, f"{field}.components.{name}", depth + 1)


def check_training(spec: TrainingSpec, v: ValidationContext, field: str) -> None:
    if spec.optimizer.name not in al.OPTIMIZERS:
        v.error(f"{field}.optimizer.name", f"'{spec.optimizer.name}' is not an allowlisted optimizer")
    if spec.scheduler is not None and spec.scheduler.name not in al.SCHEDULERS:
        v.error(f"{field}.scheduler.name", f"'{spec.scheduler.name}' is not an allowlisted scheduler")
    if v.dp and spec.max_grad_norm is None:
        v.error(f"{field}.max_grad_norm", "required when privacy.mode is 'dp'")


def check_data(data: ArrayData, task: Task, v: ValidationContext, field: str) -> None:
    if isinstance(data, TabularRef):
        v.require_ref(StepRef(step=data.step), {TABULAR_SPLITS}, f"{field}.step")
        if task == "segmentation":
            v.error(field, "tabular data cannot be used for segmentation")
    elif isinstance(data, TensorFile):
        v.require_alias(data.input, f"{field}.input")
    elif isinstance(data, ImagePairs | ImageFolder):
        for i, alias in enumerate(data.inputs):
            v.require_alias(alias, f"{field}.inputs[{i}]")
        if isinstance(data, ImagePairs) and task != "segmentation":
            v.error(field, "image_pairs data requires task 'segmentation'")
        if isinstance(data, ImageFolder) and task not in ("binary", "multiclass"):
            v.error(field, "image_folder data requires a classification task")


def check_privacy_share(share: PrivacyShare | None, v: ValidationContext) -> None:
    if share is not None and share.delta <= 0:
        v.error("privacy.delta", "DP-SGD (RDP accounting) needs delta > 0")
