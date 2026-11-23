"""Signed actions through the console against the mock services: policies, kill switch, signing,
governance ballots, publishing, model retrieval and scenario deploy."""

from __future__ import annotations

import html
import json
import re
import shutil
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from depa.contract.authoring import build_contract
from depa.dev import stack as dev_stack
from depa.profile import Profile
from depa.workflows import catalog_resolver
from fastapi.testclient import TestClient
from mockdc import MockDC

from conftest import TOKEN, TRAINER, Stack, csrf, post, wait_task
from depa_console.app import create_app
from depa_console.security import Auth
from depa_console.state import Console, Settings

FUTURE = (date.today() + timedelta(days=60)).isoformat()
VALID_POLICY = {
    "platforms": ["virtual"],
    "confirm_virtual": "on",
    "tiers": ["1"],
    "tdcs": "tdc",
    "purposes": "TRAINING",
    "require_dp": "on",
    "max_epsilon": "3",
    "max_delta": "1e-05",
    "output_modes": ["tdc", "tee-only"],
    "audiences": "",
    "custom_measurements": "",
    "max_runs": "10",
    "not_after": FUTURE,
}


def _propose(stack: Stack, **changes: Any) -> str:
    """A fresh contract proposed by the TDC (optionally with top-level fields changed)."""
    profile = stack.profile("tdc")
    cs = profile.cs_client()
    env = {"MINI_STORAGE_URI": (stack.tmp / "storage").as_uri() + "/", "DEPA_KMS_URL": str(profile.kms.url)}
    contract = build_contract(
        stack.scenarios / "mini" / "contract.yaml",
        env=env,
        resolve_manifest=catalog_resolver(cs),
        customize=lambda doc: doc.update(changes),
    )
    cs.propose_contract(profile.signer(), contract)
    return str(contract["contract_id"])


def test_policy_form_validation(stack: Stack, client_for) -> None:
    client = client_for("bank-a")
    path = "/datasets/bank-a/loans/policy"
    before = stack.dc.kms.datasets["bank-a/loans"]["policy"]
    form = client.get(path).text
    assert 'value="tdc"' in form  # starts from the policy in force

    bad = {
        **VALID_POLICY,
        "platforms": ["virtual"],
        "confirm_virtual": "",
        "tdcs": "*, Not_A_Party",
        "purposes": "training",
        "max_epsilon": "-1",
        "max_delta": "0.5",
        "output_modes": [],
        "max_runs": "0",
        "not_after": "2001-01-01",
    }
    response = post(client, path, bad, token=csrf(client, path))
    assert response.status_code == 422
    for message in ("confirm it is for development data only", "not party ids: Not_A_Party",
                    "UPPER_CASE", "must be &gt; 0", "allow at least one output mode", "1 to 10000",
                    "a date after today"):  # fmt: skip
        assert message in response.text, message
    assert stack.dc.kms.datasets["bank-a/loans"]["policy"] == before

    wildcard = post(client, path, {**VALID_POLICY, "tdcs": "*"}, token=csrf(client, path))
    assert wildcard.status_code == 422 and "confirm it explicitly" in wildcard.text

    ok = post(client, path, {**VALID_POLICY, "max_runs": "7"}, token=csrf(client, path))
    assert ok.status_code == 303
    policy = stack.dc.kms.datasets["bank-a/loans"]["policy"]
    assert policy["max_runs_per_contract"] == 7 and policy["not_after"] == f"{FUTURE}T00:00:00Z"
    assert policy["allowed_tdcs"] == ["tdc"] and policy["privacy"] == {
        "require_dp": True,
        "max_epsilon": 3.0,
        "max_delta": 1e-05,
    }
    profile = stack.profile("bank-a")
    profile.kms_client().set_policy(profile.signer(), "bank-a/loans", before)


