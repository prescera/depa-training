"""``depa run`` (runs and Execution Receipts) and ``depa model`` (fetch and decrypt outputs)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from depa.cli.common import EXIT_VERIFY, STATE, emit, handle_errors, read_env_secret
from depa.core.jws import decode_payload_unverified
from depa.errors import DepaError, ValidationError
from depa.receipts import receipt_jws_of, verify_ccf_receipt, verify_execution_receipt
from depa.workflows import decrypt_output, fetch_output

run_app = typer.Typer(help="Runs and Execution Receipts.", no_args_is_help=True)
model_app = typer.Typer(help="Trained outputs: fetch and decrypt.", no_args_is_help=True)


@run_app.command("list")
@handle_errors
def list_runs(contract_id: Annotated[str, typer.Option("--contract", help="Contract id")]) -> None:
    """Runs of a contract."""
    emit(STATE.profile().cs_client().list_runs(contract_id))


@run_app.command("receipt")
@handle_errors
def receipt(run_id: Annotated[str, typer.Argument()]) -> None:
    """Run state, events and the Execution Receipt (claims shown unverified; use verify-receipt)."""
    run = STATE.profile().cs_client().get_run(run_id)
    out = dict(run)
    try:
        out["receipt_claims"] = decode_payload_unverified(receipt_jws_of(run))
    except DepaError:
        out["receipt_claims"] = None
    emit(out)


@run_app.command("verify-receipt")
@handle_errors
def verify_receipt(
    run_id: Annotated[str, typer.Argument()],
    txid: Annotated[
        str | None,
        typer.Option(
            help="Transaction id of the `completed` event (logged by the CCR as event.posted txid) "
            "to also verify ledger inclusion with a CCF receipt"
        ),
    ] = None,
) -> None:
    """Verify the CS-signed Execution Receipt, every KMS release receipt and (optionally) ledger inclusion."""
    profile = STATE.profile()
    cs, kms = profile.cs_client(), profile.kms_client()
    run = cs.get_run(run_id)
    check = verify_execution_receipt(receipt_jws_of(run), cs.info().key_lookup(), kms.info().key_lookup())
    problems = list(check.problems)
    if check.claims.get("run_id") != run_id:
        problems.append("receipt is for a different run")
    ledger_ok = None
    if txid is not None:
        # The CS binds the signed receipt payload as the transaction's claims digest.
        verify_ccf_receipt(
            cs.ledger_receipt(txid)["receipt"], profile.service_cert_pem("cs"), expected_claims=check.signed_claims
        )
        ledger_ok = True
    result = {"ok": not problems, "problems": problems, "ledger_verified": ledger_ok, "receipt": check.claims}
    emit(
        result,
        ("receipt verified" if not problems else "receipt INVALID:\n  " + "\n  ".join(problems))
        + (f"; ledger inclusion verified ({txid})" if ledger_ok else ""),
    )
    if problems:
        raise typer.Exit(EXIT_VERIFY)


@model_app.command("fetch")
@handle_errors
def fetch(
    run_id: Annotated[str, typer.Option("--run", help="Run id")],
    out: Annotated[Path, typer.Option(help="Directory for the encrypted output")],
    name: Annotated[str, typer.Option(help="Output name")] = "model",
    access_token_env: Annotated[str | None, typer.Option(help="Env var with a storage bearer token")] = None,
) -> None:
    """Download an output named in a verified receipt (manifest checked against the receipt)."""
    profile = STATE.profile()
    cs, kms = profile.cs_client(), profile.kms_client()
    check = verify_execution_receipt(
        receipt_jws_of(cs.get_run(run_id)), cs.info().key_lookup(), kms.info().key_lookup()
    )
    if not check.ok:
        raise ValidationError("receipt invalid: " + "; ".join(check.problems), code="bad_receipt")
    contract = cs.get_contract(str(check.claims["contract_id"]))["contract"]
    manifest = fetch_output(
        check.claims,
        name,
        out,
        location=contract["output"]["location"],
        access_token=read_env_secret(access_token_env),
    )
    emit(
        {"manifest_digest": manifest.digest, "files": len(manifest.files), "dir": str(out)},
        f"fetched {name} ({len(manifest.files)} files) into {out}",
    )


@model_app.command("decrypt")
@handle_errors
def decrypt(
    encrypted: Annotated[Path, typer.Argument(help="Directory from `depa model fetch`")],
    run_id: Annotated[str, typer.Option("--run", help="Run id (the receipt binds the manifest digest)")],
    out: Annotated[Path, typer.Option(help="Plaintext output directory")],
    name: Annotated[str, typer.Option(help="Output name")] = "model",
) -> None:
    """Decrypt with this party's HPKE key after checking the manifest against the verified receipt."""
    profile = STATE.profile()
    cs, kms = profile.cs_client(), profile.kms_client()
    run = cs.get_run(run_id)
    check = verify_execution_receipt(receipt_jws_of(run), cs.info().key_lookup(), kms.info().key_lookup())
    if not check.ok:
        raise ValidationError("receipt invalid: " + "; ".join(check.problems), code="bad_receipt")
    entry = next((o for o in check.claims.get("outputs", []) if o.get("name") == name), None)
    if entry is None:
        raise ValidationError(f"receipt has no output {name!r}", code="no_output")
    key = profile.hpke_key()
    recipient = f"party:{profile.require_party()}#{key.kid}"
    run_key = run.get("run_key") if isinstance(run.get("run_key"), dict) else None
    files = decrypt_output(
        encrypted,
        out,
        key=key,
        recipient=recipient,
        expected_digest=str(entry["manifest_digest"]),
        run_key=run_key,
    )
    emit({"files": [str(f) for f in files]}, "\n".join(str(f) for f in files))
