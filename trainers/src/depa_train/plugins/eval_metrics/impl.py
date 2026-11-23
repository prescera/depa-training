from __future__ import annotations

import numpy as np

from ...artifacts import Predictions
from ...context import StepContext
from ...errors import StepError
from . import ClassificationConfig, RegressionConfig, SegmentationConfig


def _predictions(ctx: StepContext, cfg, tasks: set[str]) -> Predictions:
    preds = ctx.artifact(cfg.predictions)
    if not isinstance(preds, Predictions):
        raise StepError(f"step '{cfg.predictions.step}' produced no held-out predictions")
    if preds.task not in tasks:
        raise StepError(f"predictions are for task '{preds.task}', expected one of {sorted(tasks)}")
    if len(preds.y_true) == 0:
        raise StepError("held-out split is empty")
    return preds


def classification(cfg: ClassificationConfig, ctx: StepContext) -> None:
    from sklearn import metrics as skm

    preds = _predictions(ctx, cfg, {"binary", "multiclass"})
    y = preds.y_true.astype(np.int64).ravel()
    if preds.task == "binary":
        score = preds.y_score.reshape(-1)
        label = (score >= cfg.threshold).astype(np.int64)
        average = cfg.average or "binary"
        proba = np.column_stack([1 - score, score])
    else:
        proba = preds.y_score.reshape(len(y), -1)
        label = proba.argmax(axis=1)
        average = cfg.average or "macro"
        if average == "binary":
            raise StepError("average 'binary' requires a binary task")
    for name in cfg.metrics:
        if name == "accuracy":
            value = skm.accuracy_score(y, label)
        elif name == "balanced_accuracy":
            value = skm.balanced_accuracy_score(y, label)
        elif name in ("precision", "recall", "f1_score"):
            fn = {"precision": skm.precision_score, "recall": skm.recall_score, "f1_score": skm.f1_score}[
                name
            ]
            value = fn(y, label, average=average, zero_division=0)
        elif name == "roc_auc":
            if len(np.unique(y)) < 2:
                raise StepError("roc_auc is undefined: the held-out split has a single class")
            value = (
                skm.roc_auc_score(y, proba[:, 1])
                if preds.task == "binary"
                else skm.roc_auc_score(y, proba, multi_class="ovr", labels=list(range(proba.shape[1])))
            )
        elif name == "average_precision":
            if preds.task != "binary":
                raise StepError("average_precision is only supported for binary tasks")
            value = skm.average_precision_score(y, proba[:, 1])
        elif name == "log_loss":
            value = skm.log_loss(y, np.clip(proba, 1e-7, 1 - 1e-7), labels=list(range(proba.shape[1])))
        else:  # mcc
            value = skm.matthews_corrcoef(y, label)
        ctx.metric(f"{cfg.prefix}{name}", float(value))


def regression(cfg: RegressionConfig, ctx: StepContext) -> None:
    from sklearn import metrics as skm

    preds = _predictions(ctx, cfg, {"regression"})
    y, p = preds.y_true.reshape(-1).astype(np.float64), preds.y_score.reshape(-1).astype(np.float64)
    for name in cfg.metrics:
        if name == "mse":
            value = skm.mean_squared_error(y, p)
        elif name == "rmse":
            value = float(np.sqrt(skm.mean_squared_error(y, p)))
        elif name == "mae":
            value = skm.mean_absolute_error(y, p)
        else:
            value = skm.r2_score(y, p)
        ctx.metric(f"{cfg.prefix}{name}", float(value))


def _hausdorff(pred: np.ndarray, true: np.ndarray) -> float | None:
    from scipy.spatial.distance import directed_hausdorff

    a, b = np.argwhere(pred), np.argwhere(true)
    if len(a) == 0 or len(b) == 0:
        return None
    return max(directed_hausdorff(a, b)[0], directed_hausdorff(b, a)[0])


def segmentation(cfg: SegmentationConfig, ctx: StepContext) -> None:
    preds = _predictions(ctx, cfg, {"segmentation"})
    pred = preds.y_score > cfg.threshold
    true = preds.y_true > 0.5
    if pred.shape != true.shape:
        raise StepError("prediction and mask shapes differ")
    axes = tuple(range(1, pred.ndim))
    inter = (pred & true).sum(axis=axes).astype(np.float64)
    p_sum, t_sum = pred.sum(axis=axes).astype(np.float64), true.sum(axis=axes).astype(np.float64)
    eps = 1e-6
    for name in cfg.metrics:
        if name == "dice_score":
            value = float(np.mean((2 * inter + eps) / (p_sum + t_sum + eps)))
        elif name == "jaccard_index":
            value = float(np.mean((inter + eps) / (p_sum + t_sum - inter + eps)))
        else:
            dists = [_hausdorff(p.squeeze(), t.squeeze()) for p, t in zip(pred, true, strict=True)]
            finite = [d for d in dists if d is not None]
            if not finite:
                raise StepError("hausdorff is undefined: no sample has both a predicted and a true mask")
            value = float(np.mean(finite))
        ctx.metric(f"{cfg.prefix}{name}", value)
