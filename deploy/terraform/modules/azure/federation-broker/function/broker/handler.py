"""Request handling: Bearer KMS token in, container-scoped user-delegation SAS out (spec §10.1).

Responses: 200 {"sas_url", "prefix", "expires_at"}; 401 for any token problem (no detail);
403 for a valid token whose sub is not configured. A grant keyed "dataset:<owner>/*" covers every
dataset of that owner (one container holding all of the owner's datasets).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from .sas import DelegationKey, container_sas, format_time
from .token import TokenError, UnknownKeyError, Verifier, load_jwks

log = logging.getLogger("depa.broker")
_START_SKEW = timedelta(minutes=5)
_JWKS_REFRESH_INTERVAL_S = 300


@dataclass(frozen=True)
class Grant:
    container: str
    prefix: str
    access: str  # "read" | "write"


@dataclass(frozen=True)
class Config:
    issuer: str
    audience: str
    jwks: Mapping[str, Any]
    jwks_refresh: bool
    account: str
    blob_endpoint: str
    identity_client_id: str
    grants: Mapping[str, Grant]
    sas_max_ttl_s: int

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> Config:
        grants = {
            sub: Grant(container=g["container"], prefix=g.get("prefix", ""), access=g["access"])
            for sub, g in json.loads(env["BROKER_GRANTS"]).items()
        }
        if not grants or any(g.access not in ("read", "write") for g in grants.values()):
            raise ValueError("BROKER_GRANTS must map subjects to read/write grants")
        ttl = int(env.get("BROKER_SAS_MAX_TTL_SECONDS", "900"))
        if not 60 <= ttl <= 3600:
            raise ValueError("BROKER_SAS_MAX_TTL_SECONDS must be within [60, 3600]")
        return cls(
            issuer=env["BROKER_ISSUER"],
            audience=env["BROKER_AUDIENCE"],
            jwks=json.loads(env["BROKER_JWKS"]),
            jwks_refresh=env.get("BROKER_JWKS_REFRESH", "false").lower() == "true",
            account=env["BROKER_ACCOUNT_NAME"],
            blob_endpoint=env["BROKER_BLOB_ENDPOINT"].rstrip("/"),
            identity_client_id=env["BROKER_IDENTITY_CLIENT_ID"],
            grants=grants,
            sas_max_ttl_s=ttl,
        )


def fetch_issuer_jwks(issuer: str) -> Mapping[str, Any]:
    """JWKS from the configured issuer's discovery document; jwks_uri must live under the issuer."""

    def get(url: str) -> Any:
        if not url.startswith("https://"):
            raise TokenError("non-https JWKS location")
        with urllib.request.urlopen(url, timeout=10) as response:  # noqa: S310 - https only, see above
            return json.load(response)

    discovery = get(f"{issuer}/.well-known/openid-configuration")
    if not isinstance(discovery, dict):
        raise TokenError("discovery document is not an object")
    jwks_uri = discovery.get("jwks_uri")
    if discovery.get("issuer") != issuer or not isinstance(jwks_uri, str) or not jwks_uri.startswith(f"{issuer}/"):
        raise TokenError("issuer discovery document does not match the configured issuer")
    jwks = get(jwks_uri)
    if not isinstance(jwks, dict):
        raise TokenError("JWKS is not an object")
    return jwks


KeyProvider = Callable[[datetime, datetime], DelegationKey]
JwksFetcher = Callable[[str], Mapping[str, Any]]


class Broker:
    def __init__(
        self, config: Config, key_provider: KeyProvider, jwks_fetcher: JwksFetcher = fetch_issuer_jwks
    ) -> None:
        self._config = config
        self._key_provider = key_provider
        self._jwks_fetcher = jwks_fetcher
        self._verifier = Verifier(config.issuer, config.audience, load_jwks(config.jwks))
        self._last_refresh = 0.0
        self._lock = threading.Lock()

    def _verify(self, token: str, now: float) -> dict[str, Any]:
        try:
            return self._verifier.verify(token, now)
        except UnknownKeyError:
            if not self._config.jwks_refresh:
                raise
            with self._lock:
                if now - self._last_refresh < _JWKS_REFRESH_INTERVAL_S:
                    raise
                self._last_refresh = now
                keys = load_jwks(self._jwks_fetcher(self._config.issuer))
                self._verifier = Verifier(self._config.issuer, self._config.audience, keys)
            return self._verifier.verify(token, now)

    def _grant_for(self, subject: str) -> Grant | None:
        """Exact subject, else an owner wildcard "dataset:<owner>/*" matching dataset:<owner>/<name>."""
        grant = self._config.grants.get(subject)
        if grant is not None:
            return grant
        for key, candidate in self._config.grants.items():
            if key.endswith("/*"):
                rest = subject.removeprefix(key[:-1])
                if rest != subject and rest and "/" not in rest:
                    return candidate
        return None

    def handle(self, authorization: str | None, now: float | None = None) -> tuple[int, dict[str, Any]]:
        now = time.time() if now is None else now
        scheme, _, token = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            return 401, {"error": "unauthorized"}
        try:
            claims = self._verify(token.strip(), now)
        except (TokenError, ValueError) as exc:
            log.warning("rejected token: %s", exc)
            return 401, {"error": "unauthorized"}

        subject = claims["sub"]
        grant = self._grant_for(subject)
        if grant is None:
            log.warning("valid token for unconfigured subject %r (jti=%s)", subject, claims.get("jti"))
            return 403, {"error": "forbidden"}

        issued_at = datetime.fromtimestamp(now, UTC).replace(microsecond=0)
        expiry = min(
            datetime.fromtimestamp(float(claims["exp"]), UTC).replace(microsecond=0),
            issued_at + timedelta(seconds=self._config.sas_max_ttl_s),
        )
        if expiry <= issued_at:
            return 401, {"error": "unauthorized"}
        start = issued_at - _START_SKEW
        key = self._key_provider(issued_at, expiry)
        sas = container_sas(self._config.account, grant.container, grant.access, start, expiry, key)
        log.info(
            "issued SAS %s",
            json.dumps(
                {
                    "sub": subject,
                    "access": grant.access,
                    "container": grant.container,
                    "expires_at": format_time(expiry),
                    "jti": claims.get("jti"),
                    "contract_id": claims.get("contract_id"),
                    "run_id": claims.get("run_id"),
                    "measurement_id": claims.get("measurement_id"),
                },
                sort_keys=True,
            ),
        )
        return 200, {
            "sas_url": f"{self._config.blob_endpoint}/{grant.container}?{sas}",
            "prefix": grant.prefix,
            "expires_at": format_time(expiry),
        }
