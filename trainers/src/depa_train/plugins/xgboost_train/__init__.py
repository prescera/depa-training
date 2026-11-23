"""``xgboost/train@1``: gradient-boosted trees; DP via Sarus dp-xgboost (``approxDP``).

DP accounting: each tree is ``eps_tree``-DP (dp-xgboost splits it between split selection and leaf
noise, using histogram bins from *declared* feature bounds); trees are built on Poisson subsamples
of rate ``subsample`` and compose sequentially, so the total is
``T * log(1 + q (exp(eps_tree) - 1))``. ``eps_tree`` is solved so the total equals the step budget.
dp-xgboost is pure DP, so delta spent is 0. Its noise and subsampling seed comes from the OS CSPRNG.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from ...registry import PREDICTIONS, TABULAR_SPLITS, Plugin, ValidationContext
from ...types import OutputTarget, PrivacyShare, Seed, StepRef, Strict


class XGBParams(Strict):
    max_depth: int = Field(default=6, ge=1, le=16)
    learning_rate: float = Field(default=0.3, gt=0, le=1)
    reg_lambda: float = Field(default=1.0, ge=0, alias="lambda")
    min_child_weight: float = Field(default=1.0, ge=0)
    subsample: float = Field(default=1.0, gt=0, le=1)
    colsample_bytree: float = Field(default=1.0, gt=0, le=1)
    gamma: float = Field(default=0.0, ge=0)
    max_bin: int = Field(default=256, ge=2, le=1024)
    scale_pos_weight: float | None = Field(default=None, gt=0)
    base_score: float | None = None


class XGBConfig(Strict):
    data: StepRef
    objective: Literal["binary:logistic", "multi:softprob", "reg:squarederror", "reg:absoluteerror"] = (
        "binary:logistic"
    )
    num_boost_round: int = Field(default=100, ge=1, le=10_000)
    params: XGBParams = Field(default_factory=XGBParams)
    early_stopping_rounds: int | None = Field(default=None, ge=1)
    privacy: PrivacyShare | None = None
    save: OutputTarget
    seed: Seed | None = None


class XGBoostTrain(Plugin):
    name = "xgboost/train"
    version = "1.0.0"
    families = frozenset({"classic"})
    Config = XGBConfig
    produces = PREDICTIONS
    summary = "XGBoost boosted trees; differentially private via dp-xgboost"
    wraps = "xgboost (hist) / dp-xgboost (approxDP)"
    impl = "depa_train.plugins.xgboost_train.impl:run"
    preload_modules = ("xgboost", "dp_xgboost")
    trains = True
    dp_capable = True

    def check(self, cfg: XGBConfig, v: ValidationContext) -> None:
        v.require_ref(cfg.data, {TABULAR_SPLITS}, "data")
        v.require_output(cfg.save, "save", {"xgboost-json", "xgboost-ubj"})
        if not v.dp:
            return
        if cfg.objective != "binary:logistic":
            v.error("objective", "dp-xgboost DP guarantees are only established for 'binary:logistic'")
        if cfg.early_stopping_rounds is not None:
            v.error("early_stopping_rounds", "early stopping on private validation data is not DP")
        if cfg.privacy is not None and cfg.privacy.delta != 0:
            v.error("privacy.delta", "dp-xgboost is pure epsilon-DP; set delta to 0")
