"""Per-plugin ``with`` validation: JSON Schema plus static semantic checks."""

from __future__ import annotations

import pytest

from conftest import base_recipe, step
from depa_train.api import validate_recipe

DP = {"mode": "dp", "epsilon": 1.0, "delta": 1e-5}


def errs(recipe) -> str:
    return "\n".join(validate_recipe(recipe))


def torch_recipe(train_with: dict, **overrides) -> dict:
    recipe = base_recipe(family="torch", outputs=[{"name": "model", "format": "safetensors"}], metrics=[])
    recipe["steps"] = [*recipe["steps"][:2], {"id": "train", "uses": "torch/train@1", "with": train_with}]
    recipe.update(overrides)
    return recipe


MLP = {
    "kind": "factory",
    "config": {
        "layers": {"fc": {"class": "nn.Linear", "params": {"in_features": 6, "out_features": 1}}},
        "input": ["x"],
        "forward": [{"ops": ["fc", "torch.sigmoid"], "input": ["x"], "output": "y"}],
        "output": ["y"],
    },
}


def torch_with(**overrides) -> dict:
    cfg = {
        "data": {"kind": "tabular", "step": "split"},
        "task": "binary",
        "model": MLP,
        "loss": {"class": "torch.nn.BCELoss"},
        "training": {"epochs": 1, "batch_size": 16, "optimizer": {"name": "Adam", "params": {"lr": 0.01}}},
        "save": {"output": "model", "format": "safetensors"},
    }
    cfg.update(overrides)
    return cfg


def test_unknown_with_key_rejected():
    recipe = base_recipe()
    step(recipe, "split")["with"]["oops"] = True
    assert "recipe.steps[1].with: Additional properties are not allowed ('oops'" in errs(recipe)


def test_wrong_type_rejected():
    recipe = base_recipe()
    step(recipe, "split")["with"]["splits"] = {"train": "most"}
    assert "recipe.steps[1].with.splits.train" in errs(recipe)


def test_splits_must_sum_to_one():
    recipe = base_recipe()
    step(recipe, "split")["with"]["splits"] = {"train": 0.85, "val": 0.15, "test": 0.05}
    assert "splits must sum to 1" in errs(recipe)


def test_sql_duplicate_view_and_policy_errors_surface():
    recipe = base_recipe()
    w = step(recipe, "join")["with"]
    w["tables"][1]["name"] = "l"
    w["query"] = "SELECT * FROM read_csv('/etc/passwd')"
    e = errs(recipe)
    assert "duplicate view name 'l'" in e
    assert "table functions are not allowed" in e


def test_sql_requires_privacy_unit_under_dp():
    recipe = base_recipe(privacy=DP)
    del step(recipe, "join")["with"]["privacy_unit"]
    assert "privacy_unit: required when privacy.mode is 'dp'" in errs(recipe)


def test_tabular_split_dp_rules():
    recipe = base_recipe(privacy=DP)
    w = step(recipe, "split")["with"]
    w.update({"scale": "standard", "missing": {"strategy": "mean"}, "bounds": {"x1": [-5, 5]}})
    del w["classes"]
    del w["categorical"]["categories"]
    e = errs(recipe)
    for needle in (
        "'standard' is data-dependent",
        "'mean' is data-dependent",
        "declare bounds for numeric features",
        "must declare the categories",
        "must declare the label values",
    ):
        assert needle in e


def test_tabular_split_bounds_ordered():
    recipe = base_recipe()
    step(recipe, "split")["with"]["bounds"] = {"x1": [5, -5]}
    assert "bounds must satisfy low < high" in errs(recipe)


def test_sklearn_disallowed_estimator_and_params():
    recipe = base_recipe()
    w = step(recipe, "train")["with"]
    w["estimator"] = {"class": "sklearn.utils.estimator_html_repr", "params": {"memory": "/tmp"}}
    w["preprocess"] = [{"class": "sklearn.preprocessing.FunctionTransformer"}]
    e = errs(recipe)
    assert "'sklearn.utils.estimator_html_repr' is not an allowlisted estimator" in e
    assert "'sklearn.preprocessing.FunctionTransformer' is not an allowlisted transformer" in e
    assert "parameters ['memory'] are not allowed" in e


def test_xgboost_dp_restrictions():
    recipe = base_recipe(privacy=DP, outputs=[{"name": "model", "format": "xgboost-json"}])
    step(recipe, "train").update(
        uses="xgboost/train@1",
        **{
            "with": {
                "data": {"step": "split"},
                "objective": "reg:squarederror",
                "early_stopping_rounds": 5,
                "save": {"output": "model", "format": "xgboost-json"},
            }
        },
    )
    e = errs(recipe)
    assert "only established for 'binary:logistic'" in e
    assert "early stopping on private validation data is not DP" in e


def test_torch_valid_and_dp_requires_clipping():
    assert errs(torch_recipe(torch_with())) == ""
    assert "max_grad_norm: required when privacy.mode is 'dp'" in errs(torch_recipe(torch_with(), privacy=DP))


