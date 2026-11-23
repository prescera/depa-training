"""Shared client pieces: ``GET /info``, governance (§4) and party / release registry reads."""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from depa.clients.http import ServiceEndpoint, ServiceHttp, path_segment
from depa.core.ids import check_party_id, parse_key_ref
from depa.core.jws import KeyLookup, jwks_lookup
from depa.core.keys import JWK, Signer, public_key_from_jwk, x25519_public_from_b64
from depa.errors import ServiceError, ValidationError

GOV_VOTES = ("approve", "reject")


@dataclass(frozen=True)
class ServiceInfo:
    service: str
    version: str
    bundle_digest: str
    jwks: tuple[JWK, ...]
    oidc_issuer: str | None
    raw: dict[str, Any]
    bootstrapped: bool = True

    @classmethod
    def parse(cls, obj: object, expected_service: str) -> ServiceInfo:
        if not isinstance(obj, dict):
            raise ValidationError("GET /info returned a non-object", code="bad_info")
        if obj.get("service") != expected_service:
            raise ValidationError(f"GET /info: expected service {expected_service!r}", code="bad_info")
        jwks = obj.get("jwks")
        bootstrapped = obj.get("bootstrapped", True) is not False
        if not isinstance(jwks, list) or (bootstrapped and not jwks):
            raise ValidationError("GET /info: jwks missing", code="bad_info")
        for jwk in jwks:
            public_key_from_jwk(jwk)
        jwks_lookup(jwks)  # rejects duplicate / missing kids
        issuer = obj.get("oidc_issuer")
        return cls(
            service=expected_service,
            version=str(obj.get("version", "")),
            bundle_digest=str(obj.get("bundle_digest", "")),
            jwks=tuple(jwks),
            oidc_issuer=issuer if isinstance(issuer, str) else None,
            raw=obj,
            bootstrapped=bootstrapped,
        )

    def key_lookup(self) -> KeyLookup:
        if not self.jwks:
            raise ValidationError(f"{self.service} is not bootstrapped (no signing keys)", code="not_bootstrapped")
        return jwks_lookup(self.jwks)


@dataclass(frozen=True)
class Party:
    party_id: str
    name: str
    roles: tuple[str, ...]
    signing_keys: tuple[JWK, ...]
    hpke_public_keys: tuple[dict[str, str], ...]
    status: str
    raw: dict[str, Any]

    @classmethod
    def parse(cls, obj: object) -> Party:
        if not isinstance(obj, dict):
            raise ValidationError("party record must be an object", code="bad_party")
        party_id = check_party_id(obj.get("party_id"))
        keys = obj.get("signing_keys", [])
        hpke_keys = obj.get("hpke_public_keys", [])
        roles = obj.get("roles", [])
        if not isinstance(keys, list) or not isinstance(hpke_keys, list) or not isinstance(roles, list):
            raise ValidationError("malformed party record", code="bad_party")
        for jwk in keys:
            public_key_from_jwk(jwk)
        for hk in hpke_keys:
            if not isinstance(hk, dict) or not isinstance(hk.get("kid"), str):
                raise ValidationError("malformed hpke_public_keys entry", code="bad_party")
            x25519_public_from_b64(hk.get("x25519"))
        return cls(
            party_id=party_id,
            name=str(obj.get("name", "")),
            roles=tuple(str(r) for r in roles),
            signing_keys=tuple(keys),
            hpke_public_keys=tuple(hpke_keys),
            status=str(obj.get("status", "")),
            raw=obj,
        )

    @property
    def active(self) -> bool:
        return self.status == "active"

    def key_lookup(self) -> KeyLookup:
        """Resolve JWS header kids of the form ``<party_id>#<kid>`` (active parties only)."""
        table = jwks_lookup(self.signing_keys)

        def lookup(ref: str) -> JWK | None:
            if not self.active:
                return None
            try:
                party, kid = parse_key_ref(ref)
            except ValidationError:
                return None
            return table(kid) if party == self.party_id else None

        return lookup

    def hpke_key(self, kid: str) -> bytes:
        for entry in self.hpke_public_keys:
            if entry["kid"] == kid:
                return x25519_public_from_b64(entry["x25519"])
        raise ValidationError(f"party {self.party_id} has no HPKE key {kid!r}", code="unknown_hpke_kid")


@dataclass(frozen=True)
class ReleaseEntry:
    """Registry entry ``{release: <§6 record>, state: approved | revoked}``."""

    release: dict[str, Any]
    state: str

    @classmethod
    def parse(cls, obj: object) -> ReleaseEntry:
        if not isinstance(obj, dict) or not isinstance(obj.get("release"), dict):
            raise ValidationError("release registry entry is malformed", code="bad_release")
        state = obj.get("state")
        if state not in ("approved", "revoked"):
            raise ValidationError("release registry entry has an unknown state", code="bad_release")
        return cls(release=obj["release"], state=str(state))

    @property
    def approved(self) -> bool:
        return self.state == "approved"


