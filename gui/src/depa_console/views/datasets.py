"""Datasets (tdp): keys, release policy and history, encrypt + upload + publish, kill switch."""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import IO, Any

from depa.contract.authoring import DatasetTemplate, load_dataset_template
from depa.core.ids import check_dataset_id, dataset_owner
from depa.deployment import locate_dataset_template
from depa.errors import DepaError, NotFoundError, ValidationError
from depa.storage import Connector
from depa.storage.factory import federation_audience, party_connector
from depa.workflows import encrypt_dataset, publish_dataset, upload_directory
from fastapi import APIRouter, Request
from starlette.responses import Response

from depa_console.errors import explain
from depa_console.forms import checked, existing_dir, parse_policy_form, policy_defaults, text
from depa_console.plain import human_bytes, policy_rows
from depa_console.state import Console, Identity
from depa_console.tasks import TaskContext
from depa_console.web import Form, Me, NotAllowed, act, console, flash, redirect, render, require, start_task, try_call

router = APIRouter(dependencies=[require("tdp")])


@dataclass(frozen=True)
class TemplateRef:
    path: Path
    dataset_id: str
    version: str
    source: Path | None


def _template_env(c: Console) -> dict[str, str]:
    env = {**os.environ, "DEPA_KMS_URL": c.base.kms.url or ""}
    tdcs = c.env.parties_with("tdc") if c.env else []
    if len(tdcs) == 1:
        env["DEPA_TDC"] = tdcs[0].party_id
    return env


def _located(c: Console, t: DatasetTemplate) -> DatasetTemplate:
    """Place the dataset under the owner's storage from the environment file, as scenario deploy does."""
    if c.env is None or dataset_owner(t.dataset_id) not in (c.env.raw.get("storage") or {}):
        return t
    # The policy is set separately (policy form), so only the descriptor is placed.
    return locate_dataset_template(c.env, replace(t, policy=None), kms_url=c.base.kms.url)


def _templates(c: Console, owner: str) -> tuple[list[TemplateRef], int]:
    """Dataset templates under the scenarios root owned by ``owner`` (and how many were unreadable)."""
    refs, unreadable = [], 0
    env = _template_env(c)
    for path in sorted(c.settings.scenarios_root.glob("*/datasets/*.yaml")):
        try:
            t = load_dataset_template(path, env)
        except DepaError:
            unreadable += 1
            continue
        if dataset_owner(t.dataset_id) == owner:
            refs.append(TemplateRef(path.resolve(), t.dataset_id, t.version, t.source.path if t.source else None))
    return refs, unreadable


def _audiences(c: Console, owner: str) -> list[str]:
    storage = (c.env.raw.get("storage") or {}).get(owner) if c.env else None
    if not storage or not storage.get("federation"):
        return []
    audience = federation_audience(storage["connector"], storage["federation"])
    return [audience] if audience else []


def _own(me: Identity, owner: str, name: str) -> str:
    dataset_id = check_dataset_id(f"{owner}/{name}")
    if owner != me.party_id:
        raise NotAllowed(me, f"dataset {dataset_id}")
    return dataset_id


@router.get("/datasets")
def datasets(request: Request, me: Me) -> Response:
    c = console(request)
    lookup = request.query_params.get("dataset_id", "").strip()
    if lookup:
        try:
            if dataset_owner(lookup) != me.party_id:
                raise ValidationError(f"{lookup} is not owned by {me.party_id}", code="not_owner")
            return redirect(f"/datasets/{lookup}")
        except DepaError as exc:
            flash(request, "error", str(exc))
            return redirect("/datasets")
    kms, cs = c.clients()
    ids: set[str] = set()
    catalog, catalog_error = try_call(cs.list_datasets)
    for entry in (catalog or {}).get("datasets", []) if isinstance(catalog, dict) else []:
        if isinstance(entry, dict) and entry.get("owner") == me.party_id:
            ids.add(str(entry.get("dataset_id")))
    templates, unreadable = _templates(c, me.party_id)
    ids.update(t.dataset_id for t in templates)
    contracts, _ = try_call(lambda: cs.list_contracts(me.party_id))
    for record in (contracts or {}).get("contracts", []) if isinstance(contracts, dict) else []:
        for d in record.get("contract", {}).get("datasets", []):
            if dataset_owner(d["dataset_id"]) == me.party_id:
                ids.add(d["dataset_id"])
    rows = []
    for dataset_id in sorted(ids):
        row: dict[str, Any] = {"dataset_id": dataset_id, "state": "", "keys": 0, "policy": "", "error": None}
        try:
            record = kms.get_dataset(dataset_id)
            policy = record.raw.get("policy_version", "set" if record.policy else "none")
            row.update(state=record.state, keys=len(record.keys), policy=str(policy))
        except NotFoundError:
            row["state"] = "no key yet"
        except DepaError as exc:
            row["error"] = explain(exc).text
        rows.append(row)
    return render(
        request,
        "datasets.html",
        rows=rows,
        templates=templates,
        unreadable=unreadable,
        catalog_error=catalog_error,
    )


