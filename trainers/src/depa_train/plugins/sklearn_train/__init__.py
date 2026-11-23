"""``sklearn/train@1``: allowlisted scikit-learn estimators/pipelines, saved with skops."""

from __future__ import annotations

from pydantic import Field

from ...registry import PREDICTIONS, TABULAR_SPLITS, Plugin, ValidationContext
from ...types import DottedName, JsonValue, OutputTarget, Seed, StepRef, Strict

ESTIMATORS = frozenset(
    {
        "sklearn.linear_model.LogisticRegression",
        "sklearn.linear_model.RidgeClassifier",
        "sklearn.linear_model.SGDClassifier",
        "sklearn.linear_model.LinearRegression",
        "sklearn.linear_model.Ridge",
        "sklearn.linear_model.Lasso",
        "sklearn.linear_model.ElasticNet",
        "sklearn.linear_model.SGDRegressor",
        "sklearn.svm.LinearSVC",
        "sklearn.svm.LinearSVR",
        "sklearn.svm.SVC",
        "sklearn.svm.SVR",
        "sklearn.tree.DecisionTreeClassifier",
        "sklearn.tree.DecisionTreeRegressor",
        "sklearn.ensemble.RandomForestClassifier",
        "sklearn.ensemble.RandomForestRegressor",
        "sklearn.ensemble.ExtraTreesClassifier",
        "sklearn.ensemble.ExtraTreesRegressor",
        "sklearn.ensemble.GradientBoostingClassifier",
        "sklearn.ensemble.GradientBoostingRegressor",
        "sklearn.ensemble.HistGradientBoostingClassifier",
        "sklearn.ensemble.HistGradientBoostingRegressor",
        "sklearn.neighbors.KNeighborsClassifier",
        "sklearn.neighbors.KNeighborsRegressor",
        "sklearn.naive_bayes.GaussianNB",
        "sklearn.neural_network.MLPClassifier",
        "sklearn.neural_network.MLPRegressor",
    }
)
TRANSFORMERS = frozenset(
    {
        "sklearn.preprocessing.StandardScaler",
        "sklearn.preprocessing.MinMaxScaler",
        "sklearn.preprocessing.MaxAbsScaler",
        "sklearn.preprocessing.RobustScaler",
        "sklearn.preprocessing.PolynomialFeatures",
        "sklearn.impute.SimpleImputer",
        "sklearn.decomposition.PCA",
        "sklearn.feature_selection.VarianceThreshold",
    }
)
# Parameters that could reach the filesystem, spawn processes or take callables are refused.
DENIED_PARAMS = frozenset({"memory", "n_jobs", "callbacks", "callback", "warm_start", "verbose"})


class SklearnComponent(Strict):
    class_: DottedName = Field(alias="class")
    params: dict[str, JsonValue] = Field(default_factory=dict)


class SklearnConfig(Strict):
    data: StepRef
    estimator: SklearnComponent
    preprocess: list[SklearnComponent] = Field(default_factory=list, max_length=16)
    save: OutputTarget
    seed: Seed | None = None


class SklearnTrain(Plugin):
    name = "sklearn/train"
    version = "1.0.0"
    families = frozenset({"classic"})
    Config = SklearnConfig
    produces = PREDICTIONS
    summary = "Allowlisted scikit-learn estimator (optionally a pipeline), saved as skops"
    wraps = "scikit-learn + skops"
    impl = "depa_train.plugins.sklearn_train.impl:run"
    preload_modules = ("sklearn.pipeline", "skops.io")
    trains = True

    def preload(self, cfg: SklearnConfig) -> list[str]:
        classes = [cfg.estimator.class_, *(c.class_ for c in cfg.preprocess)]
        return [
            *super().preload(cfg),
            *(c.rpartition(".")[0] for c in classes if c in ESTIMATORS | TRANSFORMERS),
        ]

    def check(self, cfg: SklearnConfig, v: ValidationContext) -> None:
        v.require_ref(cfg.data, {TABULAR_SPLITS}, "data")
        v.require_output(cfg.save, "save", {"skops"})
        if cfg.estimator.class_ not in ESTIMATORS:
            v.error("estimator.class", f"'{cfg.estimator.class_}' is not an allowlisted estimator")
        for i, comp in enumerate(cfg.preprocess):
            if comp.class_ not in TRANSFORMERS:
                v.error(f"preprocess[{i}].class", f"'{comp.class_}' is not an allowlisted transformer")
        for path, comp in [("estimator", cfg.estimator)] + [
            (f"preprocess[{i}]", c) for i, c in enumerate(cfg.preprocess)
        ]:
            denied = sorted(DENIED_PARAMS & set(comp.params))
            if denied:
                v.error(f"{path}.params", f"parameters {denied} are not allowed")
