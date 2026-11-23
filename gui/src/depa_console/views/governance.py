"""Governance (dc-member): members, constitution, proposals and ballots, common proposals."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from depa.clients.base import BaseClient
from depa.errors import ValidationError
from fastapi import APIRouter, HTTPException, Request
from starlette.responses import Response

from depa_console.forms import checked, expiry_seconds, opaque_id, party_record, release_record, text
from depa_console.plain import describe_action
from depa_console.state import Console, Identity
from depa_console.web import Form, Me, act, console, render, require, try_call

router = APIRouter(dependencies=[require("dc-member")])
SERVICES = ("kms", "cs")
BACK = "/governance"


def _client(c: Console, service: str) -> BaseClient:
    if service not in SERVICES:
        raise HTTPException(404, "unknown service")
    kms, cs = c.clients()
    return kms if service == "kms" else cs


def _proposal_view(service: str, p: Mapping[str, Any], me: str) -> dict[str, Any]:
    ballots = p.get("ballots") if isinstance(p.get("ballots"), dict) else {}
    return {
        "service": service,
        "id": str(p.get("proposal_id", "")),
        "proposer": p.get("proposer", ""),
        "state": str(p.get("state", "")),
        "created_at": p.get("created_at"),
        "expires_at": p.get("expires_at"),
        "actions": [(str(a.get("name")), describe_action(a)) for a in p.get("actions", []) if isinstance(a, dict)],
        "ballots": sorted(ballots.items()),
        "tally": p.get("tally"),
        "my_ballot": ballots.get(me),
    }


def _members(raw: Any) -> list[tuple[str, str]]:
    items = raw.get("members", []) if isinstance(raw, dict) else raw
    out = []
    for m in items or []:
        if isinstance(m, dict):
            out.append((str(m.get("party_id")), str(m.get("name", ""))))
        else:
            out.append((str(m), ""))
    return out


@router.get("/governance")
def governance(request: Request, me: Me) -> Response:
    c = console(request)
    services: list[dict[str, Any]] = []
    proposals: list[dict[str, Any]] = []
    releases: set[str] = set()
    for service in SERVICES:
        client, error = try_call(lambda s=service: _client(c, s))
        if client is None:
            services.append({"name": service, "error": error})
            continue
        members, members_error = try_call(client.gov_members)
        constitution, constitution_error = try_call(client.gov_constitution)
        items, proposals_error = try_call(client.gov_proposals)
        registry, _ = try_call(client.list_releases)
        releases.update(str(e.release.get("release_id")) for e in registry or [] if e.approved)
        proposals.extend(_proposal_view(service, p, me.party_id) for p in items or [])
        services.append(
            {
                "name": service,
                "members": _members(members),
                "members_error": members_error,
                "constitution": json.dumps(constitution, indent=2, sort_keys=True) if constitution else None,
                "constitution_error": constitution_error,
                "proposals_error": proposals_error,
            }
        )
    manifests, manifests_error = try_call(c.env.releases) if c.env else ([], None)
    proposals.sort(key=lambda p: (p["state"] != "open", -float(p["created_at"] or 0)))
    return render(
        request,
        "governance.html",
        services=services,
        open_proposals=[p for p in proposals if p["state"] == "open"],
        closed_proposals=[p for p in proposals if p["state"] != "open"],
        approved_releases=sorted(releases),
        manifests=manifests or [],
        manifests_error=manifests_error,
        registrable=[i for i in c.held() if i.party_id != me.party_id],
    )


def _propose(c: Console, me: Identity, actions: Sequence[Mapping[str, Any]], form: Any) -> str:
    """Identical proposals to the KMS and the CS; optionally cast this member's approval."""
    expires = expiry_seconds(form)
    signer = c.profile().signer(me.party_id)
    outcome = []
    for service in SERVICES:
        client = _client(c, service)
        proposal = client.gov_propose(signer, actions, expires)
        state = proposal.get("state")
        if checked(form, "approve"):
            state = client.gov_vote(signer, str(proposal["proposal_id"]), "approve").get("state")
        outcome.append(f"{service}: proposal {proposal.get('proposal_id')} is {state}")
    return "; ".join(outcome)


