from __future__ import annotations

import numpy as np

from ...artifacts import Predictions, TabularSplits
from ...context import StepContext
from ...errors import PrivacyError, StepError
from ...privacy.accounting import composed_epsilon, per_round_epsilon
from . import XGBConfig

ACCOUNTANT = "dp-xgboost:basic-composition+poisson-subsampling"


def _params(cfg: XGBConfig, data: TabularSplits, seed: int) -> dict[str, object]:
    p = cfg.params
    params: dict[str, object] = {
        "objective": cfg.objective,
        "max_depth": p.max_depth,
        "learning_rate": p.learning_rate,
        "lambda": p.reg_lambda,
        "min_child_weight": p.min_child_weight,
        "subsample": p.subsample,
        "colsample_bytree": p.colsample_bytree,
        "gamma": p.gamma,
        "max_bin": p.max_bin,
        "seed": seed,
        "nthread": 0,
    }
    if p.scale_pos_weight is not None:
        params["scale_pos_weight"] = p.scale_pos_weight
    if p.base_score is not None:
        params["base_score"] = p.base_score
    if cfg.objective == "multi:softprob":
        if not data.n_classes:
            raise StepError("multi:softprob requires a classification split")
        params["num_class"] = data.n_classes
    return params


def _train_dp(cfg: XGBConfig, ctx: StepContext, data: TabularSplits):
    import dp_xgboost as dpx

    share = ctx.share
    assert share is not None
    if data.task != "classification" or data.n_classes != 2:
        raise PrivacyError("DP XGBoost requires a binary classification split")
    if any(b is None for b in data.bounds):
        raise PrivacyError("DP XGBoost requires declared bounds for every feature (tabular/split bounds)")
    lo = [float(b[0]) for b in data.bounds]  # type: ignore[index]
    hi = [float(b[1]) for b in data.bounds]  # type: ignore[index]
    q = cfg.params.subsample
    eps_tree = per_round_epsilon(share.epsilon, cfg.num_boost_round, q)
    # The noise/subsampling seed must be secret: a public seed would let anyone replay the noise.
    params = _params(cfg, data, ctx.secure_seed() & 0x7FFFFFFF)
    params.update({"tree_method": "approxDP", "dp_epsilon_per_tree": eps_tree})
    params.setdefault("base_score", 0.5)
    params.pop("max_bin")  # approxDP derives its bins from the declared feature bounds
    params.pop("nthread")
    train = data.splits["train"]
    # dp-xgboost's Python layer predates NumPy 2 copy semantics: pass contiguous float32 arrays.
    X = np.ascontiguousarray(train.X, dtype=np.float32)
    y = np.ascontiguousarray(train.y, dtype=np.float32)
    dtrain = dpx.DMatrix(X, label=y, feature_min=lo, feature_max=hi)
    booster = dpx.train(params, dtrain, num_boost_round=cfg.num_boost_round)
    ctx.spend(composed_epsilon(eps_tree, cfg.num_boost_round, q), 0.0, ACCOUNTANT)
    ctx.log("dp_trees_trained", trees=cfg.num_boost_round, epsilon_per_tree=eps_tree)

    def predict(X_eval: np.ndarray) -> np.ndarray:
        dm = dpx.DMatrix(np.ascontiguousarray(X_eval, dtype=np.float32), feature_min=lo, feature_max=hi)
        return booster.predict(dm)

    return booster, predict


def _train_plain(cfg: XGBConfig, ctx: StepContext, data: TabularSplits):
    import xgboost as xgb

    params = _params(cfg, data, ctx.seed)
    params["tree_method"] = "hist"
    train = data.splits["train"]
    dtrain = xgb.DMatrix(train.X, label=train.y, feature_names=None)
    evals, kwargs = [], {}
    if cfg.early_stopping_rounds is not None:
        if "val" not in data.splits:
            raise StepError("early_stopping_rounds requires a validation split")
        val = data.splits["val"]
        evals = [(xgb.DMatrix(val.X, label=val.y), "val")]
        kwargs = {"early_stopping_rounds": cfg.early_stopping_rounds, "verbose_eval": False}
    booster = xgb.train(params, dtrain, num_boost_round=cfg.num_boost_round, evals=evals, **kwargs)
    return booster, lambda X_eval: booster.predict(xgb.DMatrix(X_eval))


def run(cfg: XGBConfig, ctx: StepContext) -> Predictions | None:
    data = ctx.artifact(cfg.data)
    if not isinstance(data, TabularSplits):
        raise StepError(f"step '{cfg.data.step}' did not produce tabular splits")
    booster, predict = _train_dp(cfg, ctx, data) if ctx.dp else _train_plain(cfg, ctx, data)
    suffix = ".json" if cfg.save.format == "xgboost-json" else ".ubj"
    booster.save_model(str(ctx.output_dir(cfg.save.output) / f"model{suffix}"))

    held = data.held_out
    if held is None:
        return None
    split = data.splits[held]
    scores = np.asarray(predict(split.X), dtype=np.float32)
    if cfg.objective == "binary:logistic":
        return Predictions("binary", held, split.y, scores.reshape(-1))
    if cfg.objective == "multi:softprob":
        return Predictions("multiclass", held, split.y, scores.reshape(len(split.y), -1))
    return Predictions("regression", held, split.y, scores.reshape(-1))
