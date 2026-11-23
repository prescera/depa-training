"""``tabular/split@1``: feature selection, missing values, encoding, bounds/scaling and splits.

Under ``privacy.mode: dp`` every transformation must be data-independent (declared categories,
declared bounds, constant fills), because preprocessing fitted on private data is itself a release
that DP training does not cover.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from ...data import TableSource
from ...registry import TABLE, TABULAR_SPLITS, Plugin, ValidationContext
from ...types import ColumnName, Seed, Splits, StepRef, Strict

Bound = tuple[float, float]


class Missing(Strict):
    strategy: Literal["drop", "constant", "mean", "median"] = "drop"
    value: float | str | None = None

    @model_validator(mode="after")
    def _value(self) -> Missing:
        if (self.strategy == "constant") != (self.value is not None):
            raise ValueError("'value' is required for strategy 'constant' and only allowed there")
        return self


class Categorical(Strict):
    columns: list[ColumnName] = Field(min_length=1, max_length=1024)
    encoding: Literal["onehot", "ordinal"] = "onehot"
    categories: dict[ColumnName, list[str | int]] | None = Field(
        default=None, description="declared category values per column (required under DP)"
    )


class SplitConfig(Strict):
    source: TableSource
    target: ColumnName
    task: Literal["classification", "regression"]
    features: list[ColumnName] | None = Field(default=None, min_length=1, max_length=4096)
    drop: list[ColumnName] = Field(default_factory=list, max_length=4096)
    missing: Missing = Field(default_factory=Missing)
    categorical: Categorical | None = None
    bounds: dict[ColumnName, Bound] = Field(
        default_factory=dict, description="public [low, high] per numeric column; values are clipped"
    )
    default_bounds: Bound | None = Field(
        default=None, description="bounds for numeric columns not in 'bounds'"
    )
    scale: Literal["none", "standard", "minmax"] = "none"
    classes: list[str | int] | None = Field(default=None, min_length=2, description="declared label values")
    splits: Splits
    stratify: bool = False
    seed: Seed | None = None

    @model_validator(mode="after")
    def _bounds_ordered(self) -> SplitConfig:
        for lo, hi in [*self.bounds.values(), *([self.default_bounds] if self.default_bounds else [])]:
            if not lo < hi:
                raise ValueError("bounds must satisfy low < high")
        if self.classes is not None and self.task != "classification":
            raise ValueError("'classes' only applies to classification")
        if self.features is not None and self.drop:
            raise ValueError("use either 'features' or 'drop', not both")
        return self


class TabularSplit(Plugin):
    name = "tabular/split"
    version = "1.0.0"
    Config = SplitConfig
    produces = TABULAR_SPLITS
    summary = "Tabular preprocessing (missing values, encoding, bounds, scaling) and train/val/test split"
    wraps = "pandas + scikit-learn"
    impl = "depa_train.plugins.tabular_split.impl:run"
    preload_modules = (
        "sklearn.model_selection",
        "pyarrow.csv",
        "pyarrow.parquet",
        "pyarrow.json",
        "pyarrow.feather",
    )

    def check(self, cfg: SplitConfig, v: ValidationContext) -> None:
        if isinstance(cfg.source, StepRef):
            v.require_ref(cfg.source, {TABLE}, "source")
        else:
            v.require_alias(cfg.source.input, "source.input")
        if cfg.features is not None and cfg.target in cfg.features:
            v.error("features", "must not contain the target column")
        if cfg.categorical and cfg.categorical.categories is not None:
            unknown = set(cfg.categorical.categories) - set(cfg.categorical.columns)
            if unknown:
                v.error(
                    "categorical.categories", f"columns not listed in categorical.columns: {sorted(unknown)}"
                )
        if not v.dp:
            return
        reason = "under privacy.mode 'dp' preprocessing must not be fitted on private data"
        if cfg.features is None:
            v.error("features", f"must be listed explicitly ({reason})")
        if cfg.missing.strategy in ("mean", "median"):
            v.error("missing.strategy", f"'{cfg.missing.strategy}' is data-dependent ({reason})")
        if cfg.scale == "standard":
            v.error("scale", f"'standard' is data-dependent; use 'minmax' with declared bounds ({reason})")
        cat_cols = set(cfg.categorical.columns) if cfg.categorical else set()
        if cfg.categorical and (
            cfg.categorical.categories is None or set(cfg.categorical.categories) != cat_cols
        ):
            v.error("categorical.categories", f"must declare the categories of every column ({reason})")
        if cfg.features is not None:
            unbounded = [
                c
                for c in cfg.features
                if c not in cat_cols and c not in cfg.bounds and cfg.default_bounds is None
            ]
            if unbounded:
                v.error("bounds", f"declare bounds for numeric features {unbounded} ({reason})")
        if cfg.task == "classification" and cfg.classes is None:
            v.error("classes", f"must declare the label values ({reason})")
