"""validate_against_contract: tier, privacy, output formats, datasets."""

from __future__ import annotations

import copy

import pytest

from conftest import base_recipe
from depa_train.api import validate_against_contract

CONTRACT = {
    "schema": "depa-training/contract/v2",
    "datasets": [{"dataset_id": "tdp-a/left"}, {"dataset_id": "tdp-b/right"}, {"dataset_id": "tdp-c/unused"}],
    "computation": {"tier": 1, "custom_ccr": None},
    "privacy": {"mode": "none"},
    "output": {"allowed_formats": ["skops", "onnx"]},
}


def contract(**patch):
    c = copy.deepcopy(CONTRACT)
    for key, value in patch.items():
        target = c
        *parents, leaf = key.split(".")
        for p in parents:
            target = target[p]
        target[leaf] = value
    return c


def test_consistent():
    assert validate_against_contract(base_recipe(), contract()) == []


@pytest.mark.parametrize(
    ("contract_patch", "recipe_patch", "needle"),
    [
        ({"computation.tier": 2}, {}, "recipe tier 1 does not match contract tier 2"),
        ({"computation.tier": 3}, {"tier": 3}, "custom_ccr: required for tier 3"),
        ({"computation.custom_ccr": {"measurement_id": "x"}}, {}, "must be null for tier 1/2"),
        ({"privacy": {"mode": "dp", "epsilon": 1.0, "delta": 1e-5}}, {}, "contract requires 'dp'"),
        (
            {"privacy": {"mode": "dp", "epsilon": 1.0, "delta": 1e-5}},
            {"privacy": {"mode": "dp", "epsilon": 2.0, "delta": 1e-6}},
            "epsilon: 2 exceeds contract epsilon 1",
        ),
        (
            {"privacy": {"mode": "dp", "epsilon": 1.0, "delta": 1e-6}},
            {"privacy": {"mode": "dp", "epsilon": 1.0, "delta": 1e-5}},
            "delta: 1e-05 exceeds contract delta 1e-06",
        ),
        (
            {"output.allowed_formats": ["onnx"]},
            {},
            "format 'skops' is not in contract output.allowed_formats",
        ),
        (
            {"datasets": [{"dataset_id": "tdp-a/left"}]},
            {},
            "dataset 'tdp-b/right' is not part of the contract",
        ),
        ({"privacy": {"mode": "maybe"}}, {}, "contract.privacy.mode: missing or invalid"),
        ({"output": {}}, {}, "contract.output.allowed_formats: missing"),
    ],
)
def test_inconsistent(contract_patch, recipe_patch, needle):
    errors = validate_against_contract(base_recipe(**recipe_patch), contract(**contract_patch))
    assert any(needle in e for e in errors), errors


def test_dp_recipe_under_non_dp_contract_is_allowed():
    recipe = base_recipe(privacy={"mode": "dp", "epsilon": 1.0, "delta": 1e-5})
    assert validate_against_contract(recipe, contract()) == []


def test_malformed_inputs_fail_closed():
    assert validate_against_contract(base_recipe(), {}) != []
    assert validate_against_contract("x", CONTRACT) == ["recipe: must be an object"]