@router.post("/governance/propose/party")
def propose_party(request: Request, me: Me, form: Form) -> Response:
    c = console(request)

    def go() -> str:
        held = text(form, "held_party", max_len=63)
        if held and held not in {i.party_id for i in c.held()}:
            raise ValidationError("unknown held party", code="bad_input")
        record = c.profile().party_record(held) if held else party_record(text(form, "record", max_len=200_000))
        action = "set_member" if "dc-member" in record.get("roles", []) else "set_party"
        return f"{action} {record['party_id']}: " + _propose(c, me, [{"name": action, "args": record}], form)

    return act(request, BACK, go)


@router.post("/governance/propose/release")
def propose_release(request: Request, me: Me, form: Form) -> Response:
    c = console(request)

    def go() -> str:
        chosen = text(form, "manifest", max_len=128)
        if chosen:
            manifest = next((m for m in (c.env.releases() if c.env else []) if m.release_id == chosen), None)
            if manifest is None:
                raise ValidationError(f"no release manifest {chosen} in releases_dir", code="bad_input")
            record = manifest.release
        else:
            record = release_record(text(form, "record", max_len=200_000))
        actions = [{"name": "approve_ccr_release", "args": record}]
        return f"approve release {record['release_id']}: " + _propose(c, me, actions, form)

    return act(request, BACK, go)


@router.post("/governance/propose/revoke-release")
def propose_revoke_release(request: Request, me: Me, form: Form) -> Response:
    c = console(request)

    def go() -> str:
        release_id = opaque_id(text(form, "release_id", max_len=128), "release id")
        if not checked(form, "confirm"):
            raise ValidationError("confirm that a revoked release can never be approved again", code="unconfirmed")
        actions = [{"name": "revoke_ccr_release", "args": {"release_id": release_id}}]
        return f"revoke release {release_id}: " + _propose(c, me, actions, form)

    return act(request, BACK, go)


@router.post("/governance/propose/settings")
def propose_settings(request: Request, me: Me, form: Form) -> Response:
    c = console(request)

    def go() -> str:
        value = text(form, "allow_virtual_attestation", max_len=5)
        if value not in ("true", "false"):
            raise ValidationError("choose whether virtual attestation is allowed", code="bad_input")
        if value == "true" and not checked(form, "confirm"):
            raise ValidationError("confirm that virtual attestation is for development only", code="unconfirmed")
        actions = [{"name": "set_settings", "args": {"allow_virtual_attestation": value == "true"}}]
        return f"allow_virtual_attestation={value}: " + _propose(c, me, actions, form)

    return act(request, BACK, go)


def _find(c: Console, service: str, proposal_id: str) -> dict[str, Any]:
    wanted = opaque_id(proposal_id, "proposal id")
    for proposal in _client(c, service).gov_proposals():
        if proposal.get("proposal_id") == wanted:
            return proposal
    raise HTTPException(404, f"no proposal {wanted} in the {service} service")


@router.get("/governance/{service}/proposals/{proposal_id}/vote")
def vote_confirm(request: Request, service: str, proposal_id: str, me: Me) -> Response:
    ballot = request.query_params.get("ballot", "")
    if ballot not in ("approve", "reject"):
        raise HTTPException(400, "ballot must be approve or reject")
    proposal = _proposal_view(service, _find(console(request), service, proposal_id), me.party_id)
    return render(request, "vote.html", proposal=proposal, ballot=ballot)


@router.post("/governance/{service}/proposals/{proposal_id}/vote")
def vote(request: Request, service: str, proposal_id: str, me: Me, form: Form) -> Response:
    c = console(request)

    def go() -> str:
        ballot = text(form, "ballot", max_len=7)
        client = _client(c, service)
        result = client.gov_vote(c.profile().signer(me.party_id), opaque_id(proposal_id, "proposal id"), ballot)
        return f"{service}: ballot '{ballot}' cast; proposal is {result.get('state')}"

    return act(request, BACK, go)
