"""Fixtures: the SDK's mock Data Collaborative over real HTTPS, an environment file for four parties,
one completed scenario run (local virtual clean room), and signed-in console clients per party."""

from __future__ import annotations

import json
import re
import shutil
import sys
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parents[2]
SDK_TESTS = REPO / "sdk" / "python" / "tests"
sys.path.insert(0, str(SDK_TESTS))

from depa.deployment import DeployOptions, dc_setup, deploy_scenario
from depa.dev import stack as dev_stack
from depa.dev.stack import MEMBER_ID, virtual_release
from depa.env import load_env
from depa.profile import Profile, generate_party
from mockdc import MockDC

from depa_console.app import create_app
from depa_console.security import Auth
from depa_console.state import Console, Settings

FIXTURE = SDK_TESTS / "fixtures" / "mini"
TRAINER = f"{sys.executable} {SDK_TESTS / 'fixtures' / 'fake_trainer.py'}"
TOKEN = "test-launch-token"
CSRF_RE = re.compile(r'name="csrf_token" value="([0-9a-f]{64})"')


@dataclass
class Stack:
    dc: MockDC
    tmp: Path
    home: Path
    env_file: Path
    scenarios: Path
    contract_id: str
    run_id: str

    def profile(self, party: str) -> Profile:
        return load_env(self.env_file).profile(self.home, party)


def _write_env(tmp: Path, profile: Profile) -> Path:
    parties = []
    for pid, role in (("bank-a", "tdp"), ("bureau", "tdp"), ("tdc", "tdc")):
        key_dir = tmp / "keys" / pid
        generate_party(Profile(home=tmp, key_dirs={pid: str(key_dir)}), party_id=pid, name=pid.title(), roles=[role])
        parties.append({"party_id": pid, "roles": [role], "key_dir": f"keys/{pid}"})
    parties.append({"party_id": MEMBER_ID, "roles": ["dc-member"], "key_dir": str(profile.keys_dir(MEMBER_ID))})
    releases = tmp / "releases"
    releases.mkdir()
    manifest = {
        "release": virtual_release(),
        "deployment": {
            "image": "local/depa-ccr-classic@sha256:" + "0" * 64,
            "resources": {"cpu": 1, "memory_gb": 2},
            "env_allowlist": ["DEPA_CONTRACT_ID", "DEPA_RECIPE_DIGEST", "DEPA_CS_URL", "DEPA_KMS_URL",
                              "DEPA_PLATFORM", "DEPA_TRUST_BUNDLE", "DEPA_LOG_LEVEL"],
        },
    }  # fmt: skip
    (releases / "depa-ccr-virtual-dev.json").write_text(json.dumps(manifest))
    storage = (tmp / "storage").as_uri() + "/"
    env = {
        "collaborative": {
            "kms": {"url": profile.kms.url, "service_cert": profile.kms.cert},
            "cs": {"url": profile.cs.url, "service_cert": profile.cs.cert},
        },
        "parties": parties,
        "storage": {"bank-a": {"connector": "file", "uri": storage + "bank-a/"},
                    "bureau": {"connector": "file", "uri": storage + "bureau/"}},
        "outputs": {"tdc": {"connector": "file", "uri": storage + "outputs/"}},
        "releases_dir": "releases",
    }  # fmt: skip
    path = tmp / "depa-env.yaml"
    path.write_text(yaml.safe_dump(env, sort_keys=False))
    return path


@pytest.fixture(scope="session")
def stack(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Stack]:
    tmp = tmp_path_factory.mktemp("console")
    patch = pytest.MonkeyPatch()
    patch.setenv("MINI_STORAGE_URI", (tmp / "storage").as_uri() + "/")
    dc = MockDC()
    kms_url, cs_url, cert = dc.serve()
    home = tmp / "home"
    profile = Profile(home=home).with_overrides(kms_url=kms_url, kms_cert=str(cert), cs_url=cs_url, cs_cert=str(cert))
    profile.save()
    dev_stack.configure_dev_governance(
        profile, [profile.kms_client(), profile.cs_client()], profile.kms_client(), profile.cs_client()
    )
    env_file = _write_env(tmp, profile)
    scenarios = tmp / "scenarios"
    shutil.copytree(FIXTURE, scenarios / "mini")
    env = load_env(env_file)
    dc_setup(env, env.profile(home), log=lambda _: None)
    summary = deploy_scenario(
        scenarios / "mini",
        env,
        env.profile(home),
        DeployOptions(platform="local", trainer_cmd=TRAINER, work_dir=tmp / "seed-work"),
        log=lambda _: None,
    )
    assert summary["status"] == "succeeded", summary
    yield Stack(dc, tmp, home, env_file, scenarios, summary["contract_id"], summary["result"]["run_id"])
    patch.undo()
    dc.stop()


def make_app(stack: Stack, party: str) -> tuple[Any, Console]:
    settings = Settings(
        home=stack.home,
        env_path=stack.env_file,
        party=party,
        scenarios_root=stack.scenarios,
        work_root=stack.tmp / "console-work",
        dev_trainer_cmd=TRAINER,
    )
    console = Console(settings)
    return create_app(console, Auth(token=TOKEN), allowed_hosts=("testserver",)), console


@pytest.fixture
def client_for(stack: Stack) -> Iterator[Callable[[str], TestClient]]:
    clients: list[TestClient] = []

    def make(party: str) -> TestClient:
        app, _ = make_app(stack, party)
        client = TestClient(app)
        client.__enter__()
        clients.append(client)
        response = client.get(f"/login?token={TOKEN}", follow_redirects=False)
        assert response.status_code == 303
        return client

    yield make
    for client in clients:
        client.__exit__(None, None, None)


def csrf(client: TestClient, path: str = "/") -> str:
    match = CSRF_RE.search(client.get(path).text)
    assert match, "no CSRF token on the page"
    return match.group(1)


def post(client: TestClient, path: str, data: dict[str, Any], *, token: str | None = None) -> Any:
    return client.post(path, data={"csrf_token": token or csrf(client), **data}, follow_redirects=False)


def wait_task(client: TestClient, location: str, timeout: float = 120) -> dict[str, Any]:
    assert location.startswith("/tasks/"), location
    deadline = time.monotonic() + timeout
    while True:
        status = client.get(f"{location}/status").json()
        if status["state"] in ("succeeded", "failed", "cancelled"):
            return status
        assert time.monotonic() < deadline, "task did not finish"
        time.sleep(0.1)