def test_new_dataset_gets_restrictive_defaults(stack: Stack, client_for) -> None:
    profile = stack.profile("bank-a")
    profile.kms_client().create_dataset(profile.signer(), "bank-a/fresh", "no policy yet")
    text = client_for("bank-a").get("/datasets/bank-a/fresh/policy").text
    assert 'value="tee-only" checked' in text and 'value="virtual" checked' not in text
    assert 'name="tdcs" value=""' in text and 'value="1"' in text


def test_sign_refused_when_verify_fails(stack: Stack, client_for) -> None:
    contract_id = _propose(stack, purpose="MARKETING")
    client = client_for("bank-a")
    page = client.get(f"/contracts/{contract_id}").text
    assert "purpose MARKETING is not allowed" in page and "Sign contract" not in page
    response = post(client, f"/contracts/{contract_id}/sign", {"confirm": "on"})
    assert response.status_code == 303
    assert "refusing to sign" in client.get(response.headers["location"]).text
    assert stack.dc.contracts[contract_id]["signatures"] == []


def test_sign_and_revoke(stack: Stack, client_for) -> None:
    contract_id = _propose(stack)
    client = client_for("bank-a")
    assert "Sign contract" in client.get(f"/contracts/{contract_id}").text
    post(client, f"/contracts/{contract_id}/sign", {})  # confirmation missing
    assert stack.dc.contracts[contract_id]["signatures"] == []
    post(client, f"/contracts/{contract_id}/sign", {"confirm": "on"})
    assert [s["party"] for s in stack.dc.contracts[contract_id]["signatures"]] == ["bank-a"]
    post(client, f"/contracts/{contract_id}/revoke", {"confirm": "on", "reason": ""})
    assert stack.dc.contracts[contract_id]["state"] == "draft"
    post(client, f"/contracts/{contract_id}/revoke", {"confirm": "on", "reason": "test"})
    assert stack.dc.contracts[contract_id]["state"] == "revoked"


def test_kill_switch(stack: Stack, client_for) -> None:
    client = client_for("bureau")
    base = "/datasets/bureau/scores"
    post(client, f"{base}/suspend", {"reason": "incident"})
    assert stack.dc.kms.datasets["bureau/scores"]["suspended"] is False
    assert "tick the confirmation" in client.get(base).text
    post(client, f"{base}/suspend", {"reason": "incident", "confirm": "on"})
    assert stack.dc.kms.datasets["bureau/scores"]["suspended"] is True
    assert "Resume releases" in client.get(base).text
    post(client, f"{base}/resume", {"reason": "resolved", "confirm": "on"})
    assert stack.dc.kms.datasets["bureau/scores"]["suspended"] is False


def test_governance_propose_and_vote(stack: Stack, client_for) -> None:
    client = client_for("dc-member-1")
    post(client, "/governance/propose/settings", {"allow_virtual_attestation": "true", "expires_days": "7"})
    assert "development only" in client.get("/governance").text  # refused without confirmation
    post(client, "/governance/propose/party", {"record": "{not json", "expires_days": "7"})
    assert "not a valid party record" in client.get("/governance").text

    count = len(stack.dc.kms.proposals)
    post(client, "/governance/propose/settings", {"allow_virtual_attestation": "true", "confirm": "on",
                                                  "expires_days": "3"})  # fmt: skip
    proposal = list(stack.dc.kms.proposals.values())[count]
    assert proposal["state"] == "open"
    page = client.get(f"/governance/kms/proposals/{proposal['proposal_id']}/vote?ballot=approve")
    assert page.status_code == 200 and "allow_virtual_attestation" in page.text
    post(client, f"/governance/kms/proposals/{proposal['proposal_id']}/vote", {"ballot": "approve"})
    assert proposal["state"] == "accepted"


