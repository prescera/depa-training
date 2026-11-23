"""ES256 JWT verification against a pinned JWKS (stdlib + cryptography only)."""

from __future__ import annotations

import base64
import binascii
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.hashes import SHA256

_B64URL_RE = re.compile(r"^[A-Za-z0-9_-]*$")
MAX_TOKEN_BYTES = 8192


class TokenError(Exception):
    """Any reason to reject a token. The message is for logs only, never for the caller."""


class UnknownKeyError(TokenError):
    """The token names a kid that is not in the pinned key set."""


def b64url_decode(value: str) -> bytes:
    if not _B64URL_RE.fullmatch(value) or len(value) % 4 == 1:
        raise TokenError("malformed base64url")
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (binascii.Error, ValueError) as exc:
        raise TokenError("malformed base64url") from exc


def _json_object(raw: bytes, what: str) -> dict[str, Any]:
    try:
        obj = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TokenError(f"{what} is not JSON") from exc
    if not isinstance(obj, dict):
        raise TokenError(f"{what} is not an object")
    return obj


def load_jwks(jwks: Mapping[str, Any]) -> dict[str, ec.EllipticCurvePublicKey]:
    """P-256 signing keys of a JWKS, by kid. Raises ValueError on anything unexpected."""
    keys = jwks.get("keys")
    if not isinstance(keys, list) or not keys:
        raise ValueError("JWKS has no keys")
    out: dict[str, ec.EllipticCurvePublicKey] = {}
    for jwk in keys:
        if not isinstance(jwk, dict):
            raise ValueError("JWK is not an object")
        if jwk.get("kty") != "EC" or jwk.get("crv") != "P-256":
            continue  # only ES256 keys are usable; ignore others
        if jwk.get("use", "sig") != "sig" or jwk.get("alg", "ES256") != "ES256":
            continue
        kid = jwk.get("kid")
        if not isinstance(kid, str) or not kid or kid in out:
            raise ValueError("JWK kid missing or duplicated")
        try:
            x = b64url_decode(jwk["x"])
            y = b64url_decode(jwk["y"])
        except (KeyError, TypeError, TokenError) as exc:
            raise ValueError(f"JWK {kid} has invalid coordinates") from exc
        if len(x) != 32 or len(y) != 32:
            raise ValueError(f"JWK {kid} coordinates must be 32 bytes")
        numbers = ec.EllipticCurvePublicNumbers(int.from_bytes(x, "big"), int.from_bytes(y, "big"), ec.SECP256R1())
        out[kid] = numbers.public_key()  # validates the point is on the curve
    if not out:
        raise ValueError("JWKS has no ES256 P-256 signing key")
    return out


def _numeric_date(claims: Mapping[str, Any], name: str) -> float | None:
    value = claims.get(name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TokenError(f"{name} is not a NumericDate")
    return float(value)


@dataclass(frozen=True)
class Verifier:
    issuer: str
    audience: str
    keys: Mapping[str, ec.EllipticCurvePublicKey]
    leeway_s: int = 30
    max_lifetime_s: int = 3600

    def verify(self, token: str, now: float) -> dict[str, Any]:
        """Return the claims of a valid token or raise TokenError."""
        if len(token) > MAX_TOKEN_BYTES:
            raise TokenError("token too large")
        parts = token.split(".")
        if len(parts) != 3:
            raise TokenError("not a compact JWS")
        header = _json_object(b64url_decode(parts[0]), "header")
        if header.get("alg") != "ES256":
            raise TokenError("alg must be ES256")
        if "crit" in header:
            raise TokenError("crit header not supported")
        kid = header.get("kid")
        if not isinstance(kid, str):
            raise TokenError("kid missing")
        key = self.keys.get(kid)
        if key is None:
            raise UnknownKeyError(f"unknown kid {kid!r}")
        signature = b64url_decode(parts[2])
        if len(signature) != 64:
            raise TokenError("ES256 signature must be 64 bytes")
        der = encode_dss_signature(int.from_bytes(signature[:32], "big"), int.from_bytes(signature[32:], "big"))
        try:
            key.verify(der, f"{parts[0]}.{parts[1]}".encode("ascii"), ec.ECDSA(SHA256()))
        except InvalidSignature as exc:
            raise TokenError("bad signature") from exc

        claims = _json_object(b64url_decode(parts[1]), "payload")
        if claims.get("iss") != self.issuer:
            raise TokenError("wrong issuer")
        aud = claims.get("aud")
        if not (aud == self.audience or (isinstance(aud, list) and self.audience in aud)):
            raise TokenError("wrong audience")
        exp = _numeric_date(claims, "exp")
        nbf = _numeric_date(claims, "nbf")
        iat = _numeric_date(claims, "iat")
        if exp is None:
            raise TokenError("missing exp")
        if now >= exp + self.leeway_s:
            raise TokenError("expired")
        if nbf is not None and now < nbf - self.leeway_s:
            raise TokenError("not yet valid")
        if iat is not None and iat > now + self.leeway_s:
            raise TokenError("issued in the future")
        start = nbf if nbf is not None else iat
        if start is not None and exp - start > self.max_lifetime_s:
            raise TokenError("lifetime exceeds the allowed maximum")
        if not isinstance(claims.get("sub"), str):
            raise TokenError("sub missing")
        return claims
