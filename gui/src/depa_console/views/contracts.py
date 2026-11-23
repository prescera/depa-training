"""Contracts (tdc, tdp): list by state, plain-language detail, verify, sign (only if verified), revoke;
TDCs also propose (from a scenario template or a pasted / uploaded contract, validated first)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from depa.contract.authoring import build_contract
from depa.contract.validate import contract_errors
from depa.contract.verify import VerifyReport, verify_contract
from depa.contract.yamlio import load_yaml_text
from depa.core.encoding import digest, dumps_pretty, loads_object
from depa.core.ids import dataset_owner
from depa.dev.stack import VIRTUAL_RELEASE_ID
from depa.errors import DepaError, ValidationError
from depa.workflows import catalog_resolver, storage_manifest_loader
from fastapi import APIRouter, Request
from starlette.datastructures import UploadFile
from starlette.responses import Response

from depa_console.errors import explain
from depa_console.forms import checked, opaque_id, text
from depa_console.plain import contract_summary
from depa_console.state import Console, Identity
from depa_console.web import Form, Me, NotAllowed, act, console, flash, redirect, render, require, try_call

router = APIRouter(dependencies=[require("tdc", "tdp")])
STATE_ORDER = ("draft", "signed", "active", "exhausted", "expired", "revoked")
MAX_CONTRACT = 512 * 1024


def signers(contract: Mapping[str, Any]) -> set[str]:
    return {contract["parties"]["tdc"], *(dataset_owner(d["dataset_id"]) for d in contract["datasets"])}


def contract_records(c: Console, me: Identity) -> list[dict[str, Any]]:
    listing = c.clients()[1].list_contracts(me.party_id)
    items = listing.get("contracts", []) if isinstance(listing, dict) else listing
    return [r for r in items or [] if isinstance(r, dict) and isinstance(r.get("contract"), dict)]


def participant_record(c: Console, me: Identity, contract_id: str) -> dict[str, Any]:
    record = c.clients()[1].get_contract(opaque_id(contract_id, "contract id"))
    if me.party_id not in signers(record["contract"]):
        raise NotAllowed(me, f"contract {contract_id}")
    return record


def verify(c: Console, me: Identity, record: Mapping[str, Any]) -> VerifyReport:
    """``depa contract verify`` for this party, re-reading its own manifests from storage."""
    kms, cs = c.clients()
    return verify_contract(
        record["contract"],
        party_id=me.party_id,
        kms=kms,
        cs=cs,
        record=record,
        manifest_loader=storage_manifest_loader(c.token("storage")),
    )


@router.get("/contracts")
def contracts(request: Request, me: Me) -> Response:
    records, error = try_call(lambda: contract_records(console(request), me))
    groups: dict[str, list[dict[str, Any]]] = {}
    for record in records or []:
        c = record["contract"]
        signed = {str(s.get("party")) for s in record.get("signatures", [])}
        groups.setdefault(str(record.get("state")), []).append(
            {
                "id": c["contract_id"],
                "title": c.get("title") or c["contract_id"],
                "role": "TDC" if c["parties"]["tdc"] == me.party_id else "TDP",
                "signed": me.party_id in signed,
                "signatures": f"{len(signed & signers(c))}/{len(signers(c))}",
                "runs": f"{record.get('runs_used', 0)}/{c['computation']['max_runs']}",
                "not_after": c["validity"]["not_after"],
            }
        )
    ordered = sorted(groups.items(), key=lambda kv: STATE_ORDER.index(kv[0]) if kv[0] in STATE_ORDER else 99)
    return render(request, "contracts.html", groups=ordered, error=error)


# ---- proposing (TDC) ------------------------------------------------------------------------


def contract_templates(c: Console) -> list[Path]:
    root = c.settings.scenarios_root
    return sorted(p.resolve() for p in root.glob("*/contract.yaml")) if root.is_dir() else []


def render_template(c: Console, me: Identity, path: Path, *, dev: bool) -> dict[str, Any]:
    """``depa contract init``: fresh id and validity, embedded recipes, manifest digests from the catalog."""
    variables = {**os.environ, "DEPA_TDC": me.party_id, "DEPA_KMS_URL": c.base.kms.url or ""}
    if dev:
        variables |= {"DEPA_PLATFORMS": "[virtual]", "DEPA_CCR_RELEASES": f"[{VIRTUAL_RELEASE_ID}]"}
    outputs = (c.env.raw.get("outputs") or {}).get(me.party_id) if c.env else None

    def customize(doc: dict[str, Any]) -> None:
        if outputs:
            doc["output"]["location"] = {k: v for k, v in outputs.items() if v not in (None, {})}

    return build_contract(path, env=variables, resolve_manifest=catalog_resolver(c.clients()[1]), customize=customize)


def submitted_contract(form: Any) -> tuple[str, dict[str, Any]]:
    """The contract text (uploaded file, else the text area) and its parsed document."""
    upload = form.get("file")
    raw = ""
    if isinstance(upload, UploadFile) and upload.filename:
        data = upload.file.read(MAX_CONTRACT + 1)
        if len(data) > MAX_CONTRACT:
            raise ValidationError("the uploaded file is too large", code="bad_input")
        try:
            raw = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValidationError("the uploaded file is not UTF-8 text", code="bad_input") from exc
    raw = (raw or text(form, "contract", max_len=MAX_CONTRACT)).strip()
    if not raw:
        raise ValidationError("paste or upload a contract (JSON or YAML)", code="bad_input")
    doc = loads_object(raw) if raw.startswith("{") else load_yaml_text(raw, source="contract")
    if not isinstance(doc, dict):
        raise ValidationError("a contract is a JSON object / YAML mapping", code="bad_input")
    return raw, doc


def _proposal_page(request: Request, me: Identity, raw: str, doc: dict[str, Any] | None, error: str | None) -> Response:
    errors = [error] if error else []
    if doc is not None:
        errors += contract_errors(doc)
        parties = doc.get("parties")
        tdc = parties.get("tdc") if isinstance(parties, dict) else None
        if tdc != me.party_id:
            errors.append(f"the contract names {tdc} as TDC, not {me.party_id}")
    summary = None
    if doc is not None and not errors:
        record = {"contract": doc, "contract_digest": digest(doc), "state": "not proposed"}
        summary = contract_summary(record, me.party_id)
    return render(
        request,
        "contract_new.html",
        422 if errors else 200,
        templates=contract_templates(console(request)),
        raw=raw,
        checked=doc is not None or bool(error),
        errors=errors,
        s=summary,
    )


@router.get("/contracts/new", dependencies=[require("tdc")])
def new_contract(request: Request, me: Me) -> Response:
    return render(request, "contract_new.html", templates=contract_templates(console(request)), raw="",
                  checked=False, errors=[], s=None)  # fmt: skip


@router.post("/contracts/new", dependencies=[require("tdc")])
def new_contract_submit(request: Request, me: Me, form: Form) -> Response:
    c = console(request)
    action = text(form, "action", max_len=10)
    raw, doc = "", None
    try:
        if action == "render":
            chosen = Path(text(form, "template", max_len=4096))
            if chosen not in contract_templates(c):
                raise ValidationError("choose one of the listed contract templates", code="bad_input")
            doc = render_template(c, me, chosen, dev=text(form, "mode", max_len=10) == "dev")
            raw = dumps_pretty(doc)
        else:
            raw, doc = submitted_contract(form)
    except DepaError as exc:
        return _proposal_page(request, me, raw, None, explain(exc).text)
    if action != "propose" or contract_errors(doc) or doc["parties"]["tdc"] != me.party_id:
        return _proposal_page(request, me, raw, doc, None)
    try:
        c.clients()[1].propose_contract(c.profile().signer(), doc)
    except DepaError as exc:
        return _proposal_page(request, me, raw, doc, explain(exc).text)
    contract_id = str(doc["contract_id"])
    flash(request, "ok", f"proposed contract {contract_id} ({digest(doc)}); each party now verifies and signs it")
    return redirect(f"/contracts/{contract_id}")


@router.get("/contracts/{contract_id}")
def contract(request: Request, contract_id: str, me: Me) -> Response:
    c = console(request)
    record = participant_record(c, me, contract_id)
    report, verify_error = try_call(lambda: verify(c, me, record))
    summary = contract_summary(record, me.party_id)
    signed = any(s["me"] and s["signed"] for s in summary["signers"])
    return render(
        request,
        "contract.html",
        s=summary,
        report=report,
        verify_error=verify_error,
        can_sign=bool(report and report.ok) and not signed and record.get("state") == "draft",
        signed=signed,
        can_revoke=record.get("state") in ("draft", "signed", "active"),
    )


@router.post("/contracts/{contract_id}/sign")
def sign(request: Request, contract_id: str, me: Me, form: Form) -> Response:
    c = console(request)

    def go() -> str:
        if not checked(form, "confirm"):
            raise ValidationError("tick the confirmation to sign", code="unconfirmed")
        record = participant_record(c, me, contract_id)
        report = verify(c, me, record)  # re-verified at signing time, never trusted from the page
        if not report.ok:
            raise ValidationError("refusing to sign, verification failed: " + "; ".join(report.errors), code="verify")
        c.clients()[1].sign_contract(c.profile().signer(), contract_id, report.contract_digest)
        return f"signed contract {contract_id} ({report.contract_digest})"

    return act(request, f"/contracts/{contract_id}", go)


@router.post("/contracts/{contract_id}/revoke")
def revoke(request: Request, contract_id: str, me: Me, form: Form) -> Response:
    c = console(request)

    def go() -> str:
        reason = text(form, "reason")
        if not reason:
            raise ValidationError("give a reason for revoking (recorded on the ledger)", code="bad_input")
        if not checked(form, "confirm"):
            raise ValidationError("tick the confirmation to revoke", code="unconfirmed")
        participant_record(c, me, contract_id)
        c.clients()[1].revoke_contract(c.profile().signer(), contract_id, reason)
        return f"contract {contract_id} revoked: no further runs or key releases"

    return act(request, f"/contracts/{contract_id}", go)