def test_publish_dataset_version(stack: Stack, client_for) -> None:
    extra = stack.tmp / "extra" / "datasets"
    extra.mkdir(parents=True)
    template = extra / "loans-v2.yaml"
    source = stack.scenarios / "mini" / "datasets" / "bank-a-loans.yaml"
    template.write_text(source.read_text().replace('"2026-11-01"', '"2026-12-01"'))
    data = stack.tmp / "extra" / "data"
    shutil.copytree(stack.scenarios / "mini" / "data" / "alpha", data)
    client = client_for("bank-a")
    response = post(client, "/datasets/publish", {"template": str(template), "source": str(data)})
    status = wait_task(client, response.headers["location"])
    assert status["state"] == "succeeded", client.get(response.headers["location"]).text
    page = client.get(response.headers["location"]).text
    assert "upload 1/" in page and "published bank-a/loans@2026-12-01" in page
    assert re.search(r"encrypting [^ ]+\.csv: 0%", page) and re.search(r"encrypted [^ ]+\.csv", page)
    assert ("bank-a/loans", "2026-12-01") in stack.dc.cs.catalog
    again = post(client, "/datasets/publish", {"template": str(template), "source": str(data)})
    assert wait_task(client, again.headers["location"])["state"] == "failed"
    assert "immutable" in client.get(again.headers["location"]).text
    refused = post(client_for("bureau"), "/datasets/publish", {"template": str(template)})
    assert refused.headers["location"] == "/datasets"


def test_run_receipt_and_model_retrieval(stack: Stack, client_for, tmp_path: Path) -> None:
    tdp = client_for("bank-a").get(f"/runs/{stack.run_id}").text
    assert "verified" in tdp and "Fetch and decrypt" not in tdp
    client = client_for("tdc")
    page = client.get(f"/runs/{stack.run_id}").text
    assert 'badge-ok">verified' in page and "started" in page and "Fetch and decrypt the model" in page
    target = tmp_path / "model"
    response = post(client, f"/runs/{stack.run_id}/fetch", {"output": "model", "destination": str(target)})
    status = wait_task(client, response.headers["location"])
    assert status["state"] == "succeeded", client.get(response.headers["location"]).text
    files = list(target.iterdir())
    assert files and json.loads(files[0].read_text())["learner"]["rows"] == 500
    refused = post(client, f"/runs/{stack.run_id}/fetch", {"output": "model", "destination": str(target)})
    assert refused.headers["location"] == f"/runs/{stack.run_id}"  # destination not empty


def test_ledger_check_failure_is_reported(stack: Stack, client_for) -> None:
    client = client_for("tdc")
    assert post(client, f"/runs/{stack.run_id}/ledger", {"txid": "bad"}).headers["location"] == f"/runs/{stack.run_id}"
    response = post(client, f"/runs/{stack.run_id}/ledger", {"txid": "2.42"})
    status = wait_task(client, response.headers["location"])
    assert status["state"] == "failed" and "GET /cs/app/receipt" in status["error"]
    assert 'badge-bad">failed' in client.get(f"/runs/{stack.run_id}").text


def test_scenario_deploy_with_live_progress(stack: Stack, client_for) -> None:
    client = client_for("tdc")
    assert "mini" in client.get("/scenarios").text
    bad = post(client, "/scenarios/deploy", {"scenario": "../etc", "platform": "local", "prepare": "auto",
                                             "act_for": "all"})  # fmt: skip
    assert bad.headers["location"] == "/scenarios"
    response = post(client, "/scenarios/deploy", {"scenario": "mini", "platform": "local", "prepare": "never",
                                                  "act_for": "all", "run": "on"})  # fmt: skip
    location = response.headers["location"]
    status = wait_task(client, location, timeout=300)
    assert status["state"] == "succeeded", client.get(location).text
    with client.stream("GET", f"{location}/events") as events:
        body = "".join(events.iter_text())
    assert "event: line" in body and "contract.reuse" in body and 'event: end\ndata: {"state": "succeeded"}' in body
    assert '"status": "succeeded"' in html.unescape(client.get(location).text)


