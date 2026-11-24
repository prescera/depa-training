"""`depa scenario teardown`: revoke + delete outputs only for one scenario; a re-run then works."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from conftest import Stack, copy_fixture, trainer_cmd


@pytest.fixture(params=["mock_stack", "sandbox_stack"])
def stack(request: pytest.FixtureRequest) -> Stack:
    return request.getfixturevalue(request.param)


def test_teardown_then_rerun(stack: Stack, tmp_path: Path) -> None:
    scenario, fixture_env = copy_fixture(tmp_path)
    tag = "tdm" if stack.kind == "mock" else "tds"
    for f in [*(scenario / "datasets").glob("*.yaml"), scenario / "contract.yaml", scenario / "recipe.yaml"]:
        text = (
            f.read_text()
            .replace("bank-a/loans", f"bank-a/loans-{tag}")
            .replace("bureau/scores", f"bureau/scores-{tag}")
        )
        f.write_text(text)
    work = tmp_path / "work"
    env = {**os.environ, **fixture_env}

    def cli(*args: str) -> tuple[int, dict]:
        argv = [sys.executable, "-m", "depa.cli.main", "--home", str(stack.home), "--json", *args]
        r = subprocess.run(argv, capture_output=True, text=True, timeout=600, check=False, env=env)
        return r.returncode, json.loads(r.stdout) if r.stdout.startswith("{") else {"stderr": r.stderr}

    deploy = [
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
    rc, first = cli(*deploy)
    assert rc == 0 and first["status"] == "succeeded", first
    env_file = stack.home / "dev-env.yaml"  # written by the profile-mode deploy

    rc, out = cli("scenario", "check", str(scenario), "--env", str(env_file), "--platform", "local")
    assert rc == 0 and out["ok"]
    rc, out = cli("scenario", "check", str(scenario), "--env", str(env_file), "--platform", "aks")
    assert rc == 2 and "compute.aks" in out["message"]

    rc, down = cli("scenario", "teardown", str(scenario), "--env", str(env_file), "--work-dir", str(work))
    assert rc == 0, down
    assert down["revoked"] == [first["contract_id"]]
    assert down["deleted_outputs"] and down["deleted_outputs"][0]["objects"] >= 3
    outputs_uri = yaml.safe_load(env_file.read_text())["outputs"]["tdc"]["uri"]
    contract_outputs = Path(outputs_uri.removeprefix("file://")) / first["contract_id"]
    assert contract_outputs.parent.exists() and not list(contract_outputs.rglob("*.dedf"))
    assert not list(contract_outputs.rglob("manifest.json"))
    record = json.loads(
        subprocess.run(
            [
                sys.executable,
                "-m",
                "depa.cli.main",
                "--home",
                str(stack.home),
                "--json",
                "--env",
                str(env_file),
                "--party",
                "tdc",
                "contract",
                "status",
                first["contract_id"],
            ],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        ).stdout
    )
    assert record["state"] == "revoked"

    rc, again = cli("scenario", "teardown", str(scenario), "--env", str(env_file), "--work-dir", str(work))
    assert rc == 0 and again["revoked"] == [] and again["deleted_outputs"] == []
    assert {c["state"] for c in again["inactive"]} == {"revoked"}

    rc, second = cli(*deploy)
    assert rc == 0 and second["status"] == "succeeded", second
    assert second["contract_id"] != first["contract_id"]
    steps = [s["step"] for s in second["steps"]]
    assert "contract.propose" in steps and "contract.reuse" not in steps and "dataset.publish" not in steps
