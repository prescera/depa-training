"""``depa dev scenario run <name>``: drive a full scenario on the dev stack.

TDPs create keys, set policies, encrypt, upload and publish; the TDC renders, proposes and
everyone verifies and signs the contract; a local CCR (``virtual`` attestation) runs it; the
TDC verifies the Execution Receipt and fetches and decrypts the outputs.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from depa.contract.authoring import build_contract, load_dataset_template
from depa.contract.verify import verify_contract
from depa.deploy.local import run_local
from depa.deploy.spec import JobSpec
from depa.dev.stack import VIRTUAL_RELEASE_ID, register_party
from depa.errors import NotFoundError, ServiceError, ValidationError, VerificationError
from depa.profile import Profile, generate_party
from depa.receipts import receipt_jws_of, verify_execution_receipt
from depa.storage.factory import party_connector
from depa.workflows import (
    build_trust_bundle,
    catalog_resolver,
    decrypt_output,
    encrypt_dataset,
    fetch_output,
    publish_dataset,
    storage_manifest_loader,
    upload_directory,
)


def _ensure_party(profile: Profile, party_id: str, roles: list[str]) -> dict[str, Any]:
    if (profile.keys_dir(party_id) / "party.json").exists():
        return profile.party_record(party_id)
    return generate_party(profile, party_id=party_id, name=party_id, roles=roles)


def run_scenario(
    scenario_dir: Path,
    *,
    home: Path,
    tdc: str = "tdc",
    trainer_cmd: str | None = None,
    work_root: Path | None = None,
    env_overrides: Mapping[str, str] | None = None,
    log: Any = print,
) -> dict[str, Any]:
    profile = Profile.load(home)
    kms, cs = profile.kms_client(), profile.cs_client()
    work = work_root or Path(tempfile.mkdtemp(prefix="depa-scenario-"))
    env = {
        **os.environ,
        "DEPA_TDC": tdc,
        "DEPA_KMS_URL": kms.base_url,
        "DEPA_PLATFORMS": "[virtual]",
        "DEPA_CCR_RELEASES": f"[{VIRTUAL_RELEASE_ID}]",
        **dict(env_overrides or {}),
    }
    templates = [load_dataset_template(p, env) for p in sorted((scenario_dir / "datasets").glob("*.yaml"))]
    if not templates:
        raise ValidationError(f"{scenario_dir}/datasets has no templates", code="bad_scenario")
    owners = sorted({t.dataset_id.split("/")[0] for t in templates})

    log(f"registering parties {[*owners, tdc]}")
    for party_id in sorted({*owners, tdc}):
        # A TDC that owns a dataset (e.g. its base model) is also a TDP of the contract.
        roles = (["tdc"] if party_id == tdc else []) + (["tdp"] if party_id in owners else [])
        register_party(profile, _ensure_party(profile, party_id, roles), [kms, cs])

    for t in templates:
        owner = t.dataset_id.split("/")[0]
        signer = profile.signer(owner)
        try:
            cs.get_dataset_version(t.dataset_id, t.version)
            log(f"{t.dataset_id}@{t.version}: already published")
            continue
        except NotFoundError:
            pass
        try:
            kms.get_dataset(t.dataset_id)
        except NotFoundError:
            kms.create_dataset(signer, t.dataset_id, str(t.descriptor.get("name", "")))
        if t.policy is None:
            raise ValidationError(f"{t.dataset_id}: template has no policy", code="no_policy")
        kms.set_policy(signer, t.dataset_id, t.policy)
        out = work / "encrypted" / t.dataset_id / t.version
        manifest = encrypt_dataset(t, kms=kms, signer=signer, out_dir=out)
        upload_directory(party_connector(t.descriptor["location"]), out, manifest)
        publish_dataset(cs, signer, t, out)
        log(f"{t.dataset_id}@{t.version}: published ({manifest.digest})")

    contract = build_contract(scenario_dir / "contract.yaml", env=env, resolve_manifest=catalog_resolver(cs))
    contract_id = contract["contract_id"]
    cs.propose_contract(profile.signer(tdc), contract)
    log(f"contract {contract_id} proposed")
    for party_id in dict.fromkeys([tdc, *owners]):  # a TDC may also own a dataset
        report = verify_contract(
            contract,
            party_id=party_id,
            kms=kms,
            cs=cs,
            record=cs.get_contract(contract_id),
            manifest_loader=storage_manifest_loader(),
        )
        if not report.ok:
            raise VerificationError(f"{party_id} refuses to sign: {report.errors}", code="verify_failed")
        cs.sign_contract(profile.signer(party_id), contract_id, report.contract_digest)
    record = _wait_state(cs, contract_id, "active")
    log(f"contract {contract_id} is {record['state']}")

    recipe_digest = contract["computation"]["recipes"][0]["digest"]
    spec = JobSpec(
        contract_id=contract_id,
        recipe_digest=recipe_digest,
        cs_url=cs.base_url,
        kms_url=kms.base_url,
        platform="virtual",
        max_duration_minutes=contract["computation"]["max_duration_minutes"],
        trust_bundle=build_trust_bundle(profile, kms, cs),
        log_level="info",
    )
    ccr_dir = work / "ccr"
    ccr_dir.mkdir(parents=True, exist_ok=True)
    extra = {"DEPA_WORK_DIR": str(ccr_dir)}
    if trainer_cmd:
        extra["DEPA_TRAINER_CMD"] = trainer_cmd
    job = run_local(spec, image=None, env_extra=extra)
    lines = [json.loads(line) for line in job.stderr.decode().splitlines() if line.startswith("{")]
    run_id = next((ln.get("run_id") for ln in lines if ln.get("event") == "run.authorized"), None)
    if not job.succeeded or not run_id:
        failure: dict[str, Any] = next((ln for ln in lines if ln.get("event") == "agent.failed"), {})
        raise ServiceError(f"CCR run failed: {failure}", service="ccr", code="ccr_failed")
    log(f"run {run_id} completed")

    run = cs.get_run(run_id)
    check = verify_execution_receipt(receipt_jws_of(run), cs.info().key_lookup(), kms.info().key_lookup())
    if not check.ok:
        raise VerificationError(f"receipt invalid: {check.problems}", code="bad_receipt")
    key = profile.hpke_key(tdc)
    outputs = {}
    for entry in check.claims["outputs"]:
        enc_dir = work / "outputs" / entry["name"] / "encrypted"
        fetch_output(check.claims, entry["name"], enc_dir, location=contract["output"]["location"], access_token=None)
        files = decrypt_output(
            enc_dir,
            work / "outputs" / entry["name"] / "plain",
            key=key,
            recipient=f"party:{tdc}#{key.kid}",
            expected_digest=entry["manifest_digest"],
            run_key=run.get("run_key") if isinstance(run.get("run_key"), dict) else None,
        )
        outputs[entry["name"]] = [str(f) for f in files]
    return {
        "contract_id": contract_id,
        "run_id": run_id,
        "metrics": check.claims.get("metrics"),
        "privacy_spent": check.claims.get("privacy_spent"),
        "outputs": outputs,
        "work_dir": str(work),
    }


def _wait_state(cs: Any, contract_id: str, state: str, timeout_s: float = 30) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    while True:
        record: dict[str, Any] = cs.get_contract(contract_id)
        if record.get("state") == state or time.monotonic() > deadline:
            if record.get("state") != state:
                raise ServiceError(f"contract is {record.get('state')}, expected {state}", service="cs", code="state")
            return record
        time.sleep(1)
