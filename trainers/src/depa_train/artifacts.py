"""In-memory artifacts passed between steps (by step id)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np

Task = Literal["binary", "multiclass", "regression", "segmentation"]


@dataclass(frozen=True)
class ArraySplit:
    X: np.ndarray
    y: np.ndarray


@dataclass(frozen=True)
class TabularSplits:
    """Output of ``tabular/split``: encoded float32 features and labels per split."""

    task: Literal["classification", "regression"]
    feature_names: list[str]
    target: str
    splits: dict[str, ArraySplit]
    # Public per-feature value bounds after preprocessing (None where not declared). Derived only
    # from the recipe, never from data, so they can parameterise DP mechanisms.
    bounds: list[tuple[float, float] | None]
    n_classes: int | None

    @property
    def held_out(self) -> str | None:
        return "test" if "test" in self.splits else ("val" if "val" in self.splits else None)


@dataclass(frozen=True)
class Predictions:
    """Held-out predictions of a training step, consumed by ``eval/*`` steps.

    ``y_score``: probability of the positive class (binary, shape [n]), class probabilities
    (multiclass, [n, k]), values (regression, [n]) or per-pixel probabilities (segmentation).
    """

    task: Task
    split: str
    y_true: np.ndarray
    y_score: np.ndarray
