from __future__ import annotations

import copy
import json
import random
from pathlib import Path
from typing import Any

import pytest

from depa_train.executor import execute


def base_recipe(**overrides: Any) -> dict[str, Any]:
    """A small valid classic recipe: SQL join -> split -> sklearn -> eval."""
    recipe: dict[str, Any] = {
        "schema": "depa-training/recipe/v1",
        "name": "test-recipe",
        "tier": 1,
        "family": "classic",
        "inputs": [
            {"alias": "left", "dataset_id": "tdp-a/left"},
            {"alias": "right", "dataset_id": "tdp-b/right"},
        ],
        "steps": [
            {
                "id": "join",
                "uses": "sql/duckdb@1",
                "with": {
                    "tables": [
                        {"name": "l", "input": "left", "files": "*.csv", "format": "csv"},
                        {"name": "r", "input": "right", "files": "*.csv", "format": "csv"},
                    ],
                    "query": "SELECT l.id, l.x1, l.x2, r.x3, r.cat, l.label FROM l JOIN r ON l.id = r.id",
                    "privacy_unit": "id",
                },
            },
            {
                "id": "split",
                "uses": "tabular/split@1",
                "with": {
                    "source": {"step": "join"},
                    "target": "label",
                    "task": "classification",
                    "features": ["x1", "x2", "x3", "cat"],
                    "categorical": {"columns": ["cat"], "categories": {"cat": ["a", "b", "c"]}},
                    "bounds": {"x1": [-5, 5], "x2": [-5, 5], "x3": [-5, 5]},
                    "classes": [0, 1],
                    "splits": {"train": 0.7, "val": 0.1, "test": 0.2},
                    "seed": 1,
                },
            },
            {
                "id": "train",
                "uses": "sklearn/train@1",
                "with": {
                    "data": {"step": "split"},
                    "estimator": {
                        "class": "sklearn.linear_model.LogisticRegression",
                        "params": {"max_iter": 200},
                    },
                    "save": {"output": "model", "format": "skops"},
                },
            },
            {
                "id": "evaluate",
                "uses": "eval/classification@1",
                "with": {"predictions": {"step": "train"}, "metrics": ["accuracy", "roc_auc"]},
            },
        ],
        "privacy": {"mode": "none"},
        "outputs": [{"name": "model", "format": "skops"}],
        "metrics": ["accuracy", "roc_auc"],
    }
    recipe.update(overrides)
    return copy.deepcopy(recipe)


def step(recipe: dict[str, Any], step_id: str) -> dict[str, Any]:
    return next(s for s in recipe["steps"] if s["id"] == step_id)


def write_tabular_inputs(root: Path, n: int = 400, seed: int = 0) -> Path:
    """Two CSV datasets joinable on ``id`` with a learnable binary label."""
    rng = random.Random(seed)
    inputs = root / "inputs"
    (inputs / "left").mkdir(parents=True)
    (inputs / "right").mkdir(parents=True)
    left = ["id,x1,x2,label"]
    right = ["id,x3,cat"]
    for i in range(n):
        x1, x2, x3 = rng.gauss(0, 1), rng.gauss(0, 1), rng.gauss(0, 1)
        cat = rng.choice("abc")
        label = int(x1 + 0.5 * x2 - x3 + (0.8 if cat == "a" else 0) + rng.gauss(0, 0.5) > 0)
        left.append(f"{i},{x1:.4f},{x2:.4f},{label}")
        right.append(f"{i},{x3:.4f},{cat}")
    (inputs / "left" / "left.csv").write_text("\n".join(left) + "\n")
    (inputs / "right" / "right.csv").write_text("\n".join(right) + "\n")
    return inputs


def run_recipe(recipe: dict[str, Any], inputs: Path, tmp_path: Path) -> dict[str, Any]:
    out = tmp_path / "outputs"
    result = execute(recipe, inputs, out, tmp_path / "result.json", install_guard=False)
    assert json.loads((tmp_path / "result.json").read_text()) == result
    return result


@pytest.fixture
def tabular_inputs(tmp_path: Path) -> Path:
    return write_tabular_inputs(tmp_path)
