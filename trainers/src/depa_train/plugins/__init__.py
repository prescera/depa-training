"""Plugin catalogue. Importing it only loads specs (config models + static checks)."""

from __future__ import annotations

from ..registry import Plugin


def all_plugins() -> list[Plugin]:
    from .custom_entrypoint import CustomEntrypoint
    from .eval_metrics import EvalClassification, EvalRegression, EvalSegmentation
    from .hf_sft import HfSft
    from .keras_train import KerasTrain
    from .sklearn_train import SklearnTrain
    from .sql_duckdb import SqlDuckDB
    from .tabular_split import TabularSplit
    from .torch_graph import TorchGraphTrain
    from .torch_train import TorchTrain
    from .xgboost_train import XGBoostTrain

    return [
        SqlDuckDB(),
        TabularSplit(),
        SklearnTrain(),
        XGBoostTrain(),
        TorchTrain(),
        KerasTrain(),
        HfSft(),
        TorchGraphTrain(),
        CustomEntrypoint(),
        EvalClassification(),
        EvalRegression(),
        EvalSegmentation(),
    ]
