"""``depa party``: local party identity (keys and public record)."""

from __future__ import annotations

from typing import Annotated

import typer

from depa.cli.common import STATE, emit, handle_errors
from depa.profile import generate_party

app = typer.Typer(help="Manage this party's signing (ES256) and HPKE (X25519) keys.", no_args_is_help=True)


@app.command("keygen")
@handle_errors
def keygen(
    party_id: Annotated[str, typer.Option("--party-id", help="Party id, e.g. bank-a")],
    name: Annotated[str, typer.Option(help="Legal / display name")],
    roles: Annotated[str, typer.Option(help="Comma-separated: tdp, tdc, ccrp, dc-member, auditor")],
    signing_kid: Annotated[str, typer.Option(help="Signing key id")] = "k1",
    hpke_kid: Annotated[str, typer.Option(help="HPKE key id")] = "h1",
    set_default: Annotated[
        bool, typer.Option("--set-default/--no-set-default", help="Make this the profile party")
    ] = True,
) -> None:
    """Generate keys (files 0600 under $DEPA_HOME/keys/<party>) and the public party record."""
    profile = STATE.profile()
    record = generate_party(
        profile,
        party_id=party_id,
        name=name,
        roles=[r.strip() for r in roles.split(",") if r.strip()],
        signing_kid=signing_kid,
        hpke_kid=hpke_kid,
    )
    if set_default:
        profile.with_overrides(party_id=party_id).save()
    path = profile.keys_dir(party_id) / "party.json"
    emit(record, f"created keys for {party_id}; give {path} to the Data Collaborative for registration")


@app.command("show")
@handle_errors
def show(party_id: Annotated[str | None, typer.Option("--party-id")] = None) -> None:
    """Print the public party record (as registered with the DC)."""
    emit(STATE.profile().party_record(party_id))