class BaseClient:
    service_name: str = ""

    def __init__(
        self,
        endpoint: ServiceEndpoint,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout_s: float = 30.0,
        retries: int = 3,
        backoff_s: float = 0.5,
    ) -> None:
        if endpoint.name != self.service_name:
            raise ValueError(f"endpoint is for {endpoint.name}, client is {self.service_name}")
        self.http = ServiceHttp(
            endpoint, transport=transport, timeout_s=timeout_s, retries=retries, backoff_s=backoff_s
        )

    @property
    def base_url(self) -> str:
        return self.http.endpoint.base

    def close(self) -> None:
        self.http.close()

    def __enter__(self) -> BaseClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @staticmethod
    def _obj(value: Any, what: str) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise ServiceError(f"{what}: expected a JSON object", service="unknown", code="bad_response")
        return value

    @staticmethod
    def _bearer(token: str | None) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"} if token else {}

    # ---- /info -------------------------------------------------------------------------------

    def info(self) -> ServiceInfo:
        return ServiceInfo.parse(self.http.get("/info"), self.service_name)

    # ---- governance (§3.3, §4) ---------------------------------------------------------------

    def gov_bootstrap(
        self,
        members: Sequence[Mapping[str, Any]],
        threshold: Mapping[str, Any],
        settings: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Operator-only (client certificate or bearer). ``settings``: ``{base_url, allow_virtual_attestation}``."""
        body: dict[str, Any] = {"members": [dict(m) for m in members], "threshold": dict(threshold)}
        if settings is not None:
            body["settings"] = dict(settings)
        return self._obj(self.http.post("/gov/bootstrap", body), "bootstrap")

    def gov_propose(
        self, signer: Signer, actions: Sequence[Mapping[str, Any]], expires_in_s: int = 604800
    ) -> dict[str, Any]:
        if not actions:
            raise ValidationError("a proposal needs at least one action", code="bad_proposal")
        for action in actions:
            if not isinstance(action.get("name"), str) or not isinstance(action.get("args"), dict):
                raise ValidationError("each action needs a name and an args object", code="bad_proposal")
        body = {"actions": [dict(a) for a in actions], "expires_in_s": int(expires_in_s)}
        return self._obj(self.http.signed("POST", "/gov/proposals", signer, "gov.propose", body), "propose")

    def gov_vote(self, signer: Signer, proposal_id: str, vote: str) -> dict[str, Any]:
        if vote not in GOV_VOTES:
            raise ValidationError("vote must be approve or reject", code="bad_vote")
        path = f"/gov/proposals/{path_segment(proposal_id)}/ballots"
        body = {"vote": vote, "proposal_id": proposal_id}  # binds the ballot to the proposal
        return self._obj(self.http.signed("POST", path, signer, "gov.vote", body), "vote")

    def gov_proposals(self) -> list[dict[str, Any]]:
        result = self._obj(self.http.get("/gov/proposals"), "proposals")
        items = result.get("proposals")
        if not isinstance(items, list):
            raise ServiceError("proposals listing is malformed", service=self.service_name, code="bad_response")
        return [p for p in items if isinstance(p, dict)]

    def gov_proposal(self, proposal_id: str) -> dict[str, Any]:
        return self._obj(self.http.get(f"/gov/proposals/{path_segment(proposal_id)}"), "proposal")

    def gov_members(self) -> Any:
        return self.http.get("/gov/members")

    def gov_state(self) -> dict[str, Any]:
        """``{bootstrapped, settings, peer_services, jwt_validation_policies, approved_app_digests}``."""
        return self._obj(self.http.get("/gov/state"), "gov state")

    def gov_constitution(self) -> dict[str, Any]:
        return self._obj(self.http.get("/gov/constitution"), "constitution")

    # ---- registries (read-only; see report: endpoints implied by §3.1 / §6) -------------------

    def get_party(self, party_id: str) -> Party:
        return Party.parse(self.http.get(f"/parties/{path_segment(check_party_id(party_id))}"))

    def get_release(self, release_id: str) -> ReleaseEntry:
        return ReleaseEntry.parse(self.http.get(f"/releases/{path_segment(release_id)}"))

    def list_parties(self) -> list[Party]:
        result = self._obj(self.http.get("/parties"), "parties")
        return [Party.parse(p) for p in result.get("parties", [])]

    def list_releases(self) -> list[ReleaseEntry]:
        result = self._obj(self.http.get("/releases"), "releases")
        return [ReleaseEntry.parse(r) for r in result.get("releases", [])]

    def ledger_receipt(self, transaction_id: str, *, wait_s: float = 30.0) -> dict[str, Any]:
        """CCF receipt for a committed transaction; polls while it is pending or being fetched."""
        deadline = time.monotonic() + wait_s
        while True:
            result = self._obj(self.http.get("/receipt", params={"transaction_id": transaction_id}), "receipt")
            if isinstance(result.get("receipt"), dict):
                return result
            if time.monotonic() >= deadline:
                raise ServiceError(
                    f"receipt for {transaction_id} not available ({result.get('status')})",
                    service=self.service_name,
                    code="receipt_pending",
                )
            time.sleep(1.0)
