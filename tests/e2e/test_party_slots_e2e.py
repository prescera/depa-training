"""credit-risk deployed onto a FIXED-party environment (tdc, tdp-1..tdp-4) with the real trainer.

Requires the classic trainer stack (``depa_train`` + ``dp_xgboost``) in the running interpreter;
skipped otherwise. Run e.g. with the CCR-equivalent venv: ``<venv>/bin/python -m pytest tests/e2e``.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
import yaml

from conftest import REPO, Stack
from depa.deployment import DeployOptions, dc_setup, deploy_scenario
from depa.dev.stack import MEMBER_ID, virtual_release
from depa.env import load_env
from depa.profile import Profile, generate_party

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("dp_xgboost") is None or importlib.util.find_spec("depa_train") is None,
    reason="classic trainer stack (depa_train + dp_xgboost) not installed in this interpreter",
)


@pytest.fixture(params=["mock_stack", "sandbox_stack"])
def stack(request: pytest.FixtureRequest) -> Stack:
    return request.getfixturevalue(request.param)


def _fixed_env(stack: Stack, tmp: Path) -> Path:
    parties = [{"party_id": MEMBER_ID, "roles": ["dc-member"], "key_dir": str(stack.profile.keys_dir(MEMBER_ID))}]
    for pid, roles in [("tdc", ["tdc", "tdp"]), *((f"tdp-{i}", ["tdp"]) for i in range(1, 5))]:
        key_dir = tmp / "keys" / pid
        generate_party(Profile(home=tmp, key_dirs={pid: str(key_dir)}), party_id=pid, name=pid, roles=roles)
        parties.append({"party_id": pid, "roles": roles, "key_dir": str(key_dir)})
    releases = tmp / "releases"
    releases.mkdir()
    (releases / "depa-ccr-virtual-dev.json").write_text(
        json.dumps(
            {
                "release": virtual_release(),
                "deployment": {
                    "image": "local/depa-ccr@sha256:" + "0" * 64,
                    "resources": {"cpu": 4, "memory_gb": 16},
                    "env_allowlist": [
                        "DEPA_CONTRACT_ID",
                        "DEPA_RECIPE_DIGEST",
                        "DEPA_CS_URL",
                        "DEPA_KMS_URL",
                        "DEPA_PLATFORM",
                        "DEPA_TRUST_BUNDLE",
                        "DEPA_LOG_LEVEL",
                    ],
                },
            }
        )
    )
    storage = (tmp / "storage").as_uri() + "/"
    doc = {
        "collaborative": {
            "kms": {"url": stack.profile.kms.url, "service_cert": stack.profile.kms.cert},
            "cs": {"url": stack.profile.cs.url, "service_cert": stack.profile.cs.cert},
        },
        "parties": parties,
        "storage": {
            p: {"connector": "file", "uri": f"{storage}{p}/"} for p in ["tdc", "tdp-1", "tdp-2", "tdp-3", "tdp-4"]
        },
        "outputs": {"tdc": {"connector": "file", "uri": f"{storage}outputs/"}},
        "releases_dir": str(releases),
    }
    path = tmp / "depa-env.yaml"
    path.write_text(yaml.safe_dump(doc, sort_keys=False))
    return path


def test_credit_risk_on_fixed_party_slots(stack: Stack, tmp_path: Path) -> None:
    env = load_env(_fixed_env(stack, tmp_path))
    profile = env.profile(tmp_path / "home")
    dc_setup(env, profile, log=lambda m: None)
    summary = deploy_scenario(
        REPO / "scenarios" / "credit-risk",
        env,
        profile,
        DeployOptions(platform="local", prepare="never", work_dir=tmp_path / "work"),
        log=lambda m: None,
    )
    assert summary["status"] == "succeeded", summary
    assert summary["party_map"] == {
        "tdc": "tdc",
        "bank-a": "tdp-1",
        "bank-b": "tdp-2",
        "bureau": "tdp-3",
        "fintech": "tdp-4",
    }
    published = [s for s in summary["steps"] if s["step"] == "dataset.publish"]
    assert {s["dataset_id"].split("/")[0] for s in published} == {"tdp-1", "tdp-2", "tdp-3", "tdp-4"}
    assert len(published) == 6 and all(s["version"].endswith(".credit-risk") for s in published)
    result = summary["result"]
    assert set(result["metrics"]) == {"accuracy", "roc_auc"} and result["privacy_spent"]["mode"] == "dp"
    assert Path(result["outputs"]["model"][0]).name == "model.json"
    contract = profile.cs_client().get_contract(summary["contract_id"])["contract"]
    assert contract["parties"]["tdps"] == ["tdp-1", "tdp-2", "tdp-3", "tdp-4"]
    inputs = contract["computation"]["recipes"][0]["document"]["inputs"]
    assert all(i["dataset_id"].startswith("tdp-") for i in inputs)