def test_torch_disallowed_model_loss_optimizer():
    model = {
        "kind": "factory",
        "config": {"layers": {"x": {"class": "os.system"}}, "input": [], "forward": [], "output": []},
    }
    e = errs(
        torch_recipe(
            torch_with(
                model=model,
                loss={"class": "torch.load"},
                training={"epochs": 1, "batch_size": 4, "optimizer": {"name": "LBFGS"}},
            )
        )
    )
    assert "class 'os.system' is not allowlisted" in e
    assert "'torch.load' is not an allowlisted loss" in e
    assert "'LBFGS' is not an allowlisted optimizer" in e


def test_torch_arch_weight_params_denied():
    model = {"kind": "arch", "name": "torchvision.resnet18", "params": {"weights": "IMAGENET1K_V1"}}
    assert "parameters ['weights'] are not allowed" in errs(torch_recipe(torch_with(model=model)))
    model = {"kind": "arch", "name": "torchvision.not_a_model", "params": {}}
    assert "is not an allowlisted architecture" in errs(torch_recipe(torch_with(model=model)))


def test_lightning_backend_not_dp():
    w = torch_with(
        backend="lightning",
        training={"epochs": 1, "batch_size": 4, "optimizer": {"name": "SGD"}, "max_grad_norm": 1.0},
    )
    assert "Lightning backend does not support DP-SGD" in errs(torch_recipe(w, privacy=DP))


def test_keras_config_allowlist():
    lam = {
        "module": "keras",
        "class_name": "Sequential",
        "config": {
            "layers": [
                {
                    "module": "keras.layers",
                    "class_name": "Lambda",
                    "config": {"function": {"class_name": "__lambda__"}},
                },
                {"module": "os", "class_name": "Dense", "config": {"activation": "relu"}},
            ]
        },
    }
    recipe = torch_recipe({}, outputs=[{"name": "model", "format": "keras"}])
    recipe["steps"][2] = {
        "id": "train",
        "uses": "keras/train@1",
        "with": {
            "data": {"kind": "tabular", "step": "split"},
            "task": "binary",
            "model": lam,
            "loss": "eval",
            "optimizer": {"name": "adam"},
            "epochs": 1,
            "batch_size": 8,
            "save": {"output": "model", "format": "keras"},
        },
    }
    e = errs(recipe)
    assert "class 'Lambda' is not allowlisted" in e
    assert "module 'os' is not allowlisted" in e
    assert "not allowed (serialized Python functions)" in e
    assert "'eval' is not an allowlisted Keras loss" in e


def test_keras_not_dp_capable():
    model = {"module": "keras", "class_name": "Sequential", "config": {"layers": []}}
    recipe = torch_recipe({}, outputs=[{"name": "model", "format": "keras"}], privacy=DP)
    recipe["steps"][2] = {
        "id": "train",
        "uses": "keras/train@1",
        "with": {
            "data": {"kind": "tabular", "step": "split"},
            "task": "binary",
            "model": model,
            "loss": "binary_crossentropy",
            "optimizer": {"name": "adam"},
            "epochs": 1,
            "batch_size": 8,
            "save": {"output": "model", "format": "keras"},
        },
    }
    assert "keras/train@1 cannot train under privacy.mode 'dp'" in errs(recipe)


def test_hf_sft_dp_restrictions():
    recipe = base_recipe(
        family="llm", privacy=DP, outputs=[{"name": "adapter", "format": "safetensors"}], metrics=[]
    )
    recipe["steps"] = [
        recipe["steps"][0],
        {
            "id": "sft",
            "uses": "hf/sft@1",
            "with": {
                "base_model": {"input": "left"},
                "data": {"step": "join"},
                "format": {"kind": "prompt_completion", "template": "no placeholder"},
                "lora": {"target_modules": ["q_proj"]},
                "training": {"gradient_accumulation": 4},
                "save": {"output": "adapter", "format": "safetensors"},
            },
        },
    ]
    assert "template must contain '{prompt}' exactly once" in errs(recipe)
    recipe["steps"][1]["with"]["format"]["template"] = "Q: {prompt}\nA:"
    assert "must be 1 under DP" in errs(recipe)


def test_custom_entrypoint_rules():
    recipe = base_recipe(
        tier=3, family="custom", outputs=[{"name": "model", "format": "onnx"}], metrics=["auc"]
    )
    recipe["steps"] = [
        {
            "id": "run",
            "uses": "custom/entrypoint@1",
            "with": {
                "command": ["train.sh"],
                "outputs": [{"output": "model", "format": "onnx"}],
                "metrics": ["auc"],
                "env": {"DEPA_INPUTS_DIR": "/x", "MODE": "fast"},
            },
        }
    ]
    e = errs(recipe)
    assert "must be an absolute path" in e
    assert "reserved variables ['DEPA_INPUTS_DIR']" in e
    recipe["steps"][0]["with"]["command"] = ["/opt/app/train"]
    recipe["steps"][0]["with"]["env"] = {"MODE": "fast"}
    assert validate_recipe(recipe) == []


@pytest.mark.parametrize("glob", ["../secrets/*", "/etc/passwd", "a/../../b", "x\\y"])
def test_file_globs_cannot_escape(glob):
    recipe = base_recipe()
    step(recipe, "join")["with"]["tables"][0]["files"] = glob
    assert "recipe.steps[0].with.tables[0].files" in errs(recipe)
