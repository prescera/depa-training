"""Signed requests (spec §3.2): ``{"jws": <compact JWS>}`` over ``{aud, op, iat, jti, body}``."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from depa.core.jws import KeyLookup, sign_json, verify_compact
from depa.core.keys import Signer
from depa.core.timeutil import now_s
from depa.errors import VerificationError

REQUEST_TYP = "depa-request+jws"
MAX_SKEW_S = 300


def build_signed_request(
    signer: Signer,
    *,
    aud: str,
    op: str,
    body: Mapping[str, Any],
    iat: int | None = None,
    jti: str | None = None,
) -> dict[str, str]:
    payload = {
        "aud": aud,
        "op": op,
        "iat": now_s() if iat is None else iat,
        "jti": jti or str(uuid.uuid4()),
        "body": dict(body),
    }
    return {"jws": sign_json(signer, payload, typ=REQUEST_TYP)}


@dataclass(frozen=True)
class SignedRequest:
    kid: str
    aud: str
    op: str
    iat: int
    jti: str
    body: dict[str, Any]
    raw: str


def verify_signed_request(
    request: Mapping[str, Any],
    key_for_kid: KeyLookup,
    *,
    aud: str,
    op: str,
    now: int | None = None,
) -> SignedRequest:
    """Service-side verification (used by mock services in tests and by offline audit tools).

    Replay (``jti``) tracking and role checks are the caller's responsibility.
    """
    token = request.get("jws")
    if not isinstance(token, str):
        raise VerificationError("signed request has no jws", code="bad_request")
    verified = verify_compact(token, key_for_kid, typ=REQUEST_TYP)
    claims = verified.claims()
    if claims.get("aud") != aud:
        raise VerificationError("signed request aud mismatch", code="bad_aud")
    if claims.get("op") != op:
        raise VerificationError("signed request op mismatch", code="bad_op")
    iat = claims.get("iat")
    current = now_s() if now is None else now
    if not isinstance(iat, int) or isinstance(iat, bool) or abs(current - iat) > MAX_SKEW_S:
        raise VerificationError("signed request iat outside the allowed window", code="bad_iat")
    jti, body = claims.get("jti"), claims.get("body")
    if not isinstance(jti, str) or not jti or not isinstance(body, dict):
        raise VerificationError("signed request is missing jti/body", code="bad_request")
    return SignedRequest(str(verified.header["kid"]), aud, op, iat, jti, body, token)
