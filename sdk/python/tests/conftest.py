"""Shared fixtures: a governed mock Data Collaborative and the mini scenario published into it."""

from __future__ import annotations

import shutil
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from depa.clients.cs import CsClient
from depa.clients.http import ServiceEndpoint
from depa.clients.kms import KmsClient
from depa.contract.authoring import build_contract, load_dataset_template
from depa.dev.stack import virtual_release
from depa.profile import Profile, generate_party
from depa.storage.factory import party_connector
from depa.workflows import catalog_resolver, encrypt_dataset, publish_dataset, upload_directory
from mockdc import MockDC

FIXTURES = Path(__file__).parent / "fixtures"
FAKE_TRAINER = FIXTURES / "fake_trainer.py"


@dataclass
class World:
    dc: MockDC
    profile: Profile
    scenario: Path
    storage: Path
    env: dict[str, str]
    kms: KmsClient
    cs: CsClient
    contract: dict[str, Any] = field(default_factory=dict)

    def clients(self) -> tuple[KmsClient, CsClient]:
        return mock_clients(self.dc)


def mock_clients(dc: MockDC) -> tuple[KmsClient, CsClient]:
    kms = KmsClient(ServiceEndpoint("depa-kms", dc.kms.base_url), transport=dc.transport("kms"), backoff_s=0)
    cs = CsClient(ServiceEndpoint("depa-contract-service", dc.cs.base_url), transport=dc.transport("cs"), backoff_s=0)
    return kms, cs


def govern(client: Any, profile: Profile, actions: list[dict[str, Any]]) -> None:
    signer = profile.signer("dc-member-1")
    proposal = client.gov_propose(signer, actions)
    assert client.gov_vote(signer, proposal["proposal_id"], "approve")["state"] == "accepted"


def build_world(tmp_path: Path, *, behaviour: str = "ok", output_mode: str = "tdc", propose: bool = True) -> World:
    dc = MockDC()
    profile = Profile(home=tmp_path / "home")
    scenario = tmp_path / "mini"
    shutil.copytree(FIXTURES / "mini", scenario)
    recipe = scenario / "recipe.yaml"
    recipe.write_text(recipe.read_text().replace("name: mini-ok", f"name: mini-{behaviour}"))
    storage = tmp_path / "storage"
    storage.mkdir()
    env = {
        "MINI_STORAGE_URI": storage.as_uri() + "/",
        "DEPA_KMS_URL": dc.kms.base_url,
        "MINI_OUTPUT_MODE": output_mode,
        "MINI_RECIPIENTS": '["party:tdc#h1"]' if output_mode == "tdc" else "[]",
    }
    kms, cs = mock_clients(dc)
    member = generate_party(profile, party_id="dc-member-1", name="Member", roles=["dc-member"])
    for client in (kms, cs):
        client.gov_bootstrap([member], {"default": "majority"})
    for client, peer in ((kms, cs), (cs, kms)):
        info = peer.info()
        govern(
            client,
            profile,
            [
                {"name": "set_settings", "args": {"allow_virtual_attestation": True}},
                {
                    "name": "set_peer_service",
                    "args": {"name": info.service, "url": peer.base_url, "jwks": list(info.jwks)},
                },
                {"name": "approve_ccr_release", "args": virtual_release()},
            ],
        )
    for pid, roles in (("bank-a", ["tdp"]), ("bureau", ["tdp"]), ("tdc", ["tdc"])):
        record = generate_party(profile, party_id=pid, name=pid, roles=roles)
        for client in (kms, cs):
            govern(client, profile, [{"name": "set_party", "args": record}])
    world = World(dc, profile, scenario, storage, env, kms, cs)
    for template_path in sorted((scenario / "datasets").glob("*.yaml")):
        t = load_dataset_template(template_path, env)
        owner = t.dataset_id.split("/")[0]
        signer = profile.signer(owner)
        kms.create_dataset(signer, t.dataset_id, "fixture")
        assert t.policy is not None
        kms.set_policy(signer, t.dataset_id, t.policy)
        out = tmp_path / "enc" / t.dataset_id
        manifest = encrypt_dataset(t, kms=kms, signer=signer, out_dir=out, segment_size=4096)
        upload_directory(party_connector(t.descriptor["location"]), out, manifest)
        publish_dataset(cs, signer, t, out)
    if propose:
        contract = build_contract(scenario / "contract.yaml", env=env, resolve_manifest=catalog_resolver(cs))
        cs.propose_contract(profile.signer("tdc"), contract)
        for pid in ("tdc", "bank-a", "bureau"):
            cs.sign_contract(
                profile.signer(pid), contract["contract_id"], dc.contracts[contract["contract_id"]]["contract_digest"]
            )
        world.contract = contract
    return world


@pytest.fixture
def world(tmp_path: Path) -> Iterator[World]:
    yield build_world(tmp_path)
