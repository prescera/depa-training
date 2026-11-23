"""``gcp-cs-tdx`` attestation: Google Cloud Attestation token from Confidential Space (spec §9.2).

The Confidential Space launcher exposes a local token server on the unix socket
``/run/container_launcher/teeserver.sock``. ``POST /v1/token`` with
``{"audience": <service base URL>, "token_type": "OIDC", "nonces": [hex(binding_hash)]}``
returns a raw OIDC JWT signed by Google Cloud Attestation (``iss =
https://confidentialcomputing.googleapis.com``) whose ``eat_nonce`` carries the nonce. The token
travels as ``Authorization: Bearer`` (envelope ``{"platform": "gcp-cs-tdx"}``); the services
verify signature, issuer, audience, hardware/software claims and the nonce.

Here we only sanity-check the (unverified) token so a misbehaving launcher is caught early with
a clear error; nothing security-relevant depends on these local checks. Tokens are cached per
``(binding_hash, audience)`` until 60 s before expiry.
"""

from __future__ import annotations

import time
from collections.abc import Callable

import httpx

from depa.attestation import Evidence
from depa.attestation.virtual import check_binding_hash
from depa.core.jws import decode_payload_unverified
from depa.errors import AttestationError, VerificationError

TEESERVER_SOCKET = "/run/container_launcher/teeserver.sock"
TOKEN_URL = "http://localhost/v1/token"  # noqa: S105 - endpoint path, not a secret
GCA_ISSUER = "https://confidentialcomputing.googleapis.com"
MAX_TOKEN_BYTES = 64 << 10
REFRESH_MARGIN_S = 60


class GcpConfidentialSpaceProvider:
    platform = "gcp-cs-tdx"

    def __init__(
        self,
        *,
        socket_path: str = TEESERVER_SOCKET,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.time,
        timeout_s: float = 30.0,
    ) -> None:
        self._socket_path = socket_path
        self._transport = transport
        self._clock = clock
        self._timeout_s = timeout_s
        self._cache: dict[tuple[bytes, str], tuple[str, int]] = {}

    def _request_token(self, audience: str, nonce: str) -> str:
        transport = self._transport or httpx.HTTPTransport(uds=self._socket_path)
        body = {"audience": audience, "token_type": "OIDC", "nonces": [nonce]}
        try:
            with httpx.Client(transport=transport, timeout=self._timeout_s, follow_redirects=False) as client:
                response = client.post(TOKEN_URL, json=body)
        except httpx.HTTPError as exc:
            raise AttestationError(
                f"Confidential Space token request failed: {type(exc).__name__}", code="teeserver"
            ) from exc
        if response.status_code != 200:
            raise AttestationError(
                f"Confidential Space token request failed: HTTP {response.status_code}", code="teeserver"
            )
        if len(response.content) > MAX_TOKEN_BYTES:
            raise AttestationError("Confidential Space token too large", code="teeserver")
        try:
            return response.content.decode("ascii").strip()
        except UnicodeDecodeError as exc:
            raise AttestationError("Confidential Space token is not ASCII", code="bad_token") from exc

    @staticmethod
    def _check_token(token: str, audience: str, nonce: str) -> int:
        try:
            claims = decode_payload_unverified(token)
        except VerificationError as exc:
            raise AttestationError("Confidential Space returned a malformed JWT", code="bad_token") from exc
        if claims.get("iss") != GCA_ISSUER:
            raise AttestationError("attestation token has an unexpected issuer", code="bad_token")
        aud = claims.get("aud")
        if aud != audience and not (isinstance(aud, list) and audience in aud):
            raise AttestationError("attestation token audience mismatch", code="bad_token")
        eat_nonce = claims.get("eat_nonce")
        nonces = eat_nonce if isinstance(eat_nonce, list) else [eat_nonce]
        if nonce not in nonces:
            raise AttestationError("attestation token does not carry the binding nonce", code="bad_token")
        exp = claims.get("exp")
        if not isinstance(exp, int) or isinstance(exp, bool):
            raise AttestationError("attestation token has no numeric exp", code="bad_token")
        return exp

    def evidence(self, binding_hash: bytes, audience: str) -> Evidence:
        digest = check_binding_hash(binding_hash)
        if not isinstance(audience, str) or not audience.startswith("https://"):
            raise AttestationError("audience must be the https service base URL", code="bad_audience")
        key = (digest, audience)
        now = self._clock()
        cached = self._cache.get(key)
        if cached is not None and cached[1] - REFRESH_MARGIN_S > now:
            return Evidence(envelope={"platform": "gcp-cs-tdx"}, bearer_token=cached[0])
        nonce = digest.hex()
        token = self._request_token(audience, nonce)
        exp = self._check_token(token, audience, nonce)
        if exp - REFRESH_MARGIN_S <= now:
            raise AttestationError("attestation token is already (nearly) expired", code="bad_token")
        self._cache[key] = (token, exp)
        return Evidence(envelope={"platform": "gcp-cs-tdx"}, bearer_token=token)
