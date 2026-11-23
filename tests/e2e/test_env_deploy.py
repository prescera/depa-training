"""`depa env validate`, `depa dc setup --env` and `depa scenario deploy` (idempotent) end to end."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from conftest import Stack, copy_fixture, trainer_cmd
from depa.dev.stack import MEMBER_ID, virtual_release
from depa.profile import Profile, generate_party


@pytest.fixture(params=["mock_stack", "sandbox_stack"])
def stack(request: pytest.FixtureRequest) -> Stack:
    return request.getfixturevalue(request.param)


def _cli(home: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "depa.cli.main", "--home", str(home), "--json", *args],
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
        env=dict(os.environ),
    )


def _write_env(stack: Stack, tmp: Path, suffix: str) -> Path:
    """An environment file as deploy/scripts would generate it from Terraform outputs."""
    keys = tmp / "keys"
    parties = []
    ids = {"tdp-a": "bank-a", "tdp-b": "bureau", "tdc": "tdc"}
    for role, pid in (("tdp", ids["tdp-a"]), ("tdp", ids["tdp-b"]), ("tdc", ids["tdc"])):
        key_dir = keys / pid
        if not (key_dir / "party.json").exists():
            generate_party(Profile(home=tmp, key_dirs={pid: str(key_dir)}), party_id=pid, name=pid, roles=[role])
        parties.append({"party_id": pid, "roles": [role], "key_dir": f"keys/{pid}"})
    parties.append({"party_id": MEMBER_ID, "roles": ["dc-member"], "key_dir": str(stack.profile.keys_dir(MEMBER_ID))})
    releases = tmp / "releases"
    releases.mkdir(exist_ok=True)
    (releases / "depa-ccr-virtual-dev.json").write_text(
        json.dumps(
            {
                "release": virtual_release(),
                "deployment": {
                    "image": "local/depa-ccr-classic@sha256:" + "0" * 64,
                    "resources": {"cpu": 1, "memory_gb": 2},
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
    storage = (tmp / f"storage-{suffix}").as_uri() + "/"
    profile = stack.profile
    env = {
        "collaborative": {
            "kms": {"url": profile.kms.url, "service_cert": profile.kms.cert, "oidc_issuer": f"{profile.kms.url}/oidc"},
            "cs": {"url": profile.cs.url, "service_cert": Path(str(profile.cs.cert)).read_text()},  # inline PEM
        },
        "parties": parties,
        "storage": {
            "bank-a": {"connector": "file", "uri": storage + "bank-a/"},
            "bureau": {"connector": "file", "uri": storage + "bureau/"},
        },
        "outputs": {"tdc": {"connector": "file", "uri": storage + "outputs/"}},
        "releases_dir": "releases",
    }
    path = tmp / "depa-env.yaml"
    path.write_text(yaml.safe_dump(env, sort_keys=False))
    return path


def test_env_setup_and_deploy(stack: Stack, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scenario, fixture_env = copy_fixture(tmp_path)
    for key, value in fixture_env.items():
        monkeypatch.setenv(key, value)  # template placeholder; the driver overrides locations from the env file
    # A fresh dataset version per stack/test so publication really happens on the shared stack.
    version = f"2026-11-{'02' if stack.kind == 'mock' else '03'}"
    for f in [*(scenario / "datasets").glob("*.yaml"), scenario / "contract.yaml"]:
        f.write_text(f.read_text().replace('"2026-11-01"', f'"{version}"'))
    env_file = _write_env(stack, tmp_path, stack.kind)
    home = tmp_path / "home"

    r = _cli(home, "env", "validate", str(env_file))
    assert r.returncode == 0, r.stdout
    bad = tmp_path / "bad.yaml"
    bad.write_text(env_file.read_text().replace("connector: file", "connector: ftp", 1))
    assert _cli(home, "env", "validate", str(bad)).returncode == 3

    r = _cli(home, "dc", "setup", "--env", str(env_file))
    assert r.returncode == 0, r.stdout + r.stderr
    setup = json.loads(r.stdout)
    assert all(p["state"] == "accepted" for which in ("kms", "cs") for p in setup["proposals"][which])

    args = [
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
    ]
    r = _cli(home, *args)
    assert r.returncode == 0, r.stdout + r.stderr
    summary = json.loads(r.stdout)
    assert summary["status"] == "succeeded"
    steps = [s["step"] for s in summary["steps"]]
    assert steps.count("dataset.publish") == 2 and "contract.propose" in steps and "model.decrypt" in steps
    receipt = json.loads(Path(summary["result"]["receipt_file"]).read_text())
    assert Path(summary["result"]["receipt_file"]).name.startswith("receipt-")
    assert receipt["receipt_jws"].count(".") == 2
    model = Path(summary["result"]["outputs"]["model"][0])
    assert "receipt" not in model.name
    assert json.loads(model.read_text())["learner"]["rows"] == 500

    # Idempotent re-run: nothing re-published, the active contract is reused, one more run.
    r = _cli(home, *args)
    assert r.returncode == 0, r.stdout + r.stderr
    again = json.loads(r.stdout)
    steps = [s["step"] for s in again["steps"]]
    assert "dataset.publish" not in steps and "contract.reuse" in steps
    assert again["contract_id"] == summary["contract_id"] and again["result"]["run_id"] != summary["result"]["run_id"]

    # Acting only for one TDP: no contract or job steps.
    r = _cli(home, *args[:-4], "--parties", "bureau", "--no-run")
    assert r.returncode == 0, r.stdout + r.stderr
    assert json.loads(r.stdout)["status"] == "datasets-published"


def _running_trainers() -> list[int]:
    """PIDs whose argv is exactly a fake-trainer invocation (``... fake_trainer.py run --recipe ...``)."""
    pids = []
    for proc in Path("/proc").glob("[0-9]*"):
        try:
            argv = (proc / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        if any(a.endswith(b"fake_trainer.py") for a in argv) and b"--recipe" in argv:
            pids.append(int(proc.name))
    return pids


def _scenario_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, version: str, behaviour: str = "ok") -> Path:
    scenario, fixture_env = copy_fixture(tmp_path)
    for key, value in fixture_env.items():
        monkeypatch.setenv(key, value)
    for f in [*(scenario / "datasets").glob("*.yaml"), scenario / "contract.yaml"]:
        f.write_text(f.read_text().replace('"2026-11-01"', f'"{version}"'))
    recipe = scenario / "recipe.yaml"
    recipe.write_text(recipe.read_text().replace("name: mini-ok", f"name: mini-{behaviour}"))
    return scenario


def test_deploy_from_dev_profile_without_env(stack: Stack, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`scenario deploy --platform local` with no env file uses the `depa dev up` profile."""
    scenario = _scenario_copy(tmp_path, monkeypatch, "2026-11-04" if stack.kind == "mock" else "2026-11-05")
    r = _cli(
        stack.home,
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
    assert r.returncode == 0, r.stdout + r.stderr
    summary = json.loads(r.stdout)
    assert summary["status"] == "succeeded" and summary["result"]["metrics"] == {"accuracy": 0.9, "loss": 0.1}
    assert (stack.home / "dev-env.yaml").exists()
    assert _cli(stack.home, "scenario", "deploy", str(scenario), "--platform", "aks").returncode == 2


def test_cancel_stops_a_running_local_job(stack: Stack, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """DeployOptions.cancel terminates the CCR (which kills its trainer and reports `failed`)."""
    import threading
    import time

    from depa.deployment import DeployOptions, dc_setup, deploy_scenario, environment_from_profile
    from depa.errors import DeployCancelled

    scenario = _scenario_copy(tmp_path, monkeypatch, "2026-11-06" if stack.kind == "mock" else "2026-11-07", "sleep")
    profile = Profile.load(stack.home)
    env = environment_from_profile(profile, scenarios=[scenario])
    dc_setup(env, env.profile(stack.home), log=lambda m: None)
    cancel = threading.Event()

    def log(message: str) -> None:  # cancel a few seconds after the clean room starts training
        if message.startswith("contract.sign") or message.startswith("contract.reuse"):
            threading.Timer(4.0, cancel.set).start()

    opts = DeployOptions(platform="local", trainer_cmd=trainer_cmd(), work_dir=tmp_path / "work", cancel=cancel)
    start = time.monotonic()
    with pytest.raises(DeployCancelled):
        deploy_scenario(scenario, env, env.profile(stack.home), opts, log=log)
    assert time.monotonic() - start < 90
    cs = env.profile(stack.home).cs_client()
    contracts = cs.list_contracts("tdc")
    items = contracts.get("contracts", contracts) if isinstance(contracts, dict) else contracts
    runs = [r for c in items for r in cs.list_runs(str(c.get("contract_id") or c["contract"]["contract_id"]))]
    assert any(r.get("state") == "failed" for r in runs)  # the agent reported the termination
    assert _running_trainers() == []  # no orphaned trainer