@router.get("/datasets/{owner}/{name}")
def dataset(request: Request, owner: str, name: str, me: Me) -> Response:
    c = console(request)
    dataset_id = _own(me, owner, name)
    kms, cs = c.clients()
    record, record_error = try_call(lambda: kms.get_dataset(dataset_id))
    history, history_error = try_call(lambda: kms.policy_history(dataset_id)) if record else (None, None)
    entries = [
        {"version": e.get("version"), "set_at": e.get("set_at"), "rows": policy_rows(e.get("policy"))}
        for e in (history.get("versions", []) if isinstance(history, dict) else [])
        if isinstance(e, dict)
    ]
    catalog, catalog_error = try_call(cs.list_datasets)
    entry = next(
        (e for e in (catalog or {}).get("datasets", []) if isinstance(e, dict) and e.get("dataset_id") == dataset_id),
        None,
    )
    templates = [t for t in _templates(c, me.party_id)[0] if t.dataset_id == dataset_id]
    return render(
        request,
        "dataset.html",
        dataset_id=dataset_id,
        record=record,
        record_error=record_error,
        policy=policy_rows(record.policy) if record else None,
        history=entries,
        history_error=history_error,
        versions=list(entry.get("versions", [])) if entry else [],
        catalog_error=catalog_error,
        templates=templates,
    )


@router.post("/datasets/{owner}/{name}/create-key")
def create_key(request: Request, owner: str, name: str, me: Me, form: Form) -> Response:
    c = console(request)
    dataset_id = _own(me, owner, name)

    def go() -> str:
        record = c.clients()[0].create_dataset(c.profile().signer(), dataset_id, text(form, "description"))
        return f"created {dataset_id} with key {', '.join(k.kid for k in record.keys)}"

    return act(request, f"/datasets/{dataset_id}", go)


@router.get("/datasets/{owner}/{name}/policy")
def policy_page(request: Request, owner: str, name: str, me: Me) -> Response:
    c = console(request)
    dataset_id = _own(me, owner, name)
    record = c.clients()[0].get_dataset(dataset_id)
    values = policy_defaults(record.policy, _audiences(c, me.party_id), date.today())
    return render(request, "policy.html", dataset_id=dataset_id, values=values, errors={}, current=record.policy)


@router.post("/datasets/{owner}/{name}/policy")
def policy_submit(request: Request, owner: str, name: str, me: Me, form: Form) -> Response:
    c = console(request)
    dataset_id = _own(me, owner, name)
    parsed = parse_policy_form(form, date.today())
    if parsed.policy is None:
        current, _ = try_call(lambda: c.clients()[0].get_dataset(dataset_id).policy)
        return render(
            request, "policy.html", 422, dataset_id=dataset_id, values=parsed.values, errors=parsed.errors,
            current=current,
        )  # fmt: skip
    policy = parsed.policy

    def go() -> str:
        record = c.clients()[0].set_policy(c.profile().signer(), dataset_id, policy)
        return f"{dataset_id}: policy version {record.raw.get('policy_version', '?')} is now in force"

    return act(request, f"/datasets/{dataset_id}", go)


@router.post("/datasets/{owner}/{name}/{switch}")
def kill_switch(request: Request, owner: str, name: str, switch: str, me: Me, form: Form) -> Response:
    c = console(request)
    dataset_id = _own(me, owner, name)

    def go() -> str:
        if switch not in ("suspend", "resume"):
            raise ValidationError("unknown action", code="bad_input")
        reason = text(form, "reason")
        if not reason:
            raise ValidationError("give a reason (it is recorded in the KMS ledger)", code="bad_input")
        if not checked(form, "confirm"):
            raise ValidationError(f"tick the confirmation to {switch} {dataset_id}", code="unconfirmed")
        kms, signer = c.clients()[0], c.profile().signer()
        if switch == "suspend":
            kms.suspend_dataset(signer, dataset_id, reason)
            return f"{dataset_id} suspended: the KMS releases no keys or storage tokens for it"
        kms.resume_dataset(signer, dataset_id, reason)
        return f"{dataset_id}: owner suspension lifted (a DC suspension, if any, stays in force)"

    return act(request, f"/datasets/{dataset_id}", go)


