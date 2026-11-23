import base64
import json

import pytest
from conftest import AUDIENCE, ISSUER, NOW, Signer, b64url

from broker.token import TokenError, UnknownKeyError, Verifier, load_jwks


def verifier(*signers: Signer, **kwargs) -> Verifier:
    return Verifier(ISSUER, AUDIENCE, load_jwks({"keys": [s.jwk() for s in signers]}), **kwargs)


def test_valid_token(signer):
    claims = verifier(signer).verify(signer.token(), NOW)
    assert claims["sub"] == "dataset:bank-a/credit-applications"


def test_audience_list_accepted(signer):
    assert verifier(signer).verify(signer.token(aud=["other", AUDIENCE]), NOW)["aud"][1] == AUDIENCE


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"iss": "https://evil.example"}, "wrong issuer"),
        ({"aud": "sts.amazonaws.com"}, "wrong audience"),
        ({"aud": ["sts.amazonaws.com"]}, "wrong audience"),
        ({"exp": NOW - 31}, "expired"),
        ({"exp": None}, "missing exp"),
        ({"exp": "soon"}, "NumericDate"),
        ({"exp": True}, "NumericDate"),
        ({"nbf": NOW + 31}, "not yet valid"),
        ({"iat": NOW + 31, "nbf": None}, "issued in the future"),
        ({"nbf": NOW - 10, "exp": NOW + 3700}, "lifetime"),
        ({"sub": None}, "sub missing"),
        ({"sub": 7}, "sub missing"),
    ],
)
def test_claim_checks(signer, overrides, reason):
    with pytest.raises(TokenError, match=reason):
        verifier(signer).verify(signer.token(**overrides), NOW)


def test_leeway_allows_small_skew(signer):
    assert verifier(signer).verify(signer.token(exp=NOW - 29, nbf=NOW + 29, iat=NOW - 30), NOW)


@pytest.mark.parametrize("alg", ["none", "HS256", "RS256", "ES384", "PS256"])
def test_only_es256(signer, alg):
    with pytest.raises(TokenError, match="ES256"):
        verifier(signer).verify(signer.token(header={"alg": alg}), NOW)


def test_crit_rejected(signer):
    with pytest.raises(TokenError, match="crit"):
        verifier(signer).verify(signer.token(header={"crit": ["exp"]}), NOW)


def test_signature_from_other_key_rejected(signer):
    impostor = Signer(kid=signer.kid)
    with pytest.raises(TokenError, match="bad signature"):
        verifier(signer).verify(impostor.token(), NOW)


def test_tampered_payload_rejected(signer):
    h, _, s = signer.token().split(".")
    forged = b64url(json.dumps({"iss": ISSUER, "aud": AUDIENCE, "sub": "output:x", "exp": NOW + 60}).encode())
    with pytest.raises(TokenError, match="bad signature"):
        verifier(signer).verify(f"{h}.{forged}.{s}", NOW)


def test_unknown_kid(signer):
    with pytest.raises(UnknownKeyError):
        verifier(signer).verify(Signer(kid="k2").token(), NOW)


def test_der_signature_rejected(signer):
    h, p, s = signer.token().split(".")
    raw = base64.urlsafe_b64decode(s + "==")
    with pytest.raises(TokenError, match="64 bytes"):
        verifier(signer).verify(f"{h}.{p}.{b64url(b'0' + raw)}", NOW)


@pytest.mark.parametrize("token", ["", "a.b", "a.b.c.d", "!!.e30.AA", "e30.e30." + "A" * 86, "W10.e30." + "A" * 86])
def test_malformed(signer, token):
    with pytest.raises(TokenError):
        verifier(signer).verify(token, NOW)


def test_oversized(signer):
    with pytest.raises(TokenError, match="too large"):
        verifier(signer).verify("a" * 9000, NOW)


def test_jwks_loader_skips_non_es256_and_rejects_bad_keys(signer):
    rsa = {"kty": "RSA", "kid": "r1", "n": "AQAB", "e": "AQAB"}
    assert list(load_jwks({"keys": [rsa, signer.jwk()]})) == ["k1"]
    with pytest.raises(ValueError, match="no ES256"):
        load_jwks({"keys": [rsa]})
    with pytest.raises(ValueError, match="duplicated"):
        load_jwks({"keys": [signer.jwk(), signer.jwk()]})
    off_curve = signer.jwk() | {"y": b64url((1).to_bytes(32, "big"))}
    with pytest.raises(ValueError):
        load_jwks({"keys": [off_curve]})
    with pytest.raises(ValueError, match="32 bytes"):
        load_jwks({"keys": [signer.jwk() | {"x": b64url(b"\x01" * 31)}]})
