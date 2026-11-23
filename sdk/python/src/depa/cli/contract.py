"""``depa contract``: authoring, validation, verification and the signature flow (spec §8)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated, Any

import typer

from depa.cli.common import EXIT_VERIFY, STATE, emit, handle_errors, read_env_secret
from depa.contract.authoring import build_contract
from depa.contract.validate import contract_errors
from depa.contract.verify import VerifyReport, verify_contract
from depa.contract.yamlio import load_document
from depa.core.encoding import digest, dumps_pretty, is_digest
from depa.errors import NotFoundError, ValidationError
from depa.workflows import catalog_resolver, storage_manifest_loader

app = typer.Typer(
    help="Contracts: init from a template, validate, verify, propose, sign, revoke.", no_args_is_help=True
)


def _load(ref: str) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """A contract from a file, or from the CS by contract id (then also returns the record)."""
    path = Path(ref)
    if path.exists():
        return load_document(path), None
    record = STATE.profile().cs_client().get_contract(ref)
    return record["contract"], record


@app.command("init")
@handle_errors
def init(
    template: Annotated[Path, typer.Argument(help="Contract template (scenarios/<name>/contract.yaml)")],
    out: Annotated[Path, typer.Option(help="Where to write the canonical contract JSON")],
    offline: Annotated[bool, typer.Option(help="Do not contact the CS (template must give manifest digests)")] = False,
    manifest_digest: Annotated[
        list[str] | None, typer.Option(help="Override as <dataset_id>=sha256:<hex> (repeatable)")
    ] = None,
) -> None:
    """Render a template: new contract_id, validity, embedded recipes, manifest digests from the catalog."""
    overrides: dict[str, str] = {}
    for item in manifest_digest or []:
        key, _, value = item.partition("=")
        if not is_digest(value):
            raise ValidationError(f"bad --manifest-digest {item!r}", code="bad_option")
        overrides[key] = value
    catalog = None if offline else catalog_resolver(STATE.profile().cs_client())

    def resolve(dataset_id: str, version: str) -> str:
        if dataset_id in overrides:
            return overrides[dataset_id]
        if catalog is None:
            raise ValidationError(f"no manifest digest for {dataset_id} (offline)", code="bad_template")
        return catalog(dataset_id, version)

    contract = build_contract(template, env=os.environ, resolve_manifest=resolve)
    out.write_text(dumps_pretty(contract))
    emit(
        {"contract_id": contract["contract_id"], "contract_digest": digest(contract), "path": str(out)},
        f"wrote {out}: contract {contract['contract_id']} ({digest(contract)})",
    )


@app.command("validate")
@handle_errors
def validate(contract_file: Annotated[Path, typer.Argument()]) -> None:
    """Offline schema and consistency checks."""
    contract = load_document(contract_file)
    errors = contract_errors(contract)
    emit(
        {"ok": not errors, "errors": errors, "contract_digest": digest(contract)},
        "valid" if not errors else "INVALID:\n  " + "\n  ".join(errors),
    )
    if errors:
        raise typer.Exit(EXIT_VERIFY)


def _print_report(report: VerifyReport) -> None:
    lines = [f"contract {report.contract_id} ({report.contract_digest})"]
    lines += [f"  info   {m}" for m in report.info]
    lines += [f"  WARN   {m}" for m in report.warnings]
    lines += [f"  ERROR  {m}" for m in report.errors]
    lines.append("verified: OK" if report.ok else "verified: FAILED")
    emit(report.to_json(), "\n".join(lines))


def _verify(ref: str, fetch_manifests: bool, access_token_env: str | None) -> tuple[VerifyReport, dict[str, Any]]:
    profile = STATE.profile()
    contract, record = _load(ref)
    cs = profile.cs_client()
    if record is None:
        try:
            record = cs.get_contract(str(contract.get("contract_id")))
        except NotFoundError:  # not yet proposed: verify the document alone
            record = None
    loader = storage_manifest_loader(read_env_secret(access_token_env)) if fetch_manifests else None
    report = verify_contract(
        contract,
        party_id=profile.require_party(),
        kms=profile.kms_client(),
        cs=cs,
        record=record,
        manifest_loader=loader,
    )
    return report, contract


@app.command("verify")
@handle_errors
def verify(
    contract: Annotated[str, typer.Argument(help="Contract JSON file or contract id")],
    fetch_manifests: Annotated[bool, typer.Option(help="Re-verify your manifests from storage")] = True,
    access_token_env: Annotated[str | None, typer.Option(help="Env var with a storage bearer token")] = None,
) -> None:
    """Everything a party should check before signing (datasets, policies, releases, outputs, privacy)."""
    report, _ = _verify(contract, fetch_manifests, access_token_env)
    _print_report(report)
    if not report.ok:
        raise typer.Exit(EXIT_VERIFY)


@app.command("propose")
@handle_errors
def propose(contract_file: Annotated[Path, typer.Argument()]) -> None:
    """Submit the contract (as the TDC). It starts in state draft."""
    contract = load_document(contract_file)
    errors = contract_errors(contract)
    if errors:
        raise ValidationError("contract is invalid:\n  " + "\n  ".join(errors), code="invalid_contract")
    profile = STATE.profile()
    result = profile.cs_client().propose_contract(profile.signer(), contract)
    emit(
        result if isinstance(result, dict) else {"contract_id": contract["contract_id"]},
        f"proposed {contract['contract_id']} ({digest(contract)})",
    )


@app.command("sign")
@handle_errors
def sign(
    contract_id: Annotated[str, typer.Argument()],
    fetch_manifests: Annotated[bool, typer.Option(help="Re-verify your manifests from storage")] = True,
    access_token_env: Annotated[str | None, typer.Option(help="Env var with a storage bearer token")] = None,
) -> None:
    """Verify (refusing on any error) and sign the CS's copy of the contract."""
    report, _ = _verify(contract_id, fetch_manifests, access_token_env)
    if not report.ok:
        _print_report(report)
        raise typer.Exit(EXIT_VERIFY)
    profile = STATE.profile()
    result = profile.cs_client().sign_contract(profile.signer(), contract_id, report.contract_digest)
    emit(
        {"signed": report.contract_digest, "result": result, "warnings": report.warnings},
        f"signed {contract_id} ({report.contract_digest})",
    )


@app.command("status")
@handle_errors
def status(contract_id: Annotated[str, typer.Argument()]) -> None:
    """State, signatures and runs used."""
    record = STATE.profile().cs_client().get_contract(contract_id)
    sigs = ", ".join(str(s.get("party")) for s in record.get("signatures", []))
    emit(
        {k: v for k, v in record.items() if k != "contract"},
        f"{contract_id}: {record.get('state')}; signed by [{sigs}]; runs used {record.get('runs_used')}",
    )


@app.command("revoke")
@handle_errors
def revoke(contract_id: Annotated[str, typer.Argument()], reason: Annotated[str, typer.Option()]) -> None:
    """Revoke immediately (any required party). Future runs and key releases fail."""
    profile = STATE.profile()
    emit(profile.cs_client().revoke_contract(profile.signer(), contract_id, reason))
