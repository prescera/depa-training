"""`depa scenario deploy --stage ...`: one party action per invocation, resumable from --work-dir."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import Stack, copy_fixture, trainer_cmd


@pytest.fixture(params=["mock_stack", "sandbox_stack"])
def stack(request: pytest.FixtureRequest) -> Stack:
    return request.getfixturevalue(request.param)


def test_stages_one_at_a_time_with_resume(stack: Stack, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scenario, fixture_env = copy_fixture(tmp_path)
    version = "2026-11-08" if stack.kind == "mock" else "2026-11-09"
    tag = "stm" if stack.kind == "mock" else "sts"  # fresh dataset ids: no KMS keys exist yet
    for f in [*(scenario / "datasets").glob("*.yaml"), scenario / "contract.yaml", scenario / "recipe.yaml"]:
        text = f.read_text().replace('"2026-11-01"', f'"{version}"')
        text = text.replace("bank-a/loans", f"bank-a/loans-{tag}").replace("bureau/scores", f"bureau/scores-{tag}")
        f.write_text(text)
    work = tmp_path / "work"
    env = {**os.environ, **fixture_env}

    def deploy(*stages: str) -> tuple[int, dict]:
        argv = [
            sys.executable,
            "-m",
            "depa.cli.main",
            "--home",
            str(stack.home),
            "--json",
            "scenario",
            "deploy",
            str(scenario),
            "--platform",
            "local",
            "--dev-trainer-cmd",
            trainer_cmd(),
            "--work-dir",
            str(work),
        ]
        for s in stages:
            argv += ["--stage", s]
        r = subprocess.run(argv, capture_output=True, text=True, timeout=600, check=False, env=env)
        return r.returncode, json.loads(r.stdout) if r.stdout.startswith("{") else {"stderr": r.stderr}

    # Prerequisites are enforced (exit 2) before anything runs out of order.
    rc, out = deploy("data")
    assert rc == 2 and "--stage keys" in out["message"]
    rc, out = deploy("contract")
    assert rc == 2 and "--stage data" in out["message"]
    rc, out = deploy("train")
    assert rc == 2

    rc, out = deploy("keys")
    assert rc == 0 and set(out["stages"]) == {"keys"}
    assert sum(s["step"] == "dataset.create-key" for s in out["steps"]) == 2
    rc, out = deploy("data")
    assert rc == 0 and sum(s["step"] == "dataset.publish" for s in out["steps"]) == 2
    assert out["stages"]["data"]["published"] == {f"bank-a/loans-{tag}": version, f"bureau/scores-{tag}": version}
    rc, out = deploy("model")
    assert rc == 2 and "--stage contract" in out["message"]
    rc, out = deploy("contract")
    assert rc == 0 and out["stages"]["contract"]["state"] == "active"
    contract_id = out["contract_id"]
    rc, out = deploy("model")
    assert rc == 2 and "--stage train" in out["message"]
    rc, out = deploy("train")
    assert rc == 0 and out["stages"]["train"]["state"] == "completed" and out["status"] == "stages-completed"
    run_id = out["stages"]["train"]["run_id"]
    state = json.loads((work / "state.json").read_text())
    assert state["contract_id"] == contract_id and state["run_id"] == run_id and "party_map" in state

    # Resume without the state file: the contract and the latest completed run come from the services.
    (work / "state.json").unlink()
    rc, out = deploy("model")
    assert rc == 0 and out["status"] == "succeeded", out
    assert out["stages"]["model"]["run_id"] == run_id and out["contract_id"] == contract_id
    assert json.loads(Path(out["result"]["outputs"]["model"][0]).read_text())["learner"]["rows"] == 500

    # Idempotent: keys and data again change nothing; comma lists work.
    rc, out = deploy("keys,data")
    assert rc == 0 and not [s for s in out["steps"] if s["step"].startswith("dataset.")]
    rc, out = deploy("bogus")
    assert rc == 2
