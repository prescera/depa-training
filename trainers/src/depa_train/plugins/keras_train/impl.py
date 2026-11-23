from __future__ import annotations

import importlib.util
import os

import numpy as np

from ...artifacts import Predictions
from ...context import StepContext
from ...errors import SafetyError, StepError
from ...safety.keras_config import check_keras_config
from ...torchkit.data import load_arrays
from . import KerasTrainConfig


def run(cfg: KerasTrainConfig, ctx: StepContext) -> Predictions | None:
    if cfg.backend == "jax" and importlib.util.find_spec("jax") is None:
        raise StepError("Keras backend 'jax' is not installed in this image")
    os.environ["KERAS_BACKEND"] = cfg.backend
    import keras

    if keras.backend.backend() != cfg.backend:
        raise StepError("Keras was already initialised with a different backend")
    errors = check_keras_config(cfg.model)
    if errors:
        raise SafetyError(f"Keras config rejected: {errors[0]}")
    keras.utils.set_random_seed(ctx.seed)
    model = keras.saving.deserialize_keras_object(cfg.model, safe_mode=True)
    if not isinstance(model, keras.Model):
        raise StepError("config does not describe a Keras model")
    optimizer = keras.optimizers.get({"class_name": cfg.optimizer.name, "config": dict(cfg.optimizer.params)})
    model.compile(optimizer=optimizer, loss=cfg.loss)

    arrays = load_arrays(ctx, cfg.data, cfg.task)
    X, y = arrays["train"]
    history = model.fit(X, y, epochs=cfg.epochs, batch_size=cfg.batch_size, verbose=0, shuffle=True)
    ctx.metric("train_loss", float(history.history["loss"][-1]))
    for split in ("val", "test"):
        if split in arrays:
            ctx.metric(
                f"{split}_loss", float(model.evaluate(*arrays[split], batch_size=cfg.batch_size, verbose=0))
            )
    ctx.log("keras_trained", epochs=cfg.epochs, parameters=model.count_params())

    model.save(str(ctx.output_dir(cfg.save.output) / "model.keras"))

    split = "test" if "test" in arrays else ("val" if "val" in arrays else None)
    if split is None:
        return None
    scores = np.asarray(
        model.predict(arrays[split][0], batch_size=cfg.batch_size, verbose=0), dtype=np.float32
    )
    if cfg.output_activation == "sigmoid":
        scores = 1.0 / (1.0 + np.exp(-scores))
    elif cfg.output_activation == "softmax":
        e = np.exp(scores - scores.max(axis=1, keepdims=True))
        scores = e / e.sum(axis=1, keepdims=True)
    y_true = arrays[split][1]
    if cfg.task in ("binary", "regression"):
        scores, y_true = scores.reshape(len(scores), -1)[:, 0], y_true.reshape(len(y_true), -1)[:, 0]
    return Predictions(cfg.task, split, y_true, scores)
