"""Recipe schema and cross-step semantic validation (no data is read)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import base_recipe, step
from depa_train.api import validate_recipe
from depa_train.errors import RecipeError
from depa_train.recipe import parse_recipe_text, recipe_json_schema
from depa_train.registry import registry

SCHEMAS = Path(__file__).resolve().parents[1] / "src" / "depa_train" / "schemas"


def errors_of(recipe) -> str:
    return "\n".join(validate_recipe(recipe))


def test_base_recipe_is_valid():
    assert validate_recipe(base_recipe()) == []


@pytest.mark.parametrize(
    "field", ["schema", "name", "tier", "family", "inputs", "steps", "privacy", "outputs"]
)
def test_required_top_level_fields(field):
    recipe = base_recipe()
    del recipe[field]
    assert f"'{field}' is a required property" in errors_of(recipe)


@pytest.mark.parametrize(
    ("patch", "needle"),
    [
        ({"schema": "depa-training/recipe/v2"}, "recipe.schema"),
        ({"tier": 4}, "recipe.tier"),
        ({"family": "spark"}, "recipe.family"),
        ({"unexpected": 1}, "Additional properties"),
        ({"inputs": [{"alias": "Bad Alias", "dataset_id": "a/b"}]}, "recipe.inputs[0].alias"),
        ({"inputs": [{"alias": "x", "dataset_id": "no-slash"}]}, "recipe.inputs[0].dataset_id"),
        ({"outputs": [{"name": "model", "format": "pickle"}]}, "recipe.outputs[0].format"),
        ({"privacy": {"mode": "dp"}}, "epsilon"),
        ({"privacy": {"mode": "none", "epsilon": 1.0}}, "recipe.privacy"),
        ({"privacy": {"mode": "dp", "epsilon": -1, "delta": 1e-5}}, "recipe.privacy.epsilon"),
        ({"metrics": ["Accuracy"]}, "recipe.metrics[0]"),
    ],
)
def test_schema_rejections(patch, needle):
    assert needle in errors_of(base_recipe(**patch))


def test_tier3_requires_custom_family():
    assert "tier 3 requires family 'custom'" in errors_of(base_recipe(tier=3))


def test_unknown_plugin_and_major():
    recipe = base_recipe()
    step(recipe, "train")["uses"] = "sklearn/train@2"
    assert "unknown plugin 'sklearn/train@2' (available: sklearn/train@1)" in errors_of(recipe)
    step(recipe, "train")["uses"] = "spark/join@1"
    assert "unknown plugin 'spark/join@1'" in errors_of(recipe)


def test_plugin_family_mismatch():
    recipe = base_recipe(family="torch")
    assert "'sklearn/train@1' is not available in family 'torch'" in errors_of(recipe)


def test_tier2_plugin_in_tier1_recipe():
    recipe = base_recipe(family="torch")
    recipe["steps"] = [{"id": "g", "uses": "torch/graph-train@1", "with": {}}]
    assert "'torch/graph-train@1' is tier 2, recipe is tier 1" in errors_of(recipe)


def test_tier3_plugin_only_in_tier3():
    recipe = base_recipe()
    recipe["steps"].append({"id": "c", "uses": "custom/entrypoint@1", "with": {"command": ["/bin/true"]}})
    assert "only valid in tier-3 recipes" in errors_of(recipe)


def test_duplicate_step_ids_and_aliases():
    recipe = base_recipe()
    recipe["steps"].append(dict(recipe["steps"][0]))
    recipe["inputs"].append({"alias": "left", "dataset_id": "tdp-a/other"})
    errs = errors_of(recipe)
    assert "duplicate step id 'join'" in errs
    assert "duplicate alias 'left'" in errs


def test_reference_to_later_or_unknown_step():
    recipe = base_recipe()
    step(recipe, "split")["with"]["source"] = {"step": "train"}
    assert "refers to unknown or later step 'train'" in errors_of(recipe)


def test_reference_kind_mismatch():
    recipe = base_recipe()
    step(recipe, "train")["with"]["data"] = {"step": "join"}
    assert "step 'join' produces 'table', expected one of ['tabular_splits']" in errors_of(recipe)


def test_undeclared_input_alias():
    recipe = base_recipe()
    step(recipe, "join")["with"]["tables"][0]["input"] = "ghost"
    assert "input alias 'ghost' is not declared" in errors_of(recipe)


def test_outputs_must_be_written_exactly_once_with_matching_format():
    recipe = base_recipe(outputs=[{"name": "model", "format": "skops"}, {"name": "extra", "format": "onnx"}])
    assert "output 'extra' is not written by any step" in errors_of(recipe)
    recipe = base_recipe(outputs=[{"name": "model", "format": "onnx"}])
    assert "output 'model' is declared with format 'onnx'" in errors_of(recipe)


def test_declared_metrics_must_be_producible():
    assert "metric 'dice_score' is not produced by any step" in errors_of(base_recipe(metrics=["dice_score"]))


def test_dp_requires_dp_capable_trainers():
    recipe = base_recipe(privacy={"mode": "dp", "epsilon": 1.0, "delta": 1e-5})
    assert "sklearn/train@1 cannot train under privacy.mode 'dp'" in errors_of(recipe)


def test_privacy_share_on_non_dp_recipe_rejected():
    recipe = base_recipe()
    step(recipe, "train")["uses"] = "xgboost/train@1"
    step(recipe, "train")["with"] = {
        "data": {"step": "split"},
        "privacy": {"epsilon": 1.0, "delta": 0},
        "save": {"output": "model", "format": "xgboost-json"},
    }
    recipe["outputs"] = [{"name": "model", "format": "xgboost-json"}]
    assert "with.privacy is set but recipe privacy.mode is 'none'" in errors_of(recipe)


def test_multi_trainer_shares_must_fit_budget():
    recipe = base_recipe(privacy={"mode": "dp", "epsilon": 1.0, "delta": 1e-5})
    xgb = {
        "data": {"step": "split"},
        "privacy": {"epsilon": 0.6, "delta": 0},
        "save": {"output": "model", "format": "xgboost-json"},
    }
    step(recipe, "train").update({"uses": "xgboost/train@1", "with": xgb})
    second = {
        "id": "train2",
        "uses": "xgboost/train@1",
        "with": {**xgb, "save": {"output": "m2", "format": "xgboost-json"}},
    }
    recipe["steps"].insert(3, second)
    recipe["outputs"] = [
        {"name": "model", "format": "xgboost-json"},
        {"name": "m2", "format": "xgboost-json"},
    ]
    assert "step epsilons sum to 1.2 > budget 1" in errors_of(recipe)
    second["with"]["privacy"] = {"epsilon": 0.4, "delta": 0}
    assert validate_recipe(recipe) == []


def test_yaml_parsing_is_strict():
    with pytest.raises(RecipeError, match="duplicate key 'name'"):
        parse_recipe_text("name: a\nname: b\n")
    with pytest.raises(RecipeError, match="not representable as JSON"):
        parse_recipe_text("when: 2025-01-01\n")
    with pytest.raises(RecipeError, match="not valid YAML"):
        parse_recipe_text("a: !!python/object/apply:os.system ['true']\n")


def test_committed_schemas_are_up_to_date():
    assert json.loads((SCHEMAS / "recipe.v1.json").read_text()) == recipe_json_schema()
    for plugin in registry().all():
        path = SCHEMAS / "plugins" / (plugin.ref.replace("/", "__") + ".json")
        assert json.loads(path.read_text()) == plugin.json_schema(), f"regenerate {path.name}"


def test_validation_does_not_import_frameworks():
    code = (
        "import sys, json; from depa_train.api import validate_recipe; "
        f"validate_recipe(json.loads({json.dumps(json.dumps(base_recipe()))})); "
        "print(sorted(m for m in sys.modules if m.split('.')[0] in "
        "('torch','numpy','pandas','duckdb','transformers','sklearn','xgboost','keras')))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]"
