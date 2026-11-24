"""``depa dc``: Data Collaborative governance on both services (spec §3.3, §4, §5, §6)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlsplit

import typer

from depa.cli.common import EXIT_VERIFY, STATE, emit, fail, handle_errors, read_env_secret
from depa.clients.base import BaseClient
from depa.contract.schema import require_schema
from depa.contract.yamlio import load_document
from depa.core.encoding import loads_object
from depa.errors import ValidationError
from depa.workflows import audit, submit_to_both

app = typer.Typer(help="Data Collaborative governance (members only).", no_args_is_help=True)
release_app = typer.Typer(help="CCR release registry (identical proposals to both services).", no_args_is_help=True)
app.add_typer(release_app, name="release")


class Service(StrEnum):
    kms = "kms"
    cs = "cs"
    both = "both"


def _clients(service: Service, *, operator: bool = False) -> list[BaseClient]:
    profile = STATE.profile()
    out: list[BaseClient] = []
    if service in (Service.kms, Service.both):
        out.append(profile.kms_client(operator=operator))
    if service in (Service.cs, Service.both):
        out.append(profile.cs_client(operator=operator))
    return out


def _short(client: BaseClient) -> str:
    return "kms" if client.service_name == "depa-kms" else "cs"


@app.command("bootstrap")
@handle_errors
def bootstrap(
    members: Annotated[list[Path], typer.Option("--member", help="Party record JSON of a dc-member (repeat)")],
    service: Annotated[Service, typer.Option(help="Which service(s)")] = Service.both,
    threshold: Annotated[str, typer.Option(help="Default threshold: majority | unanimous | n=<k>")] = "majority",
    client_cert: Annotated[str | None, typer.Option(help="Operator client certificate (sandbox user_cert)")] = None,
    client_key: Annotated[str | None, typer.Option(help="Operator client key")] = None,
) -> None:
    """Operator-only, once per service: register the initial members and default threshold."""
    records = [loads_object(p.read_bytes()) for p in members]
    default: Any = {"n": int(threshold[2:])} if threshold.startswith("n=") else threshold
    profile = STATE.profile()
    if client_cert:
        profile = profile.with_overrides(operator_cert=client_cert, operator_key=client_key)
    results = {}
    for client in [profile.kms_client(operator=True), profile.cs_client(operator=True)]:
        if service != Service.both and _short(client) != service.value:
            continue
        results[_short(client)] = client.gov_bootstrap(records, {"default": default})
    emit(results, "\n".join(f"{k}: bootstrapped, service key {v.get('jwks')}" for k, v in results.items()))


@app.command("propose")
@handle_errors
def propose(
    actions_file: Annotated[Path, typer.Argument(help="YAML/JSON: a list of {name, args} actions")],
    service: Annotated[Service, typer.Option()] = Service.both,
    expires_in_s: Annotated[int, typer.Option(help="Proposal lifetime")] = 604800,
) -> None:
    """Submit a governance proposal (identical actions to each selected service)."""
    doc = load_document(actions_file)
    actions = doc.get("actions")
    if not isinstance(actions, list):
        raise ValidationError("actions file must contain `actions: [...]`", code="bad_proposal")
    signer = STATE.profile().signer()
    results = {_short(c): c.gov_propose(signer, actions, expires_in_s) for c in _clients(service)}
    emit(
        results,
        "\n".join(f"{k}: proposal {v.get('proposal_id')} ({v.get('state')})" for k, v in results.items()),
    )


@app.command("vote")
@handle_errors
def vote(
    proposal_id: Annotated[str, typer.Argument()],
    ballot: Annotated[str, typer.Argument(help="approve | reject")],
    service: Annotated[Service, typer.Option(help="Service holding the proposal")] = Service.kms,
) -> None:
    """Cast a ballot (last ballot wins while the proposal is open)."""
    if service == Service.both:
        raise fail("proposal ids differ per service; pick --service kms or cs", 2)
    signer = STATE.profile().signer()
    result = _clients(service)[0].gov_vote(signer, proposal_id, ballot)
    emit(result, f"{service.value}: proposal {proposal_id} is {result.get('state')}")


@app.command("proposals")
@handle_errors
def proposals(service: Annotated[Service, typer.Option()] = Service.both) -> None:
    """List proposals and their state."""
    result = {_short(c): c.gov_proposals() for c in _clients(service)}
    lines = []
    for svc, items in result.items():
        for p in items:
            names = ",".join(a.get("name", "?") for a in p.get("actions", []))
            lines.append(f"{svc}  {p.get('proposal_id')}  {p.get('state'):9}  {names}")
    emit(result, "\n".join(lines) or "no proposals")


@app.command("members")
@handle_errors
def members(service: Annotated[Service, typer.Option()] = Service.both) -> None:
    """List governance members."""
    emit({_short(c): c.gov_members() for c in _clients(service)})


@app.command("settings")
@handle_errors
def settings(
    allow_virtual_attestation: Annotated[bool, typer.Option(help="Development only; never for production data")],
    base_url: Annotated[str | None, typer.Option(help="Service base URL (each service gets its own)")] = None,
    service: Annotated[Service, typer.Option()] = Service.both,
) -> None:
    """Propose ``set_settings`` (unanimous by default)."""
    signer = STATE.profile().signer()
    results = {}
    for client in _clients(service):
        args: dict[str, Any] = {"allow_virtual_attestation": allow_virtual_attestation}
        args["base_url"] = base_url or client.base_url
        results[_short(client)] = client.gov_propose(signer, [{"name": "set_settings", "args": args}])
    emit(results)


@app.command("pin-peers")
@handle_errors
def pin_peers() -> None:
    """Propose ``set_peer_service`` on each service with the other's live URL and JWKS.

    Compare the printed key ids with the services' out-of-band published values before voting.
    """
    profile = STATE.profile()
    kms, cs = profile.kms_client(), profile.cs_client()
    signer = profile.signer()
    kms_info, cs_info = kms.info(), cs.info()
    result = {
        "kms": kms.gov_propose(
            signer,
            [
                {
                    "name": "set_peer_service",
                    "args": {"name": cs_info.service, "url": cs.base_url, "jwks": list(cs_info.jwks)},
                }
            ],
        ),
        "cs": cs.gov_propose(
            signer,
            [
                {
                    "name": "set_peer_service",
                    "args": {"name": kms_info.service, "url": kms.base_url, "jwks": list(kms_info.jwks)},
                }
            ],
        ),
        "cs_kids": [k["kid"] for k in cs_info.jwks],
        "kms_kids": [k["kid"] for k in kms_info.jwks],
    }
    emit(result)


def _operator_token(service_url: str, token_env: str | None) -> str | None:
    """ACL Administrator bearer: ``--operator-token-env``, ``$DEPA_OPERATOR_TOKEN``, else the az CLI for ACL."""
    if token_env:
        return read_env_secret(token_env)
    if os.environ.get("DEPA_OPERATOR_TOKEN"):
        return os.environ["DEPA_OPERATOR_TOKEN"]
    az = shutil.which("az")
    if az is None or not (urlsplit(service_url).hostname or "").endswith(".confidential-ledger.azure.com"):
        return None
    argv = [
        az,
        "account",
        "get-access-token",
        "--resource",
        "https://confidential-ledger.azure.com",
        "--query",
        "accessToken",
        "-o",
        "tsv",
    ]
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=60, check=False)
    return (proc.stdout.strip() or None) if proc.returncode == 0 else None


@app.command("setup")
@handle_errors
def setup(
    env_file: Annotated[Path | None, typer.Option("--env", help="depa-env.yaml (or the global --env)")] = None,
    client_cert: Annotated[str | None, typer.Option(help="Operator client certificate (only to bootstrap)")] = None,
    client_key: Annotated[str | None, typer.Option(help="Operator client key")] = None,
    operator_token_env: Annotated[
        str | None, typer.Option(help="Env var with an ACL Administrator bearer token (default: az CLI for ACL)")
    ] = None,
    allow_virtual_attestation: Annotated[
        bool, typer.Option(help="Bootstrap with virtual attestation enabled (development only)")
    ] = False,
    step: Annotated[
        list[str] | None,
        typer.Option("--step", help="bootstrap-kms | bootstrap-cs | link | register | all (repeatable/comma)"),
    ] = None,
) -> None:
    """One-time / idempotent governance from an environment file: bootstrap if needed, register the
    parties, pin peers, approve the releases in releases_dir in both services, set OIDC settings."""
    from depa.deployment import dc_setup, parse_dc_steps  # noqa: PLC0415
    from depa.env import load_env  # noqa: PLC0415
    from depa.profile import depa_home  # noqa: PLC0415

    path = env_file or STATE.env
    if path is None:
        raise fail("--env depa-env.yaml is required", 2)
    env = load_env(path)
    profile = env.profile(STATE.home or depa_home())
    operator = (client_cert, client_key) if client_cert and client_key else None
    summary = dc_setup(
        env,
        profile,
        operator=operator,
        operator_token=lambda url: _operator_token(url, operator_token_env),
        allow_virtual=allow_virtual_attestation,
        steps=parse_dc_steps(step or []),
        log=lambda m: sys.stderr.write(f"[dc setup] {m}\n"),
    )
    emit(summary)


@app.command("trust-bundle")
@handle_errors
def trust_bundle() -> None:
    """Print the CCR trust bundle (DEPA_TRUST_BUNDLE value), its document and digest (spec §9.1)."""
    from depa.core.encoding import digest  # noqa: PLC0415
    from depa.workflows import build_trust_bundle, trust_bundle_document  # noqa: PLC0415

    profile = STATE.profile()
    kms, cs = profile.kms_client(), profile.cs_client()
    doc = trust_bundle_document(profile, kms, cs)
    value = build_trust_bundle(profile, kms, cs)
    settings = {}
    for client, which in ((kms, "kms"), (cs, "cs")):
        settings[which] = (client.gov_state().get("settings") or {}).get("trust_bundle_digest")
    emit(
        {"digest": digest(doc), "bundle": value, "document": doc, "service_settings": settings},
        f"digest  {digest(doc)}\nkms     {settings['kms']}\ncs      {settings['cs']}\nbundle  {value}",
    )


@app.command("audit")
@handle_errors
def audit_cmd() -> None:
    """Check deployed bundles are governance-approved, peer pins are current, OIDC gateway is consistent."""
    profile = STATE.profile()
    report = audit(profile.kms_client(), profile.cs_client())
    emit(
        report,
        "\n".join([*(f"OK    {i}" for i in report["info"]), *(f"FAIL  {e}" for e in report["errors"])]),
    )
    if report["errors"]:
        raise typer.Exit(EXIT_VERIFY)


def _release_record(path: Path) -> dict[str, Any]:
    doc = load_document(path)
    record = doc.get("release") if "deployment" in doc else doc
    if not isinstance(record, dict):
        raise ValidationError("release manifest has no release record", code="bad_release")
    require_schema(record, "ccr-release", "CCR release record")
    return record


@release_app.command("approve")
@handle_errors
def release_approve(
    release: Annotated[Path, typer.Argument(help="ccr/releases/<release_id>.json (or a bare §6 record)")],
) -> None:
    """Propose ``approve_ccr_release`` with the identical record to the KMS and the CS."""
    profile = STATE.profile()
    record = _release_record(release)
    result = submit_to_both(
        profile.kms_client(),
        profile.cs_client(),
        profile.signer(),
        [{"name": "approve_ccr_release", "args": record}],
    )
    emit(result, "\n".join(f"{k}: proposal {v.get('proposal_id')}" for k, v in result.items()))


@release_app.command("revoke")
@handle_errors
def release_revoke(release_id: Annotated[str, typer.Argument()]) -> None:
    """Propose ``revoke_ccr_release`` to both services (a revoked id can never be re-approved)."""
    profile = STATE.profile()
    result = submit_to_both(
        profile.kms_client(),
        profile.cs_client(),
        profile.signer(),
        [{"name": "revoke_ccr_release", "args": {"release_id": release_id}}],
    )
    emit(result, "\n".join(f"{k}: proposal {v.get('proposal_id')}" for k, v in result.items()))


@release_app.command("list")
@handle_errors
def release_list() -> None:
    """Show both registries side by side."""
    profile = STATE.profile()
    out = {}
    for client in (profile.kms_client(), profile.cs_client()):
        out[_short(client)] = [
            {"release_id": e.release.get("release_id"), "state": e.state} for e in client.list_releases()
        ]
    emit(out)
