from __future__ import annotations

import importlib
import inspect

import numpy as np

from ...artifacts import Predictions, TabularSplits
from ...context import StepContext
from ...errors import SafetyError, StepError
from . import ESTIMATORS, TRANSFORMERS, SklearnComponent, SklearnConfig


def _build(comp: SklearnComponent, allowed: frozenset[str], seed: int):
    if comp.class_ not in allowed:
        raise SafetyError(f"'{comp.class_}' is not allowlisted")
    module_name, _, cls_name = comp.class_.rpartition(".")
    cls = getattr(importlib.import_module(module_name), cls_name)
    params = dict(comp.params)
    accepted = inspect.signature(cls).parameters
    unknown = sorted(set(params) - set(accepted))
    if unknown:
        raise StepError(f"{cls_name} does not accept parameters {unknown}")
    if "random_state" in accepted and "random_state" not in params:
        params["random_state"] = seed
    try:
        return cls(**params)
    except (TypeError, ValueError) as exc:
        raise StepError(f"invalid parameters for {cls_name}: {exc}") from None


def run(cfg: SklearnConfig, ctx: StepContext) -> Predictions | None:
    import skops.io as sio
    from sklearn.pipeline import Pipeline

    data = ctx.artifact(cfg.data)
    if not isinstance(data, TabularSplits):
        raise StepError(f"step '{cfg.data.step}' did not produce tabular splits")
    steps = [(f"pre{i}", _build(c, TRANSFORMERS, ctx.seed)) for i, c in enumerate(cfg.preprocess)]
    steps.append(("model", _build(cfg.estimator, ESTIMATORS, ctx.seed)))
    model = Pipeline(steps)
    train = data.splits["train"]
    model.fit(train.X, train.y)
    ctx.log("model_fitted", rows=len(train.y))

    path = ctx.output_dir(cfg.save.output) / "model.skops"
    sio.dump(model, path)
    untrusted = sio.get_untrusted_types(file=str(path))
    if untrusted:
        raise SafetyError(f"skops would need untrusted types to load the model: {sorted(untrusted)}")

    held = data.held_out
    if held is None:
        return None
    split = data.splits[held]
    if data.task == "regression":
        return Predictions("regression", held, split.y, model.predict(split.X).astype(np.float32))
    if hasattr(model, "predict_proba"):
        proba = model.predict_proba(split.X)
    else:
        scores = model.decision_function(split.X)
        proba = 1.0 / (1.0 + np.exp(-scores)) if scores.ndim == 1 else _softmax(scores)
        if proba.ndim == 1:
            proba = np.column_stack([1 - proba, proba])
    if data.n_classes == 2:
        return Predictions("binary", held, split.y, proba[:, 1].astype(np.float32))
    return Predictions("multiclass", held, split.y, proba.astype(np.float32))


def _softmax(z: np.ndarray) -> np.ndarray:
    e = np.exp(z - z.max(axis=1, keepdims=True))
    return e / e.sum(axis=1, keepdims=True)
