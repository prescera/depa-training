"""Contract authoring (YAML -> canonical v2), validation, policy evaluation and TDP verify."""

from __future__ import annotations

import copy
import datetime as dt
from pathlib import Path

import pytest

from conftest import FIXTURES, World, build_world, govern
from depa.contract.authoring import build_contract, load_dataset_template
from depa.contract.policy import evaluate_policy
from depa.contract.recipe import select_recipe, structural_errors
from depa.contract.validate import contract_errors
from depa.contract.verify import verify_contract
from depa.contract.yamlio import load_yaml_text, substitute
from depa.core.encoding import digest
from depa.errors import ValidationError, VerificationError
from depa.workflows import storage_manifest_loader

DIGEST = "sha256:" + "ab" * 32
NOW = dt.datetime(2026, 11, 23, tzinfo=dt.UTC)
SCENARIOS = Path(__file__).resolve().parents[3] / "scenarios"


def _env(tmp_path: Path) -> dict[str, str]:
    return {"MINI_STORAGE_URI": (tmp_path / "s").as_uri() + "/", "DEPA_KMS_URL": "https://kms.test/app"}


def _contract(tmp_path: Path) -> dict:
    return build_contract(
        FIXTURES / "mini" / "contract.yaml",
        env=_env(tmp_path),
        resolve_manifest=lambda d, v: DIGEST,
        contract_id="0b7f0d4e-6a3a-4d6e-9c55-2f1b8a7c1e00",
        now=NOW,
    )


def test_substitution_and_strict_yaml() -> None:
    assert substitute("a: ${X:-[1, 2]} b: ${Y}", {"Y": "y"}) == "a: [1, 2] b: y"
    with pytest.raises(ValidationError):
        substitute("${MISSING}", {})
    assert load_yaml_text("t: 2026-11-01T00:00:00Z") == {"t": "2026-11-01T00:00:00Z"}  # no datetime objects
    with pytest.raises(ValidationError):
        load_yaml_text("a: 1\na: 2\n")


def test_build_contract(tmp_path: Path) -> None:
    c = _contract(tmp_path)
    assert c["schema"] == "depa-training/contract/v2"
    assert c["validity"] == {"not_before": "2026-11-23T00:00:00Z", "not_after": "2026-11-30T00:00:00Z"}
    assert [d["manifest_digest"] for d in c["datasets"]] == [DIGEST, DIGEST]
    recipe = c["computation"]["recipes"][0]
    assert recipe["digest"] == digest(recipe["document"]) and recipe["document"]["name"] == "mini-ok"
    assert c["computation"]["custom_ccr"] is None
    assert select_recipe(c, recipe["digest"]) == recipe["document"]
    assert contract_errors(c) == []


@pytest.mark.parametrize(
    ("mutate", "fragment"),
    [
        (lambda c: c["parties"].update(tdps=["bank-a"]), "parties.tdps"),
        (lambda c: c["validity"].update(not_after=c["validity"]["not_before"]), "not_before"),
        (lambda c: c["computation"].update(ccr_releases=[]), "ccr_releases"),
        (lambda c: c["computation"]["recipes"][0]["document"].update(name="x"), "digest"),
        (lambda c: c["privacy"].update(epsilon=1.0), "epsilon"),
        (lambda c: c["output"].update(recipients=["party:mallory#h1"]), "not a contract party"),
        (lambda c: c["output"].update(mode="tee-only"), "tee-only"),
        (lambda c: c["output"].update(allowed_formats=["onnx"]), "format"),
        (lambda c: c.update(extra=1), "Additional properties"),
        (lambda c: c["datasets"].append(dict(c["datasets"][0])), "more than once"),
        (lambda c: c["output"]["location"].update(connector="s3", uri="s3://b/p/"), "output.location"),
    ],
)
def test_validation_errors(tmp_path: Path, mutate, fragment: str) -> None:
    c = copy.deepcopy(_contract(tmp_path))
    mutate(c)
    errors = contract_errors(c)
    assert errors and any(fragment in e for e in errors), errors


def test_recipe_checks() -> None:
    recipe = load_yaml_text((FIXTURES / "mini" / "recipe.yaml").read_text())
    assert structural_errors(recipe) == []
    bad = dict(recipe, inputs=[{"alias": "../x", "dataset_id": "bank-a/loans"}], family="other")
    assert len(structural_errors(bad)) == 2
    with pytest.raises(VerificationError):
        select_recipe({"computation": {"recipes": []}}, DIGEST)


