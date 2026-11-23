"""The clean-room agent end to end against the mock KMS/CS (virtual attestation, file storage)."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

from conftest import FAKE_TRAINER, World, build_world, mock_clients
from depa.attestation.virtual import VirtualProvider
from depa.ccr.agent import Agent
from depa.ccr.config import CcrConfig
from depa.ccr.log import StatusLog
from depa.receipts import verify_execution_receipt
from depa.workflows import decrypt_output, fetch_output


def _run(
    world: World, tmp_path: Path, *, recipe_digest: str | None = None, trust_bundle=None
) -> tuple[int, list[dict], Path]:
    kms, cs = mock_clients(world.dc)
    work = tmp_path / "ccr-work"
    config = CcrConfig(
        contract_id=world.contract["contract_id"],
        recipe_digest=recipe_digest or world.contract["computation"]["recipes"][0]["digest"],
        cs_url=world.dc.cs.base_url,
        kms_url=world.dc.kms.base_url,
        platform="virtual",
        log_level="info",
        trust_bundle=trust_bundle,
        work_dir=work,
    )
    stream = io.StringIO()
    agent = Agent(
        config,
        log=StatusLog("info", stream=stream),
        provider=VirtualProvider(),
        cs=cs,
        kms=kms,
        trainer_command=[sys.executable, str(FAKE_TRAINER)],
        require_root=False,
    )
    code = agent.run()
    lines = [json.loads(line) for line in stream.getvalue().splitlines()]
    return code, lines, work


def _events(world: World, run_id: str) -> list[str]:
    return [e["type"] for e in world.dc.runs[run_id]["events"]]


def test_full_run_and_tdc_decrypt(world: World, tmp_path: Path) -> None:
    code, lines, work = _run(world, tmp_path)
    assert code == 0, [ln for ln in lines if ln["event"] == "agent.failed"]
    run_id = next(ln["run_id"] for ln in lines if ln["event"] == "run.authorized")
    assert _events(world, run_id) == ["started", "progress", "completed"]
    assert world.dc.release_count == 2
    assert list(work.iterdir()) == []  # work directory wiped

    # Logs are allowlisted structured lines only.
    allowed = {
        "ts",
        "level",
        "event",
        "contract_id",
        "run_id",
        "platform",
        "trust_bundle",
        "recipe_digest",
        "fstype",
        "dataset_id",
        "manifest_digest",
        "files",
        "bytes",
        "count",
        "seconds",
        "returncode",
        "network",
        "privdrop",
        "type",
        "seq",
        "txid",
        "output",
        "format",
    }
    assert all(set(ln) <= allowed for ln in lines)

    # TDC verifies the receipt, fetches and decrypts the model.
    kms, cs = mock_clients(world.dc)
    run = cs.get_run(run_id)
    check = verify_execution_receipt(run["receipt_jws"], cs.info().key_lookup(), kms.info().key_lookup())
    assert check.ok, check.problems
    claims = check.claims
    assert claims["metrics"] == {"accuracy": 0.9, "loss": 0.1}  # secret/leak/non-allowlisted dropped
    assert claims["privacy_spent"] == {"mode": "dp", "epsilon": 1.9, "delta": 1e-5, "accountant": "rdp"}
    assert {i["dataset_id"] for i in claims["inputs"]} == {"bank-a/loans", "bureau/scores"}
    enc = tmp_path / "fetched"
    fetch_output(claims, "model", enc, location=world.contract["output"]["location"], access_token=None)
    key = world.profile.hpke_key("tdc")
    files = decrypt_output(
        enc,
        tmp_path / "plain",
        key=key,
        recipient="party:tdc#h1",
        expected_digest=claims["outputs"][0]["manifest_digest"],
        run_key=run["run_key"] | {"kid": f"run-{run_id}"},
    )
    model = json.loads(files[0].read_text())
    assert model["learner"]["rows"] == 500  # trained on the decrypted input


def test_trainer_sees_read_only_inputs_and_scrubbed_env(world: World, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DEPA_SECRET_TOKEN", "leak-me")
    captured = {}
    import depa.ccr.agent as agent_mod

    real_gate = agent_mod.apply_gate

    def spy(result, **kwargs):
        captured.update(result)
        return real_gate(result, **kwargs)

    monkeypatch.setattr(agent_mod, "apply_gate", spy)
    code, _, _ = _run(world, tmp_path)
    assert code == 0
    assert captured["debug"] == {"inputs_writable": False, "env": []}


@pytest.mark.parametrize(
    ("behaviour", "code"),
    [
        ("fail", "trainer_failed"),
        ("overspend", "privacy_budget_exceeded"),
        ("undeclared", "undeclared_output"),
        ("badformat", "bad_output_format"),
        ("escape", "bad_output_file"),
    ],
)
def test_failures_are_reported(tmp_path: Path, behaviour: str, code: str) -> None:
    w = build_world(tmp_path, behaviour=behaviour)
    rc, lines, work = _run(w, tmp_path)
    assert rc == 1
    failed = next(ln for ln in lines if ln["event"] == "agent.failed")
    assert failed["code"] == code
    run_id = next(ln["run_id"] for ln in lines if ln["event"] == "run.authorized")
    assert _events(w, run_id)[-1] == "failed"
    assert w.dc.runs[run_id]["events"][-1]["data"]["code"] == code
    assert not any((w.storage / "outputs").rglob("*.dedf")) if (w.storage / "outputs").exists() else True
    assert list(work.iterdir()) == []


def test_contract_not_active_fails_before_authorization(world: World, tmp_path: Path) -> None:
    world.dc.contracts[world.contract["contract_id"]]["state"] = "revoked"
    rc, lines, _ = _run(world, tmp_path)
    assert rc == 1
    assert next(ln for ln in lines if ln["event"] == "agent.failed")["code"] == "contract_not_active"
    assert world.dc.runs == {}


def test_unknown_recipe_digest(world: World, tmp_path: Path) -> None:
    rc, lines, _ = _run(world, tmp_path, recipe_digest="sha256:" + "0" * 64)
    assert rc == 1 and next(ln for ln in lines if ln["event"] == "agent.failed")["code"] == "recipe_not_in_contract"


def test_tampered_dataset_object_is_detected(world: World, tmp_path: Path) -> None:
    obj = next((world.storage / "bank-a" / "loans" / "objects").rglob("*.dedf"))
    data = bytearray(obj.read_bytes())
    data[60] ^= 1
    obj.write_bytes(bytes(data))
    rc, lines, _ = _run(world, tmp_path)
    assert rc == 1
    assert next(ln for ln in lines if ln["event"] == "agent.failed")["code"] in ("dedf_tag", "dedf_hash")


def test_swapped_manifest_is_detected(world: World, tmp_path: Path) -> None:
    src = world.storage / "bureau" / "scores" / "manifest.json"
    dst = world.storage / "bank-a" / "loans" / "manifest.json"
    dst.write_bytes(src.read_bytes())
    rc, lines, _ = _run(world, tmp_path)
    assert rc == 1
    assert next(ln for ln in lines if ln["event"] == "agent.failed")["code"] in ("bad_signature", "manifest_mismatch")


def test_forged_rat_claims_rejected(world: World, tmp_path: Path) -> None:
    world.dc.rat_tamper = {"output": {"mode": "tdc", "recipients": ["party:bank-a#h1"]}}
    rc, lines, _ = _run(world, tmp_path)
    assert rc == 1 and next(ln for ln in lines if ln["event"] == "agent.failed")["code"] == "rat_mismatch"


def test_tee_only_output_mode(tmp_path: Path) -> None:
    w = build_world(tmp_path, output_mode="tee-only")
    rc, lines, _ = _run(w, tmp_path)
    assert rc == 0, lines
    run_id = next(ln["run_id"] for ln in lines if ln["event"] == "run.authorized")
    receipt = w.dc.runs[run_id]["receipt"]
    assert w.dc.runs[run_id]["events"][0]["at"].endswith("Z")
    assert receipt["outputs"][0]["recipients"] == [f"kms:output:{w.contract['contract_id']}#1"]


def test_dataset_suspension_blocks_key_release(world: World, tmp_path: Path) -> None:
    kms, _ = mock_clients(world.dc)
    kms.suspend_dataset(world.profile.signer("bank-a"), "bank-a/loans", "stop")
    rc, lines, _ = _run(world, tmp_path)
    assert rc == 1 and next(ln for ln in lines if ln["event"] == "agent.failed")["code"] == "service"


def test_rat_is_refreshed_and_output_credentials_come_late(world: World, tmp_path: Path) -> None:
    world.dc.rat_lifetime = 30  # always within the 60 s refresh margin
    rc, lines, _ = _run(world, tmp_path)
    assert rc == 0, lines
    events = [ln["event"] for ln in lines]
    assert world.dc.refresh_count >= 3  # two key releases + output preparation
    assert events.index("output.prepared") > events.index("gate.passed")  # credentials only at upload


def test_revocation_stops_an_in_flight_run_at_refresh(world: World, tmp_path: Path, monkeypatch) -> None:
    import depa.ccr.agent as agent_mod

    real_gate = agent_mod.apply_gate

    def revoke_then_gate(result, **kwargs):
        world.dc.contracts[world.contract["contract_id"]]["state"] = "revoked"
        return real_gate(result, **kwargs)

    monkeypatch.setattr(agent_mod, "apply_gate", revoke_then_gate)
    world.dc.rat_lifetime = 30
    rc, lines, _ = _run(world, tmp_path)
    assert rc == 1
    failed = next(ln for ln in lines if ln["event"] == "agent.failed")
    assert failed["stage"] == "output_prepare" and failed["code"] == "service"
    assert not (world.storage / "outputs").exists() or not any((world.storage / "outputs").rglob("*.dedf"))


def test_binding_carries_trust_bundle_digest(world: World, tmp_path: Path, monkeypatch) -> None:
    from depa.ccr.config import PinnedService, TrustBundle

    captured: dict = {}
    real = world.dc.cs.authorize

    def spy(rec, body):
        captured.update(body["binding"])
        return real(rec, body)

    monkeypatch.setattr(world.dc.cs, "authorize", spy)
    pinned = PinnedService(url="https://x", service_cert_pem="-----BEGIN CERTIFICATE-----", jwks=None)
    bundle = TrustBundle(cs=pinned, kms=pinned, digest="sha256:" + "ab" * 32)
    rc, _, _ = _run(world, tmp_path, trust_bundle=bundle)
    assert rc == 0 and captured["trust_bundle_digest"] == "sha256:" + "ab" * 32
