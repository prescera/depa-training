"""Full DEPA Training flows through the SDK, the `depa` CLI and the `depa-ccr` agent subprocess.

Each test runs against the HTTPS mock DC and, when available, the real KMS + CS sandboxes.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import Stack, copy_fixture, trainer_cmd
from depa.contract.authoring import build_contract
from depa.dev.scenario import run_scenario
from depa.workflows import catalog_resolver

STACKS = ["mock_stack", "sandbox_stack"]


def depa(
    stack: Stack, *args: str, party: str | None = None, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess:
    argv = [sys.executable, "-m", "depa.cli.main", "--home", str(stack.home), "--json"]
    if party:
        argv += ["--party", party]
    return subprocess.run(
        [*argv, *args], capture_output=True, text=True, timeout=600, check=False, env={**os.environ, **(env or {})}
    )


@pytest.fixture(params=STACKS)
def stack(request: pytest.FixtureRequest) -> Stack:
    return request.getfixturevalue(request.param)


def _ccr_events(stderr: str) -> list[dict]:
    return [json.loads(line) for line in stderr.splitlines() if line.startswith("{")]


def test_scenario_cli_receipts_and_models(stack: Stack, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scenario, env = copy_fixture(tmp_path)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    result = run_scenario(
        scenario, home=stack.home, trainer_cmd=trainer_cmd(), work_root=tmp_path / "work", log=lambda m: None
    )
    assert result["metrics"] == {"accuracy": 0.9, "loss": 0.1}
    model = json.loads(Path(result["outputs"]["model"][0]).read_text())
    assert model["learner"]["rows"] == 500
    run_id, contract_id = result["run_id"], result["contract_id"]

    r = depa(stack, "run", "verify-receipt", run_id, party="tdc")
    assert r.returncode == 0, r.stdout + r.stderr
    assert json.loads(r.stdout)["ok"] is True

    # A second run through `depa job submit --platform local`, then ledger inclusion of its receipt.
    r = depa(
        stack,
        "job",
        "submit",
        "--platform",
        "local",
        "--contract-id",
        contract_id,
        "--dev-trainer-cmd",
        trainer_cmd(),
        party="tdc",
        env=env,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    log = json.loads(r.stdout)["log"]
    events = [json.loads(line) for line in log]
    run2 = next(e["run_id"] for e in events if e["event"] == "run.authorized")
    txid = next(e["txid"] for e in events if e["event"] == "event.posted" and e["type"] == "completed")
    args = ["run", "verify-receipt", run2] + (["--txid", txid] if stack.kind == "sandbox" else [])
    r = depa(stack, *args, party="tdc")
    assert r.returncode == 0, r.stdout + r.stderr
    if stack.kind == "sandbox":
        assert json.loads(r.stdout)["ledger_verified"] is True

    fetched, plain = tmp_path / "fetched", tmp_path / "plain"
    assert depa(stack, "model", "fetch", "--run", run2, "--out", str(fetched), party="tdc").returncode == 0
    r = depa(stack, "model", "decrypt", str(fetched), "--run", run2, "--out", str(plain), party="tdc")
    assert r.returncode == 0, r.stdout + r.stderr
    r = depa(stack, "model", "decrypt", str(fetched), "--run", run2, "--out", str(tmp_path / "x"), party="bank-a")
    assert r.returncode == 3  # not a recipient

    # Contract inspection and verification from a TDP's point of view.
    r = depa(stack, "contract", "verify", contract_id, party="bureau")
    assert r.returncode == 0, r.stdout
    assert json.loads(r.stdout)["ok"] is True
    status = json.loads(depa(stack, "contract", "status", contract_id, party="tdc").stdout)
    assert status["state"] == "active" and status["runs_used"] == 2


def test_kill_switches_and_tampering(stack: Stack, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Owner suspension, contract revocation and storage tampering all stop runs (fail closed)."""
    scenario, env = copy_fixture(tmp_path)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    # Publishes (or reuses) the datasets and an active contract.
    first = run_scenario(
        scenario, home=stack.home, trainer_cmd=trainer_cmd(), work_root=tmp_path / "w1", log=lambda m: None
    )
    profile = stack.profile
    kms, cs = profile.kms_client(), profile.cs_client()

    def new_contract() -> str:
        contract = build_contract(
            scenario / "contract.yaml",
            env={**os.environ, "DEPA_KMS_URL": kms.base_url},
            resolve_manifest=catalog_resolver(cs),
        )
        cs.propose_contract(profile.signer("tdc"), contract)
        digest = cs.get_contract(contract["contract_id"])["contract_digest"]
        for party in ("tdc", "bank-a", "bureau"):
            cs.sign_contract(profile.signer(party), contract["contract_id"], digest)
        return str(contract["contract_id"])

    def submit(contract_id: str) -> tuple[int, list[dict]]:
        r = depa(
            stack,
            "job",
            "submit",
            "--platform",
            "local",
            "--contract-id",
            contract_id,
            "--dev-trainer-cmd",
            trainer_cmd(),
            party="tdc",
            env=env,
        )
        out = json.loads(r.stdout) if r.stdout.startswith("{") else {}
        return r.returncode, [json.loads(line) for line in out.get("log", []) if line.startswith("{")]

    # 1. Owner suspends a dataset: key release is refused, the run reports `failed`.
    cid = new_contract()
    kms.suspend_dataset(profile.signer("bank-a"), "bank-a/loans", "e2e")
    try:
        rc, events = submit(cid)
        assert rc != 0
        failed = next(e for e in events if e["event"] == "agent.failed")
        assert failed["stage"] == "keys"
        run_id = next(e["run_id"] for e in events if e["event"] == "run.authorized")
        assert cs.get_run(run_id)["state"] == "failed"
    finally:
        kms.resume_dataset(profile.signer("bank-a"), "bank-a/loans", "e2e")

    # 2. Revocation by a TDP: the job is refused before any CCR starts.
    r = depa(stack, "contract", "revoke", cid, "--reason", "e2e", party="bureau")
    assert r.returncode == 0, r.stdout + r.stderr
    rc, _ = submit(cid)
    assert rc == 3

    # 3. Substituted trust anchors (an extra CS key in the bundle): the services refuse the binding.

    from depa.core.encoding import b64url_encode, canonicalize
    from depa.core.keys import EcSigner
    from depa.deploy.local import run_local
    from depa.deploy.spec import JobSpec
    from depa.workflows import trust_bundle_document

    cid = new_contract()
    doc = trust_bundle_document(profile, kms, cs)
    doc["cs"]["jwks"] = [*doc["cs"]["jwks"], EcSigner.generate("attacker").public_jwk()]
    contract = cs.get_contract(cid)["contract"]
    spec = JobSpec(
        contract_id=cid,
        recipe_digest=contract["computation"]["recipes"][0]["digest"],
        cs_url=cs.base_url,
        kms_url=kms.base_url,
        platform="virtual",
        max_duration_minutes=30,
        trust_bundle=b64url_encode(canonicalize(doc)),
    )
    job = run_local(
        spec, image=None, env_extra={"DEPA_WORK_DIR": str(tmp_path / "tb"), "DEPA_TRAINER_CMD": trainer_cmd()}
    )
    assert job.returncode != 0
    events = [json.loads(line) for line in job.stderr.decode().splitlines() if line.startswith("{")]
    failed = next(e for e in events if e["event"] == "agent.failed")
    assert failed["stage"] == "authorize" and not any(e["event"] == "run.authorized" for e in events)

    # 4. Tampered ciphertext in the TDP's storage: decryption fails closed.
    cid = new_contract()
    location = cs.get_dataset_descriptor("bank-a/loans", "2026-11-01")["location"]["uri"]
    obj = next((Path(location.removeprefix("file://")) / "objects").rglob("*.dedf"))
    original = obj.read_bytes()
    obj.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
    try:
        rc, events = submit(cid)
        assert rc != 0
        assert next(e for e in events if e["event"] == "agent.failed")["code"] in ("dedf_tag", "dedf_hash")
    finally:
        obj.write_bytes(original)
    assert first["run_id"]
