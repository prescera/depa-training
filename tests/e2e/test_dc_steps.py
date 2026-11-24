"""`depa dc setup --step ...` on FRESH services: four separate processes, then a scenario.

Variants: a new HTTPS mock DC, and new real KMS + CS sandboxes on their own ports
(``DEPA_E2E_STEPS_PORTS``, default 18162,18163), torn down afterwards.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

from conftest import _sandbox_available, copy_fixture, trainer_cmd
from depa.dev import stack as dev_stack
from depa.dev.stack import virtual_release
from depa.profile import Profile, generate_party
from mockdc import MockDC


@dataclass
class Fresh:
    kms_url: str
    cs_url: str
    kms_cert: str
    cs_cert: str
    kms_operator: list[str]  # CLI args authenticating the operator for bootstrap-kms
    cs_operator: list[str]
    env: dict[str, str]


@pytest.fixture(params=["mock", "sandbox"])
def fresh(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Fresh]:
    if request.param == "mock":
        dc = MockDC()
        kms_url, cs_url, cert = dc.serve()
        yield Fresh(kms_url, cs_url, str(cert), str(cert), [], [], {"DEPA_OPERATOR_TOKEN": "mock-operator"})
        dc.stop()
        return
    if not _sandbox_available():
        pytest.skip("KMS/CS sandboxes not available")
    kms_port, cs_port = (int(p) for p in os.environ.get("DEPA_E2E_STEPS_PORTS", "18162,18163").split(","))
    repos = {"kms": dev_stack.default_repo("kms"), "cs": dev_stack.default_repo("cs")}
    dirs = {k: tmp_path / "sandboxes" / k for k in repos}
    try:
        k = dev_stack.start_sandbox(repos["kms"], kms_port, state_dir=dirs["kms"])
        c = dev_stack.start_sandbox(repos["cs"], cs_port, state_dir=dirs["cs"])
        yield Fresh(
            k.app_url,
            c.app_url,
            k.service_cert,
            c.service_cert,
            ["--client-cert", k.user_cert, "--client-key", k.user_key],
            ["--client-cert", c.user_cert, "--client-key", c.user_key],
            {},
        )
    finally:
        dev_stack.stop_sandbox(repos["kms"], kms_port, state_dir=dirs["kms"], purge=True)
        dev_stack.stop_sandbox(repos["cs"], cs_port, state_dir=dirs["cs"], purge=True)


def _env_file(fresh: Fresh, tmp: Path) -> Path:
    parties = []
    for pid, roles in [
        ("dc-member-1", ["dc-member"]),
        ("tdc", ["tdc", "tdp"]),
        ("bank-a", ["tdp"]),
        ("bureau", ["tdp"]),
    ]:
        key_dir = tmp / "keys" / pid
        generate_party(Profile(home=tmp, key_dirs={pid: str(key_dir)}), party_id=pid, name=pid, roles=roles)
        parties.append({"party_id": pid, "roles": roles, "key_dir": f"keys/{pid}"})
    (tmp / "releases").mkdir()
    (tmp / "releases" / "depa-ccr-virtual-dev.json").write_text(
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
            "kms": {"url": fresh.kms_url, "service_cert": fresh.kms_cert, "oidc_issuer": fresh.kms_url + "/oidc"},
            "cs": {"url": fresh.cs_url, "service_cert": fresh.cs_cert},
        },
        "parties": parties,
        "storage": {p: {"connector": "file", "uri": f"{storage}{p}/"} for p in ("tdc", "bank-a", "bureau")},
        "outputs": {"tdc": {"connector": "file", "uri": f"{storage}outputs/"}},
        "releases_dir": "releases",
    }
    path = tmp / "depa-env.yaml"
    path.write_text(yaml.safe_dump(doc, sort_keys=False))
    return path


def test_dc_setup_steps_as_separate_processes(fresh: Fresh, tmp_path: Path) -> None:
    env_file = _env_file(fresh, tmp_path)
    scenario, fixture_env = copy_fixture(tmp_path)

    def run(*args: str) -> tuple[int, dict]:
        argv = [sys.executable, "-m", "depa.cli.main", "--home", str(tmp_path / "home"), "--json", *args]
        r = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
            env={**os.environ, **fresh.env, **fixture_env},
        )
        return r.returncode, json.loads(r.stdout) if r.stdout.startswith("{") else {"stderr": r.stderr}

    def setup(step: str, *extra: str) -> tuple[int, dict]:
        return run("dc", "setup", "--env", str(env_file), "--step", step, *extra)

    rc, out = setup("link")
    assert rc == 2 and "bootstrap-kms" in out["message"]
    rc, out = setup("bootstrap-kms", "--allow-virtual-attestation", *fresh.kms_operator)
    assert rc == 0 and out["bootstrapped"] == ["kms"]
    rc, out = setup("register")
    assert rc == 2 and "bootstrap-cs" in out["message"]
    rc, out = setup("bootstrap-cs", "--allow-virtual-attestation", *fresh.cs_operator)
    assert rc == 0 and out["bootstrapped"] == ["cs"]
    rc, out = setup("bootstrap-cs", *fresh.cs_operator)
    assert rc == 0 and out["bootstrapped"] == [] and "cs: already bootstrapped" in out["skipped"]

    rc, out = setup("link")
    assert rc == 0, out
    names = [a for which in ("kms", "cs") for p in out["proposals"][which] for a in p["actions"]]
    assert {"set_peer_service", "set_settings", "set_oidc_settings"} <= set(names)
    assert all(p["state"] == "accepted" for which in ("kms", "cs") for p in out["proposals"][which])
    rc, out = setup("link")
    assert rc == 0 and out["proposals"] == {"kms": [], "cs": []}  # idempotent

    rc, out = setup("register")
    assert rc == 0
    names = [a for which in ("kms", "cs") for p in out["proposals"][which] for a in p["actions"]]
    assert names.count("set_party") == 6 and names.count("approve_ccr_release") == 2
    rc, out = setup("register")
    assert rc == 0 and out["proposals"] == {"kms": [], "cs": []}

    rc, out = run(
        "--env",
        str(env_file),
        "scenario",
        "deploy",
        str(scenario),
        "--platform",
        "local",
        "--dev-trainer-cmd",
        trainer_cmd(),
        "--work-dir",
        str(tmp_path / "work"),
    )
    assert rc == 0 and out["status"] == "succeeded", out
