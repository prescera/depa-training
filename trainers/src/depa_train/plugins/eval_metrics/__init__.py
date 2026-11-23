"""``eval/classification@1``, ``eval/regression@1``, ``eval/segmentation@1``: held-out metrics.

Metrics are computed from the predictions artifact of a training step (its test split, else its
validation split). Only scalar metrics are produced; plots and per-row predictions never leave.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from ...registry import NONE, PREDICTIONS, Plugin, ValidationContext
from ...types import StepRef, Strict


class _EvalBase(Strict):
    predictions: StepRef
    prefix: str = Field(default="", pattern=r"^([a-z][a-z0-9_]{0,31})?$", description="metric name prefix")


class ClassificationConfig(_EvalBase):
    metrics: list[
        Literal[
            "accuracy",
            "balanced_accuracy",
            "precision",
            "recall",
            "f1_score",
            "roc_auc",
            "average_precision",
            "log_loss",
            "mcc",
        ]
    ] = Field(min_length=1)
    average: Literal["binary", "macro", "micro", "weighted"] | None = None
    threshold: float = Field(default=0.5, gt=0, lt=1)


class RegressionConfig(_EvalBase):
    metrics: list[Literal["mse", "rmse", "mae", "r2"]] = Field(min_length=1)


class SegmentationConfig(_EvalBase):
    metrics: list[Literal["dice_score", "jaccard_index", "hausdorff_distance"]] = Field(min_length=1)
    threshold: float = Field(default=0.5, gt=0, lt=1)


class _Eval(Plugin):
    version = "1.0.0"
    produces = NONE
    wraps = "scikit-learn / NumPy / SciPy metrics"
    preload_modules = ("sklearn.metrics", "scipy.spatial.distance")

    def check(self, cfg: _EvalBase, v: ValidationContext) -> None:
        v.require_ref(cfg.predictions, {PREDICTIONS}, "predictions")
        if len(set(cfg.metrics)) != len(cfg.metrics):  # type: ignore[attr-defined]
            v.error("metrics", "duplicate metric")

    def metrics(self, cfg: _EvalBase) -> list[str]:
        return [f"{cfg.prefix}{m}" for m in cfg.metrics]  # type: ignore[attr-defined]


class EvalClassification(_Eval):
    name = "eval/classification"
    Config = ClassificationConfig
    summary = "Classification metrics on held-out predictions"
    impl = "depa_train.plugins.eval_metrics.impl:classification"


class EvalRegression(_Eval):
    name = "eval/regression"
    Config = RegressionConfig
    summary = "Regression metrics on held-out predictions"
    impl = "depa_train.plugins.eval_metrics.impl:regression"


class EvalSegmentation(_Eval):
    name = "eval/segmentation"
    Config = SegmentationConfig
    summary = "Segmentation metrics (Dice, Jaccard, Hausdorff) on held-out predictions"
    impl = "depa_train.plugins.eval_metrics.impl:segmentation"