def test_scenario_deploy_cancel(stack: Stack, client_for) -> None:
    client = client_for("tdc")
    response = post(client, "/scenarios/deploy", {"scenario": "mini", "platform": "local", "prepare": "never",
                                                  "act_for": "all", "run": "on"})  # fmt: skip
    location = response.headers["location"]
    post(client, f"{location}/cancel", {})
    assert wait_task(client, location)["state"] == "cancelled"
    assert 'badge-bad">cancelled' in client.get(location).text


def test_propose_contract(stack: Stack, client_for) -> None:
    assert client_for("bank-a").get("/contracts/new").status_code == 403
    client = client_for("tdc")
    assert "Propose a contract" in client.get("/contracts").text
    template = str((stack.scenarios / "mini" / "contract.yaml").resolve())
    rendered = post(client, "/contracts/new", {"action": "render", "template": template, "mode": "dev"},
                    token=csrf(client, "/contracts/new"))  # fmt: skip
    assert rendered.status_code == 200 and "Valid." in rendered.text and "Propose as tdc" in rendered.text
    document = json.loads(html.unescape(re.search(r'name="contract"[^>]*>(.*?)</textarea>', rendered.text, re.S)[1]))
    assert document["computation"]["platforms"] == ["virtual"] and document["parties"]["tdc"] == "tdc"

    broken = post(client, "/contracts/new", {"action": "propose", "contract": '{"schema": "x"}'})
    assert broken.status_code == 422 and "cannot be proposed" in broken.text
    foreign = dict(document, parties={**document["parties"], "tdc": "someone"})
    upload = client.post(
        "/contracts/new",
        data={"csrf_token": csrf(client), "action": "propose"},
        files={"file": ("c.json", json.dumps(foreign).encode(), "application/json")},
    )
    assert upload.status_code == 422 and "names someone as TDC" in upload.text
    assert document["contract_id"] not in stack.dc.contracts

    proposed = post(client, "/contracts/new", {"action": "propose", "contract": json.dumps(document)})
    assert proposed.status_code == 303 and proposed.headers["location"] == f"/contracts/{document['contract_id']}"
    assert stack.dc.contracts[document["contract_id"]]["state"] == "draft"


def test_scenario_deploy_in_development_profile(stack: Stack, tmp_path: Path) -> None:
    """No environment file: the SDK derives one from the dev profile, registers parties, runs locally."""
    dc = MockDC()
    kms_url, cs_url, cert = dc.serve()
    try:
        home = tmp_path / "devhome"
        profile = Profile(home=home).with_overrides(
            kms_url=kms_url, kms_cert=str(cert), cs_url=cs_url, cs_cert=str(cert)
        )
        profile.save()
        dev_stack.configure_dev_governance(
            profile, [profile.kms_client(), profile.cs_client()], profile.kms_client(), profile.cs_client()
        )
        settings = Settings(home=home, scenarios_root=stack.scenarios, dev_trainer_cmd=TRAINER)
        console = Console(settings)
        with TestClient(create_app(console, Auth(token=TOKEN), allowed_hosts=("testserver",))) as client:
            client.get(f"/login?token={TOKEN}")
            gcp = post(client, "/scenarios/deploy", {"scenario": "mini", "platform": "gcp", "prepare": "never",
                                                     "act_for": "all"})  # fmt: skip
            assert gcp.headers["location"] == "/scenarios"
            form = {"scenario": "mini", "platform": "local", "prepare": "never", "act_for": "all", "run": "on"}
            response = post(client, "/scenarios/deploy", form)
            location = response.headers["location"]
            status = wait_task(client, location, timeout=300)
            assert status["state"] == "succeeded", client.get(location).text
        assert {"bank-a", "bureau", "tdc"} <= {i.party_id for i in console.held()}
        assert any(r["state"] == "completed" for r in dc.runs.values())
    finally:
        dc.stop()
