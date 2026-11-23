"""Pages render per role; navigation shows only what the active party's roles allow."""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from conftest import TOKEN, Stack, csrf, post
from depa_console.app import create_app
from depa_console.security import Auth
from depa_console.state import Console, Settings

ALL = ("/governance", "/datasets", "/contracts", "/runs")
ALLOWED = {
    "dc-member-1": {"/governance"},
    "bank-a": {"/datasets", "/contracts", "/runs"},
    "tdc": {"/contracts", "/runs"},
}


@pytest.mark.parametrize("party", sorted(ALLOWED))
def test_role_gated_pages(client_for, party: str) -> None:
    client = client_for(party)
    home = client.get("/")
    assert home.status_code == 200 and party in home.text
    for path in ALL:
        visible = path in ALLOWED[party]
        assert (f'href="{path}"' in home.text) is visible, path
        assert client.get(path).status_code == (200 if visible else 403), path
    for path in ("/scenarios", "/tasks"):
        assert client.get(path).status_code == 200


def test_overview_shows_services(client_for) -> None:
    text = client_for("tdc").get("/").text
    assert "2.0.0-mock" in text and "governance-approved" in text and "pinned certificate" in text


def test_party_switch(client_for) -> None:
    client = client_for("tdc")
    response = post(client, "/session/party", {"party_id": "bank-a"})
    assert response.status_code == 303
    assert client.get("/datasets").status_code == 200
    post(client, "/session/party", {"party_id": "not-held"})
    assert "no keys for party not-held" in client.get("/").text


def test_contract_detail_in_plain_language(stack: Stack, client_for) -> None:
    text = client_for("bank-a").get(f"/contracts/{stack.contract_id}").text
    for expected in ("Mini scenario", "TRAINING", "bank-a/loans", "bureau/scores", "ε = 2.5",
                     "SQL query over the inputs", "Train an XGBoost model"):  # fmt: skip
        assert expected in text, expected
    assert "You have signed this contract" in text and re.search(r"Runs used: [123] of 3", text)


def test_unknown_contract_is_not_found(stack: Stack, client_for) -> None:
    client = client_for("tdc")
    assert client.get("/contracts").status_code == 200
    assert client.get("/contracts/not-a-contract").status_code == 404


def test_governance_lists_proposals_members_and_constitution(client_for) -> None:
    text = client_for("dc-member-1").get("/governance").text
    assert "approve_ccr_release" in text and "set_peer_service" in text and "depa-ccr-virtual-dev" in text
    assert "<code>dc-member-1</code>" in text and "set_constitution" in text and "majority" in text
    assert "not available" not in text


def test_dataset_pages(client_for) -> None:
    client = client_for("bank-a")
    listing = client.get("/datasets").text
    assert "bank-a/loans" in listing and "bureau/scores" not in listing
    assert "Catalog listing unavailable" not in listing
    detail = client.get("/datasets/bank-a/loans").text
    assert "Kill switch" in detail and "bank-a/loans#1" in detail and "Suspend dataset" in detail
    assert "Version 1" in detail and "<code>2026-11-01</code>" in detail  # policy history, catalog versions
    assert client.get("/datasets/bureau/scores").status_code == 403
    assert client.get("/datasets?dataset_id=bureau/scores", follow_redirects=True).status_code == 200
    csrf(client, "/datasets/bank-a/loans/policy")


def test_profile_mode_without_environment_file(stack: Stack) -> None:
    """A plain profile (as `depa dev up` writes it): parties from $DEPA_HOME/keys, no scenario deploy."""
    console = Console(Settings(home=stack.home, scenarios_root=stack.scenarios))
    assert console.identity().party_id == "dc-member-1"
    with TestClient(create_app(console, Auth(token=TOKEN), allowed_hosts=("testserver",))) as client:
        client.get(f"/login?token={TOKEN}")
        assert "profile " + str(stack.home) in client.get("/").text
        scenarios = client.get("/scenarios").text
        assert "Development profile" in scenarios and 'value="aks" disabled' in scenarios
        assert client.get("/governance").status_code == 200
