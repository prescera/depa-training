"""Party slot mapping of native scenario parties onto a fixed-party environment."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from depa.contract.authoring import build_contract, load_dataset_template, map_dataset_id
from depa.contract.validate import contract_errors
from depa.core.encoding import digest
from depa.deployment import compute_party_map, locate_dataset_template, scenario_parties
from depa.env import DepaEnv, load_env
from depa.errors import ConfigError
from test_env import _pem

SCENARIOS = Path(__file__).resolve().parents[3] / "scenarios"
DIGEST = "sha256:" + "ab" * 32
VARS = {"DEPA_KMS_URL": "https://kms.example/app"}


def fixed_env(tmp: Path, slots: int = 4) -> DepaEnv:
    tdps = [f"tdp-{i}" for i in range(1, slots + 1)]
    doc = {
        "collaborative": {
            "kms": {"url": "https://kms.example/app", "service_cert": _pem()},
            "cs": {"url": "https://cs.example/app", "service_cert": _pem()},
        },
        "parties": [
            {"party_id": "dc-member-1", "roles": ["dc-member"], "key_dir": "keys/dc-member-1"},
            {"party_id": "tdc", "roles": ["tdc", "tdp"], "key_dir": "keys/tdc"},
            *({"party_id": p, "roles": ["tdp"], "key_dir": f"keys/{p}"} for p in tdps),
        ],
        "storage": {p: {"connector": "file", "uri": f"file:///srv/{p}/"} for p in [*tdps, "tdc"]},
        "outputs": {"tdc": {"connector": "file", "uri": "file:///srv/outputs/"}},
    }
    path = tmp / f"env-{slots}.yaml"
    path.write_text(yaml.safe_dump(doc))
    return load_env(path)


EXPECTED = {
    "credit-risk": {"tdc": "tdc", "bank-a": "tdp-1", "bank-b": "tdp-2", "bureau": "tdp-3", "fintech": "tdp-4"},
    "brats": {"tdc": "tdc", "brats-a": "tdp-1", "brats-b": "tdp-2", "brats-c": "tdp-3", "brats-d": "tdp-4"},
    "covid": {"tdc": "tdc", "cowin": "tdp-1", "icmr": "tdp-2", "index": "tdp-3"},
    "cifar10": {"tdc": "tdc", "cifar10": "tdp-1"},
    "mnist": {"tdc": "tdc", "mnist": "tdp-1"},
    "llm-sft": {"tdc": "tdc", "bank-a": "tdp-1", "shop-b": "tdp-2"},
}


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_every_scenario_maps_onto_the_fixed_parties(name: str, tmp_path: Path) -> None:
    env = fixed_env(tmp_path)
    scenario = SCENARIOS / name
    native = scenario_parties(scenario, VARS)
    native_tdc = next(p for p, r in native.items() if "tdc" in r)
    party_map = compute_party_map([p for p, r in native.items() if "tdp" in r], native_tdc, env)
    assert party_map == EXPECTED[name]

    contract = build_contract(
        scenario / "contract.yaml",
        env={**VARS, "DEPA_TDC": "tdc"},
        resolve_manifest=lambda d, v: DIGEST,
        party_map=party_map,
        version_suffix=f".{name}",
    )
    assert contract_errors(contract) == []
    env_ids = {p.party_id for p in env.parties}
    assert contract["parties"]["tdc"] == "tdc" and set(contract["parties"]["tdps"]) <= env_ids
    assert all(d["dataset_id"].split("/")[0] in env_ids for d in contract["datasets"])
    assert all(d["version"].endswith(f".{name}") for d in contract["datasets"])
    recipe = contract["computation"]["recipes"][0]
    assert recipe["digest"] == digest(recipe["document"])
    assert {i["dataset_id"] for i in recipe["document"]["inputs"]} <= {d["dataset_id"] for d in contract["datasets"]}
    assert contract["output"]["recipients"] == ["party:tdc#h1"]

    for path in sorted((scenario / "datasets").glob("*.yaml")):
        t = load_dataset_template(path, {**VARS, "DEPA_TDC": "tdc"})
        mapped_id = map_dataset_id(t.dataset_id, party_map)
        from depa.contract.authoring import DatasetTemplate

        placed = locate_dataset_template(
            env, DatasetTemplate(dict(t.descriptor, dataset_id=mapped_id), t.policy, t.source), platform="virtual"
        )
        owner = mapped_id.split("/")[0]
        assert placed.descriptor["location"]["uri"].startswith(f"file:///srv/{owner}/")
        assert placed.policy is not None and placed.policy["allowed_tdcs"] == ["tdc"]
        assert t.source is not None and t.source.path.is_relative_to(scenario)  # plaintext source unchanged


def test_overflow_is_a_configuration_error(tmp_path: Path) -> None:
    env = fixed_env(tmp_path, slots=2)
    with pytest.raises(ConfigError) as exc:
        compute_party_map(["bank-a", "bank-b", "bureau", "fintech"], "tdc", env)
    assert exc.value.exit_code == 2 and "4 data providers" in str(exc.value)


def test_native_ids_present_means_no_mapping(tmp_path: Path) -> None:
    env = fixed_env(tmp_path)
    assert compute_party_map(["tdp-2", "tdc"], "tdc", env) == {"tdc": "tdc", "tdp-2": "tdp-2"}


def test_parse_stages() -> None:
    from depa.deployment import parse_stages

    assert parse_stages([]) is None and parse_stages(["all"]) is None
    assert parse_stages(["keys,data", "model"]) == frozenset({"keys", "data", "model"})
    with pytest.raises(ConfigError) as exc:
        parse_stages(["keys,bogus"])
    assert exc.value.exit_code == 2


def test_parse_dc_steps() -> None:
    from depa.deployment import parse_dc_steps

    assert parse_dc_steps([]) is None and parse_dc_steps(["all"]) is None
    assert parse_dc_steps(["bootstrap-kms", "link,register"]) == frozenset({"bootstrap-kms", "link", "register"})
    with pytest.raises(ConfigError) as exc:
        parse_dc_steps(["bootstrap"])
    assert exc.value.exit_code == 2