class _ProgressConnector:
    """Wraps a storage connector to report each object upload (and allow cancellation between them)."""

    def __init__(self, inner: Connector, total: int, ctx: TaskContext) -> None:
        self._inner, self._total, self._ctx, self._done = inner, total, ctx, 0
        self.connector = inner.connector

    def upload(self, key: str, src: IO[bytes], size: int) -> None:
        self._done += 1
        self._ctx.log(f"upload {self._done}/{self._total}: {key} ({human_bytes(size)})")
        self._inner.upload(key, src, size)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def _encrypt_progress(ctx: TaskContext) -> Callable[[str, int, int], None]:
    """Per-file encryption progress: start, every further 25 %, and completion (a cancellation point)."""
    reported: dict[str, int] = {}

    def report(path: str, done: int, total: int) -> None:
        quarter = 4 if total == 0 else done * 4 // total
        if path in reported and quarter <= reported[path]:
            return
        reported[path] = quarter
        if quarter >= 4:
            ctx.log(f"encrypted {path} ({human_bytes(total)})")
        else:
            ctx.log(f"encrypting {path}: {quarter * 25}% of {human_bytes(total)}")

    return report


@router.post("/datasets/publish")
def publish(request: Request, me: Me, form: Form) -> Response:
    c = console(request)
    try:
        raw = text(form, "template", max_len=4096)
        path = Path(raw).expanduser()
        if not raw or not path.is_absolute() or path.suffix not in (".yaml", ".yml") or not path.is_file():
            raise ValidationError("choose a dataset template (absolute path to a .yaml file)", code="bad_path")
        template = _located(c, load_dataset_template(path, _template_env(c)))
        if dataset_owner(template.dataset_id) != me.party_id:
            raise ValidationError(f"{template.dataset_id} is not owned by {me.party_id}", code="not_owner")
        source_raw = text(form, "source", max_len=4096)
        source = existing_dir(source_raw, "the source directory") if source_raw else None
        records_raw = text(form, "records", max_len=12)
        if records_raw and not records_raw.isdigit():
            raise ValidationError("records must be a whole number", code="bad_input")
        records = int(records_raw) if records_raw else None
        token = c.token("storage")
    except DepaError as exc:
        flash(request, "error", str(exc))
        return redirect("/datasets")
    profile = c.profile()
    label = f"{template.dataset_id}@{template.version}"

    def job(ctx: TaskContext) -> dict[str, Any]:
        kms, cs = c.clients()
        signer = profile.signer()
        try:
            cs.get_dataset_version(template.dataset_id, template.version)
            raise ValidationError(f"{label} is already in the catalog (versions are immutable)", code="exists")
        except NotFoundError:
            pass
        try:
            kms.get_dataset(template.dataset_id)
        except NotFoundError:
            kms.create_dataset(signer, template.dataset_id, str(template.descriptor.get("name", "")))
            ctx.log(f"created the dataset key {template.dataset_id}#1 in the KMS")
        work = Path(tempfile.mkdtemp(prefix="depa-console-encrypt-"))
        try:
            ctx.log(f"encrypting {source or (template.source.path if template.source else '?')} (dedf/1)")
            manifest = encrypt_dataset(
                template, kms=kms, signer=signer, out_dir=work / "out", source=source, progress=_encrypt_progress(ctx)
            )
            ctx.log(
                f"encrypted {len(manifest.files)} files, {human_bytes(manifest.total_ciphertext_size)}; "
                f"manifest {manifest.digest}"
            )
            location = template.descriptor["location"]
            connector = _ProgressConnector(party_connector(location, access_token=token), len(manifest.files) + 2, ctx)
            ctx.log(f"uploading to {location['uri']} (manifest last)")
            upload_directory(connector, work / "out", manifest)
            ctx.checkpoint()
            descriptor = publish_dataset(cs, signer, template, work / "out", records=records)
            ctx.log(f"published {label} to the catalog")
        finally:
            shutil.rmtree(work, ignore_errors=True)
        policy = kms.get_dataset(template.dataset_id).policy
        if policy is None:
            ctx.log("note: no release policy is set yet, so no clean room can obtain the key")
        return {
            "dataset_id": template.dataset_id,
            "version": template.version,
            "manifest_digest": descriptor["manifest_digest"],
            "size_bytes": descriptor["size_bytes"],
            "location": location["uri"],
        }

    return start_task(request, f"Publish {label}", job, back="/datasets")
