from __future__ import annotations

import numpy as np
import pandas as pd

from ...artifacts import ArraySplit, TabularSplits
from ...context import StepContext
from ...data.readers import load_table, split_indices
from ...errors import StepError
from . import SplitConfig


def _split(cfg: SplitConfig, y: np.ndarray, seed: int) -> dict[str, np.ndarray]:
    fractions = {"train": cfg.splits.train, "val": cfg.splits.val, "test": cfg.splits.test}
    if not (cfg.stratify and cfg.task == "classification"):
        return split_indices(len(y), fractions, seed)
    from sklearn.model_selection import train_test_split

    idx = np.arange(len(y))
    out: dict[str, np.ndarray] = {}
    rest = idx
    try:
        if cfg.splits.test > 0:
            rest, out["test"] = train_test_split(
                idx, test_size=cfg.splits.test, random_state=seed, stratify=y
            )
        if cfg.splits.val > 0:
            frac = cfg.splits.val / (cfg.splits.val + cfg.splits.train)
            rest, out["val"] = train_test_split(rest, test_size=frac, random_state=seed, stratify=y[rest])
    except ValueError:
        raise StepError("stratified split failed (too few samples in a class)") from None
    out["train"] = rest
    return {k: np.sort(v) for k, v in out.items()}


def _labels(cfg: SplitConfig, series: pd.Series) -> tuple[np.ndarray, int | None]:
    if cfg.task == "regression":
        if not pd.api.types.is_numeric_dtype(series):
            raise StepError(f"target '{cfg.target}' must be numeric for regression")
        return series.to_numpy(dtype=np.float32), None
    values = series.astype(str)
    classes = [str(c) for c in cfg.classes] if cfg.classes is not None else sorted(values.unique())
    index = {c: i for i, c in enumerate(classes)}
    codes = values.map(index)
    if codes.isna().any():
        raise StepError(f"target '{cfg.target}' contains values outside the declared classes")
    if len(classes) < 2:
        raise StepError("classification needs at least two classes")
    return codes.to_numpy(dtype=np.int64), len(classes)


def run(cfg: SplitConfig, ctx: StepContext) -> TabularSplits:
    df = load_table(ctx, cfg.source).to_pandas()
    if cfg.target not in df.columns:
        raise StepError(f"target column '{cfg.target}' not found")
    features = cfg.features or [c for c in df.columns if c != cfg.target and c not in set(cfg.drop)]
    missing_cols = [c for c in [*features, *cfg.drop] if c not in df.columns]
    if missing_cols:
        raise StepError(f"columns not found: {missing_cols}")
    df = df[[*features, cfg.target]]
    df = df[df[cfg.target].notna()]

    if cfg.categorical is not None:
        cat_cols = list(cfg.categorical.columns)
        unknown = [c for c in cat_cols if c not in features]
        if unknown:
            raise StepError(f"categorical columns are not features: {unknown}")
    elif ctx.dp:
        cat_cols = []
    else:
        cat_cols = [c for c in features if not pd.api.types.is_numeric_dtype(df[c])]
    num_cols = [c for c in features if c not in cat_cols]
    non_numeric = [c for c in num_cols if not pd.api.types.is_numeric_dtype(df[c])]
    if non_numeric:
        raise StepError(f"columns {non_numeric} are not numeric; declare them as categorical")

    if cfg.missing.strategy == "drop":
        df = df.dropna(subset=features)
    df = df.reset_index(drop=True)
    if len(df) == 0:
        raise StepError("no rows left after removing missing values")

    y, n_classes = _labels(cfg, df[cfg.target])
    parts = _split(cfg, y, ctx.seed)
    train = parts["train"]

    columns: list[np.ndarray] = []
    names: list[str] = []
    bounds: list[tuple[float, float] | None] = []
    for col in num_cols:
        x = df[col].to_numpy(dtype=np.float64)
        if cfg.missing.strategy == "constant":
            if not isinstance(cfg.missing.value, int | float):
                raise StepError("missing.value must be numeric for numeric columns")
            x = np.where(np.isnan(x), float(cfg.missing.value), x)
        elif cfg.missing.strategy in ("mean", "median"):
            stat = np.nanmean if cfg.missing.strategy == "mean" else np.nanmedian
            fill = stat(x[train]) if np.isfinite(x[train]).any() else 0.0
            x = np.where(np.isnan(x), fill, x)
        bound = cfg.bounds.get(col, cfg.default_bounds)
        if bound is not None:
            x = np.clip(x, bound[0], bound[1])
        if cfg.scale == "minmax":
            lo, hi = bound if bound is not None else (float(x[train].min()), float(x[train].max()))
            x = (x - lo) / (hi - lo) if hi > lo else np.zeros_like(x)
            out_bound: tuple[float, float] | None = (0.0, 1.0) if bound is not None else None
        elif cfg.scale == "standard":
            mu, sd = float(x[train].mean()), float(x[train].std())
            x = (x - mu) / (sd if sd > 0 else 1.0)
            out_bound = None
        else:
            out_bound = (float(bound[0]), float(bound[1])) if bound is not None else None
        columns.append(x)
        names.append(col)
        bounds.append(out_bound)

    for col in cat_cols:
        values = df[col].astype("string")
        if cfg.missing.strategy == "constant":
            values = values.fillna(str(cfg.missing.value))
        declared = cfg.categorical.categories if cfg.categorical and cfg.categorical.categories else None
        if declared is not None and col in declared:
            cats = [str(c) for c in declared[col]]
        else:
            cats = sorted(values.iloc[train].dropna().unique())
        encoding = cfg.categorical.encoding if cfg.categorical else "onehot"
        if encoding == "onehot":
            for cat in cats:
                columns.append((values == cat).fillna(False).to_numpy(dtype=np.float64))
                names.append(f"{col}={cat}")
                bounds.append((0.0, 1.0))
        else:
            index = {c: i for i, c in enumerate(cats)}
            columns.append(values.map(index).fillna(-1).to_numpy(dtype=np.float64))
            names.append(col)
            bounds.append((-1.0, float(len(cats) - 1)))

    if not columns:
        raise StepError("no feature columns selected")
    X = np.ascontiguousarray(np.column_stack(columns), dtype=np.float32)
    if not np.isfinite(X).all():
        raise StepError("features contain missing or non-finite values; configure 'missing'")
    splits = {name: ArraySplit(X=X[idx], y=y[idx]) for name, idx in parts.items()}
    ctx.log("split_finished", features=X.shape[1], **{f"rows_{k}": len(v) for k, v in parts.items()})
    return TabularSplits(
        task=cfg.task,
        feature_names=names,
        target=cfg.target,
        splits=splits,
        bounds=bounds,
        n_classes=n_classes,
    )
