"""DP accounting sanity: closed-form composition, ledger enforcement, Opacus and dp-xgboost spends."""

from __future__ import annotations

import math

import pytest

from conftest import base_recipe, run_recipe, step
from depa_train.errors import PrivacyError
from depa_train.privacy import PrivacyLedger
from depa_train.privacy.accounting import amplified_epsilon, composed_epsilon, per_round_epsilon
from depa_train.recipe import PrivacySpec
from depa_train.types import PrivacyShare


@pytest.mark.parametrize(
    ("eps", "rounds", "q"), [(4.0, 50, 0.5), (1.0, 100, 1.0), (0.5, 10, 0.1), (8.0, 1, 0.3)]
)
def test_per_round_inverts_composition(eps, rounds, q):
    per = per_round_epsilon(eps, rounds, q)
    assert composed_epsilon(per, rounds, q) == pytest.approx(eps, rel=1e-12)
    assert per >= eps / rounds  # subsampling only ever allows a larger per-round budget


def test_amplification_bounds():
    assert amplified_epsilon(1.0, 1.0) == pytest.approx(1.0)
    assert amplified_epsilon(1.0, 0.1) < 0.1 * math.e
    assert amplified_epsilon(1.0, 0.1) < 1.0
    with pytest.raises(ValueError):
        amplified_epsilon(1.0, 0.0)


def test_ledger_enforces_step_budget_and_composes():
    ledger = PrivacyLedger(PrivacySpec(mode="dp", epsilon=2.0, delta=1e-5))
    share = PrivacyShare(epsilon=1.0, delta=5e-6)
    with pytest.raises(PrivacyError, match="above its budget"):
        ledger.record("a", share, 1.01, 1e-6, "rdp")
    ledger.record("a", share, 0.99, 5e-6, "opacus-rdp")
    ledger.record("b", share, 1.0, 0.0, "dp-xgboost")
    summary = ledger.summary()
    assert summary["epsilon"] == pytest.approx(1.99)
    assert summary["accountant"] == "basic-composition(opacus-rdp,dp-xgboost)"
    ledger.check_total()
    with pytest.raises(PrivacyError):
        PrivacyLedger(PrivacySpec(mode="none")).record("a", share, 0.1, 0, "x")


def test_non_dp_summary_has_no_epsilon():
    assert PrivacyLedger(PrivacySpec(mode="none")).summary() == {
        "mode": "none",
        "epsilon": None,
        "delta": None,
        "accountant": "none",
    }


def _torch_dp_recipe(epsilon: float, delta: float = 1e-5, seed: int = 3) -> dict:
    recipe = base_recipe(
        family="torch",
        privacy={"mode": "dp", "epsilon": epsilon, "delta": delta},
        outputs=[{"name": "model", "format": "safetensors"}],
        metrics=[],
    )
    step(recipe, "split")["with"].update({"scale": "minmax", "default_bounds": [-5, 5]})
    recipe["steps"][2] = {
        "id": "train",
        "uses": "torch/train@1",
        "with": {
            "data": {"kind": "tabular", "step": "split"},
            "task": "binary",
            "model": {
                "kind": "factory",
                "config": {
                    "layers": {"fc": {"class": "nn.Linear", "params": {"in_features": 6, "out_features": 1}}},
                    "input": ["x"],
                    "forward": [{"ops": ["fc"], "input": ["x"], "output": "y"}],
                    "output": ["y"],
                },
            },
            "loss": {"class": "nn.BCEWithLogitsLoss"},
            "training": {
                "epochs": 3,
                "batch_size": 32,
                "optimizer": {"name": "SGD", "params": {"lr": 0.5}},
                "max_grad_norm": 1.0,
            },
            "save": {"output": "model", "format": "safetensors"},
            "seed": seed,
        },
    }
    del recipe["steps"][3]
    return recipe


def test_opacus_spend_is_reported_and_within_budget(tmp_path, tabular_inputs):
    pytest.importorskip("torchvision")  # torch family image (torch/train + Opacus)
    result = run_recipe(_torch_dp_recipe(2.0), tabular_inputs, tmp_path)
    assert result["status"] == "succeeded", result.get("error")
    spent = result["privacy_spent"]
    assert spent["accountant"] == "opacus-rdp" and spent["delta"] == 1e-5
    assert 1.8 <= spent["epsilon"] <= 2.0


def test_delta_must_be_below_one_over_n(tmp_path, tabular_inputs):
    pytest.importorskip("torchvision")  # torch family image (torch/train + Opacus)
    result = run_recipe(_torch_dp_recipe(2.0, delta=0.01), tabular_inputs, tmp_path)
    assert result["status"] == "failed" and "must be below 1/n_train" in result["error"]


def test_dp_noise_is_not_reproducible_from_the_public_seed(tmp_path, tabular_inputs):
    """Same recipe (same seed) twice: DP weights differ (secret noise); non-DP weights are identical."""
    pytest.importorskip("torchvision")  # torch family image (torch/train + Opacus)
    from safetensors.numpy import load_file

    def weights(recipe, name):
        run_recipe(recipe, tabular_inputs, tmp_path / name)
        return load_file(str(tmp_path / name / "outputs" / "model" / "model.safetensors"))

    dp_a, dp_b = weights(_torch_dp_recipe(2.0), "a"), weights(_torch_dp_recipe(2.0), "b")
    assert any((dp_a[k] != dp_b[k]).any() for k in dp_a)
    plain = _torch_dp_recipe(2.0)
    plain["privacy"] = {"mode": "none"}
    del plain["steps"][2]["with"]["training"]["max_grad_norm"]
    p_a, p_b = weights(plain, "c"), weights(plain, "d")
    assert all((p_a[k] == p_b[k]).all() for k in p_a)


def test_dp_xgboost_spend_equals_budget(tmp_path, tabular_inputs):
    pytest.importorskip("dp_xgboost")
    recipe = base_recipe(
        privacy={"mode": "dp", "epsilon": 3.0, "delta": 1e-5},
        outputs=[{"name": "model", "format": "xgboost-json"}],
    )
    step(recipe, "train").update(
        uses="xgboost/train@1",
        **{
            "with": {
                "data": {"step": "split"},
                "num_boost_round": 20,
                "params": {"max_depth": 3, "subsample": 0.5},
                "save": {"output": "model", "format": "xgboost-json"},
            }
        },
    )
    result = run_recipe(recipe, tabular_inputs, tmp_path)
    assert result["status"] == "succeeded", result.get("error")
    assert result["privacy_spent"]["epsilon"] == pytest.approx(3.0, rel=1e-9)
    assert result["privacy_spent"]["delta"] == 0.0


def test_dp_xgboost_requires_declared_bounds(tmp_path, tabular_inputs):
    pytest.importorskip("dp_xgboost")
    recipe = base_recipe(
        privacy={"mode": "dp", "epsilon": 3.0, "delta": 1e-5},
        outputs=[{"name": "model", "format": "xgboost-json"}],
    )
    step(recipe, "split")["with"]["bounds"] = {"x1": [-5, 5], "x2": [-5, 5]}
    step(recipe, "split")["with"]["default_bounds"] = None
    step(recipe, "train").update(
        uses="xgboost/train@1",
        **{"with": {"data": {"step": "split"}, "save": {"output": "model", "format": "xgboost-json"}}},
    )
    from depa_train.api import validate_recipe

    assert any("declare bounds" in e for e in validate_recipe(recipe))
