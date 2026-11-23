"""Runs (tdc, tdp): runs per contract, event timeline, receipt and ledger verification, model retrieval."""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from depa.errors import DepaError, ValidationError
from depa.receipts import ReceiptCheck, receipt_jws_of, verify_ccf_receipt, verify_execution_receipt
from depa.workflows import decrypt_output, fetch_output
from fastapi import APIRouter, Request
from starlette.responses import Response

from depa_console.forms import TXID_RE, empty_target_dir, opaque_id, text
from depa_console.plain import is_recipient, privacy_text
from depa_console.state import Console
from depa_console.tasks import TaskContext
from depa_console.views.contracts import contract_records, participant_record
from depa_console.web import Form, Me, console, flash, redirect, render, require, start_task, try_call

router = APIRouter(dependencies=[require("tdc", "tdp")])


def verify_receipt(c: Console, run: Mapping[str, Any]) -> tuple[ReceiptCheck, list[str]]:
    """The CS-signed Execution Receipt and every embedded KMS release receipt, against live keys."""
    kms, cs = c.clients()
    check = verify_execution_receipt(receipt_jws_of(run), cs.info().key_lookup(), kms.info().key_lookup())
    problems = list(check.problems)
    if check.claims.get("run_id") != run.get("run_id"):
        problems.append("receipt is for a different run")
    return check, problems


@router.get("/runs")
def runs(request: Request, me: Me) -> Response:
    c = console(request)
    wanted = request.query_params.get("contract", "")
    records, error = try_call(lambda: contract_records(c, me))
    groups = []
    for record in records or []:
        contract = record["contract"]
        if wanted and contract["contract_id"] != wanted:
            continue
        items, runs_error = try_call(lambda cid=contract["contract_id"]: c.clients()[1].list_runs(cid))
        rows = [
            {
                "run_id": r.get("run_id"),
                "state": r.get("state"),
                "platform": r.get("platform"),
                "events": len(r.get("events", [])),
                "last": (r.get("events") or [{}])[-1].get("at", ""),
            }
            for r in items or []
        ]
        groups.append({"contract": contract, "rows": rows, "error": runs_error})
    return render(request, "runs.html", groups=groups, error=error, wanted=wanted)


@router.get("/runs/{run_id}")
def run(request: Request, run_id: str, me: Me) -> Response:
    c = console(request)
    record = c.clients()[1].get_run(opaque_id(run_id, "run id"))
    contract_record = participant_record(c, me, str(record.get("contract_id")))
    contract = contract_record["contract"]
    events = sorted((e for e in record.get("events", []) if isinstance(e, dict)), key=lambda e: e.get("seq", 0))
    receipt, receipt_error, problems = None, None, []
    if record.get("state") == "completed":
        result, receipt_error = try_call(lambda: verify_receipt(c, record))
        if result is not None:
            receipt, problems = result
    claims = receipt.claims if receipt else {}
    return render(
        request,
        "run.html",
        run=record,
        contract=contract,
        events=events,
        receipt=receipt,
        claims=claims,
        problems=problems,
        receipt_error=receipt_error,
        privacy=privacy_text(claims.get("privacy_spent") or {}) if claims else "",
        ledger=c.ledger_checks.get(run_id),
        can_fetch=bool(receipt) and not problems and is_recipient(contract, me.party_id),
        outputs=[o.get("name") for o in claims.get("outputs", [])],
    )


@router.post("/runs/{run_id}/ledger")
def ledger(request: Request, run_id: str, me: Me, form: Form) -> Response:
    c = console(request)
    back = f"/runs/{run_id}"
    txid = text(form, "txid", max_len=40)
    if not TXID_RE.fullmatch(txid):
        flash(request, "error", "a transaction id looks like 2.42 (view.seqno)")
        return redirect(back)
    profile = c.profile()

    def job(ctx: TaskContext) -> dict[str, Any]:
        cs = c.clients()[1]
        record = cs.get_run(opaque_id(run_id, "run id"))
        participant_record(c, me, str(record.get("contract_id")))
        check, problems = verify_receipt(c, record)
        if problems:
            raise ValidationError("execution receipt invalid: " + "; ".join(problems), code="bad_receipt")
        ctx.log(f"fetching the CCF receipt for transaction {txid}")
        try:
            receipt = cs.ledger_receipt(txid)["receipt"]
            verify_ccf_receipt(receipt, profile.service_cert_pem("cs"), expected_claims=check.signed_claims)
        except DepaError as exc:
            c.record_check(run_id, False, [f"transaction {txid}: {exc}"])
            raise
        c.record_check(run_id, True, [f"transaction {txid} is in the ledger and binds this receipt"])
        ctx.log("ledger inclusion verified against the pinned service certificate")
        return {"run_id": run_id, "txid": txid, "ledger_verified": True}

    return start_task(request, f"Ledger check for run {run_id}", job, back=back)


@router.post("/runs/{run_id}/fetch")
def fetch(request: Request, run_id: str, me: Me, form: Form) -> Response:
    c = console(request)
    back = f"/runs/{run_id}"
    try:
        name = text(form, "output", max_len=128)
        if not name:
            raise ValidationError("choose an output", code="bad_input")
        target = empty_target_dir(text(form, "destination", max_len=4096), "the destination")
        token = c.token("output")
    except DepaError as exc:
        flash(request, "error", str(exc))
        return redirect(back)
    profile = c.profile()

    def job(ctx: TaskContext) -> dict[str, Any]:
        cs = c.clients()[1]
        record = cs.get_run(opaque_id(run_id, "run id"))
        contract = participant_record(c, me, str(record.get("contract_id")))["contract"]
        if not is_recipient(contract, me.party_id):
            raise ValidationError(f"{me.party_id} is not an output recipient of this contract", code="not_recipient")
        check, problems = verify_receipt(c, record)
        if problems:
            raise ValidationError("execution receipt invalid: " + "; ".join(problems), code="bad_receipt")
        ctx.log("execution receipt verified")
        entry = next((o for o in check.claims.get("outputs", []) if o.get("name") == name), None)
        if entry is None:
            raise ValidationError(f"the receipt has no output {name!r}", code="no_output")
        work = Path(tempfile.mkdtemp(prefix="depa-console-fetch-"))
        try:
            ctx.log(f"downloading {name} from {entry.get('location')}")
            fetch_output(check.claims, name, work, location=contract["output"]["location"], access_token=token)
            ctx.log("manifest matches the receipt; decrypting with this party's key")
            key = profile.hpke_key()
            run_key = record.get("run_key") if isinstance(record.get("run_key"), dict) else None
            files = decrypt_output(
                work,
                target,
                key=key,
                recipient=f"party:{me.party_id}#{key.kid}",
                expected_digest=str(entry["manifest_digest"]),
                run_key=run_key,
            )
        finally:
            shutil.rmtree(work, ignore_errors=True)
        for f in files:
            ctx.log(f"wrote {f}")
        return {"run_id": run_id, "output": name, "files": [str(f) for f in files]}

    return start_task(request, f"Fetch {name} of run {run_id}", job, back=back)