def test_policy_evaluation(tmp_path: Path) -> None:
    c = _contract(tmp_path)
    policy = load_dataset_template(FIXTURES / "mini" / "datasets" / "bank-a-loans.yaml", _env(tmp_path)).policy
    ok = evaluate_policy(policy, c, dataset_id="bank-a/loans", now=NOW)
    assert ok.errors == [] and any("virtual" in w for w in ok.warnings)
    strict = dict(
        policy,
        privacy={"require_dp": True, "max_epsilon": 1.0},
        allowed_tdcs=["other"],
        allowed_output_modes=["tee-only"],
        allowed_platforms=["aci-snp"],
    )
    found = evaluate_policy(strict, c, dataset_id="bank-a/loans", now=NOW)
    assert len(found.errors) == 4
    assert evaluate_policy(None, c, dataset_id="x/y", now=NOW).errors


def test_verify_happy_path(world: World) -> None:
    record = world.cs.get_contract(world.contract["contract_id"])
    for party in ("tdc", "bank-a", "bureau"):
        report = verify_contract(
            world.contract,
            party_id=party,
            kms=world.kms,
            cs=world.cs,
            record=record,
            manifest_loader=storage_manifest_loader(),
        )
        assert report.ok, report.errors
    report = verify_contract(
        world.contract,
        party_id="bank-a",
        kms=world.kms,
        cs=world.cs,
        record=record,
        manifest_loader=storage_manifest_loader(),
    )
    assert any("manifest signature and digest verified" in i for i in report.info)


def test_verify_detects_problems(tmp_path: Path) -> None:
    w = build_world(tmp_path, propose=False)
    contract = build_contract(w.scenario / "contract.yaml", env=w.env, resolve_manifest=lambda d, v: DIGEST)
    report = verify_contract(contract, party_id="bank-a", kms=w.kms, cs=w.cs)
    assert any("manifest_digest differs" in e for e in report.errors)
    assert verify_contract(contract, party_id="mallory", kms=w.kms, cs=w.cs).errors
    # A revoked release must block signing.
    from conftest import mock_clients

    kms, cs = mock_clients(w.dc)
    govern(kms, w.profile, [{"name": "revoke_ccr_release", "args": {"release_id": "depa-ccr-virtual-dev"}}])
    good = build_contract(
        w.scenario / "contract.yaml",
        env=w.env,
        resolve_manifest=lambda d, v: cs.get_dataset_version(d, v)["manifest_digest"],
    )
    report = verify_contract(good, party_id="bureau", kms=kms, cs=cs)
    assert any("revoked" in e for e in report.errors)


@pytest.mark.parametrize("name", ["covid", "brats", "credit-risk", "cifar10", "mnist", "llm-sft"])
def test_scenario_templates_parse(name: str, tmp_path: Path) -> None:
    """Every scenario descriptor/contract template renders with defaults (recipe.yaml may be pending)."""
    env: dict[str, str] = {}
    templates = [load_dataset_template(p, env) for p in sorted((SCENARIOS / name / "datasets").glob("*.yaml"))]
    assert templates
    contract_doc = load_yaml_text(substitute((SCENARIOS / name / "contract.yaml").read_text(), env))
    owners = {t.dataset_id.split("/")[0] for t in templates}
    assert set(contract_doc["parties"]["tdps"]) == owners
    assert {d["dataset_id"] for d in contract_doc["datasets"]} == {t.dataset_id for t in templates}
    if (SCENARIOS / name / "recipe.yaml").exists():
        c = build_contract(SCENARIOS / name / "contract.yaml", env=env, resolve_manifest=lambda d, v: DIGEST)
        assert contract_errors(c) == []
        for t in templates:
            assert evaluate_policy(t.policy, c, dataset_id=t.dataset_id, now=NOW).errors == []


def test_legal_text_digest_is_checked(world: World, tmp_path: Path) -> None:
    text = b"These terms govern the use of the trained model.\n"
    (world.scenario / "terms.txt").write_bytes(text)
    template = world.scenario / "contract.yaml"
    template.write_text(
        template.read_text().replace(
            "terms: {governing_law: IN}",
            "terms: {governing_law: IN, uri: 'https://terms.example/t.txt', legal_text_path: terms.txt}",
        )
    )
    from depa.workflows import catalog_resolver

    contract = build_contract(template, env=world.env, resolve_manifest=catalog_resolver(world.cs))
    import hashlib

    assert contract["terms"]["legal_text_digest"] == "sha256:" + hashlib.sha256(text).hexdigest()
    assert "legal_text_path" not in contract["terms"]
    good = verify_contract(contract, party_id="tdc", kms=world.kms, cs=world.cs, legal_text_loader=lambda uri: text)
    assert good.ok and any("legal text verified" in i for i in good.info)
    bad = verify_contract(contract, party_id="tdc", kms=world.kms, cs=world.cs, legal_text_loader=lambda uri: b"other")
    assert any("does not match" in e for e in bad.errors)
