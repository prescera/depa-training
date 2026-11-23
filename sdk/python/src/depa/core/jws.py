"""JWS Compact Serialization with ECDSA (spec §2): ES256 signing, ES256/ES384 verification.

Verification is deliberately narrow: the protected header must contain ``alg``, ``kid`` and
the expected ``typ``; embedded keys (``jwk``, ``jku``, ``x5c``, ``x5u``), ``crit`` and
unencoded payloads (``b64``) are rejected; JSON with duplicate members is rejected.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

from depa.core.encoding import b64url_decode, b64url_encode, canonicalize, loads_object
from depa.core.keys import JWK, Signer, jwk_alg, public_key_from_jwk
from depa.core.timeutil import now_s
from depa.errors import CryptoError, ValidationError, VerificationError

_FORBIDDEN_HEADERS = frozenset({"jwk", "jku", "x5c", "x5u", "crit", "b64"})
_SIG_SIZES = {"ES256": 64, "ES384": 96}
_HASHES: dict[str, hashes.HashAlgorithm] = {"ES256": hashes.SHA256(), "ES384": hashes.SHA384()}
DEFAULT_ALGS: tuple[str, ...] = ("ES256",)
PEER_ALGS: tuple[str, ...] = ("ES256", "ES384")

KeyLookup = Callable[[str], JWK | None]


@dataclass(frozen=True)
class VerifiedJWS:
    header: dict[str, Any]
    payload: bytes

    def claims(self) -> dict[str, Any]:
        return loads_object(self.payload)


def _encode_header(header: Mapping[str, Any]) -> str:
    return b64url_encode(canonicalize(dict(header)))


def sign_compact(
    signer: Signer,
    payload: bytes,
    *,
    typ: str,
    detached: bool = False,
    extra_header: Mapping[str, Any] | None = None,
) -> str:
    header: dict[str, Any] = dict(extra_header or {})
    header.update({"alg": signer.alg, "kid": signer.kid, "typ": typ})
    protected = _encode_header(header)
    encoded_payload = b64url_encode(payload)
    signature = signer.sign(f"{protected}.{encoded_payload}".encode("ascii"))
    expected = _SIG_SIZES.get(signer.alg)
    if expected is None or len(signature) != expected:
        raise CryptoError("signer returned a malformed signature")
    return f"{protected}.{'' if detached else encoded_payload}.{b64url_encode(signature)}"


def sign_json(signer: Signer, claims: Mapping[str, Any], *, typ: str) -> str:
    """Sign a JSON object; the payload is its RFC 8785 canonical form."""
    return sign_compact(signer, canonicalize(dict(claims)), typ=typ)


def split_compact(token: str) -> tuple[str, str, str]:
    if not isinstance(token, str) or token.count(".") != 2 or len(token) > 1_000_000:
        raise VerificationError("malformed compact JWS", code="bad_jws")
    a, b, c = token.split(".")
    return a, b, c


def decode_header(token: str) -> dict[str, Any]:
    """Return the protected header WITHOUT verifying (only for key selection)."""
    protected, _, _ = split_compact(token)
    try:
        return loads_object(b64url_decode(protected))
    except ValidationError as exc:
        raise VerificationError("malformed JWS header", code="bad_jws") from exc


def decode_payload_unverified(token: str) -> dict[str, Any]:
    _, payload, _ = split_compact(token)
    try:
        return loads_object(b64url_decode(payload))
    except ValidationError as exc:
        raise VerificationError("malformed JWS payload", code="bad_jws") from exc


def jwks_lookup(jwks: Iterable[JWK]) -> KeyLookup:
    """Exact ``kid`` match against a key set. Duplicate kids make the set ambiguous and invalid."""
    table: dict[str, JWK] = {}
    for jwk in jwks:
        kid = jwk.get("kid")
        if not isinstance(kid, str) or not kid:
            raise ValidationError("JWKS entry without kid", code="bad_jwk")
        if kid in table:
            raise ValidationError(f"duplicate kid {kid!r} in JWKS", code="bad_jwk")
        table[kid] = jwk
    return table.get


def verify_compact(
    token: str,
    key_for_kid: KeyLookup,
    *,
    typ: str,
    detached_payload: bytes | None = None,
    algs: tuple[str, ...] = DEFAULT_ALGS,
) -> VerifiedJWS:
    protected_b64, payload_b64, sig_b64 = split_compact(token)
    try:
        header = loads_object(b64url_decode(protected_b64))
        signature = b64url_decode(sig_b64)
    except ValidationError as exc:
        raise VerificationError("malformed JWS", code="bad_jws") from exc
    if _FORBIDDEN_HEADERS & header.keys():
        raise VerificationError("JWS header contains a forbidden member", code="bad_jws")
    alg, kid, header_typ = header.get("alg"), header.get("kid"), header.get("typ")
    if alg not in algs:
        raise VerificationError(f"JWS alg {alg!r} not allowed", code="bad_jws_alg")
    if not isinstance(kid, str) or not kid:
        raise VerificationError("JWS header has no kid", code="bad_jws")
    if header_typ != typ:
        raise VerificationError(f"JWS typ {header_typ!r} != {typ!r}", code="bad_jws_typ")
    if detached_payload is not None:
        if payload_b64:
            raise VerificationError("expected a detached-payload JWS", code="bad_jws")
        payload = detached_payload
        payload_b64 = b64url_encode(payload)
    else:
        try:
            payload = b64url_decode(payload_b64)
        except ValidationError as exc:
            raise VerificationError("malformed JWS payload", code="bad_jws") from exc
    jwk = key_for_kid(kid)
    if jwk is None:
        raise VerificationError(f"unknown signing key {kid!r}", code="unknown_kid")
    try:
        key = public_key_from_jwk(jwk)
    except ValidationError as exc:
        raise VerificationError("signing key is invalid", code="bad_jwk") from exc
    if not isinstance(alg, str) or jwk_alg(jwk) != alg or len(signature) != _SIG_SIZES[alg]:
        raise VerificationError("JWS alg does not match the key", code="bad_jws_alg")
    half = len(signature) // 2
    der = encode_dss_signature(int.from_bytes(signature[:half], "big"), int.from_bytes(signature[half:], "big"))
    try:
        key.verify(der, f"{protected_b64}.{payload_b64}".encode("ascii"), ec.ECDSA(_HASHES[alg]))
    except InvalidSignature as exc:
        raise VerificationError("JWS signature is invalid", code="bad_signature") from exc
    return VerifiedJWS(header=header, payload=payload)


def check_jwt_times(claims: Mapping[str, Any], *, now: int | None = None, leeway_s: int = 60) -> None:
    """Validate ``exp`` (required), ``iat`` and ``nbf`` NumericDate claims."""
    current = now_s() if now is None else now
    exp = claims.get("exp")
    if not isinstance(exp, int) or isinstance(exp, bool):
        raise VerificationError("token has no numeric exp", code="bad_token")
    if current > exp + leeway_s:
        raise VerificationError("token expired", code="token_expired")
    for name in ("iat", "nbf"):
        value = claims.get(name)
        if value is None:
            continue
        if not isinstance(value, int) or isinstance(value, bool):
            raise VerificationError(f"token {name} is not numeric", code="bad_token")
        if value > current + leeway_s:
            raise VerificationError(f"token {name} is in the future", code="bad_token")
