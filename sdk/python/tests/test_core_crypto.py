"""JCS / digests, base64url, strict JSON, JWS ES256, HPKE (RFC 9180 vector) and signed requests."""

from __future__ import annotations

import hashlib
import json

import pytest

from depa.core import hpke
from depa.core.encoding import (
    b64url_decode,
    b64url_encode,
    canonicalize,
    digest,
    is_digest,
    loads_strict,
)
from depa.core.jws import (
    check_jwt_times,
    decode_header,
    jwks_lookup,
    sign_compact,
    sign_json,
    verify_compact,
)
from depa.core.keys import EcSigner, HpkeKeyPair, public_key_from_jwk, read_secret_file, write_secret_file
from depa.core.signed_request import build_signed_request, verify_signed_request
from depa.errors import ConfigError, CryptoError, ValidationError, VerificationError

# ---- JCS (RFC 8785) -------------------------------------------------------------------------


def test_jcs_sorts_keys_and_formats_numbers() -> None:
    assert canonicalize({"b": 1, "a": [True, None, "x"]}) == b'{"a":[true,null,"x"],"b":1}'
    # ES6 number formatting (RFC 8785 section 3.2.2.3)
    assert canonicalize([4.0, 1e-5, 1e21, 0.1, -0.0, 333333333.3333333]) == b"[4,0.00001,1e+21,0.1,0,333333333.3333333]"
    # UTF-16 code unit ordering of keys and minimal escaping
    assert canonicalize({"\u20ac": 1, "\r": 2, "1": 3}) == '{"\\r":2,"1":3,"\u20ac":1}'.encode()


def test_jcs_rfc8785_example() -> None:
    doc = {
        "numbers": [333333333.33333329, 1e30, 4.50, 2e-3, 0.000000000000000000000000001],
        "string": "\u20ac$\u000f\u000aA'\u0042\u0022\u005c\\\"/",
        "literals": [None, True, False],
    }
    expected = (
        '{"literals":[null,true,false],"numbers":[333333333.3333333,1e+30,4.5,0.002,1e-27],'
        '"string":"\u20ac$\\u000f\\nA\'B\\"\\\\\\\\\\"/"}'
    )
    assert canonicalize(doc) == expected.encode()


def test_digest_format_and_rejections() -> None:
    d = digest({"a": 1})
    assert is_digest(d) and d == "sha256:" + hashlib.sha256(b'{"a":1}').hexdigest()
    with pytest.raises(ValidationError):
        canonicalize(float("nan"))
    with pytest.raises(ValidationError):
        canonicalize(2**60)  # outside the I-JSON safe integer range


def test_b64url_strict() -> None:
    assert b64url_decode(b64url_encode(b"\xff\x00")) == b"\xff\x00"
    for bad in ("AA==", "A", "A+B/", "AB"[:1], "AC"):  # padding, length, std alphabet, non-canonical
        with pytest.raises(ValidationError):
            b64url_decode(bad)


def test_loads_strict_rejects_duplicates_and_nan() -> None:
    with pytest.raises(ValidationError):
        loads_strict('{"a":1,"a":2}')
    with pytest.raises(ValidationError):
        loads_strict('{"a":NaN}')


# ---- keys -------------------------------------------------------------------------------------


def test_private_jwk_roundtrip_and_public_validation() -> None:
    signer = EcSigner.generate("k1", "bank-a#k1")
    again = EcSigner.from_private_jwk(signer.private_jwk(), "bank-a#k1")
    assert again.public_jwk() == signer.public_jwk()
    bad = dict(signer.public_jwk(), y=signer.public_jwk()["x"])
    with pytest.raises(ValidationError):
        public_key_from_jwk(bad)  # not on the curve
    with pytest.raises(ValidationError):
        public_key_from_jwk(signer.private_jwk())  # private member present


def test_secret_files_are_0600_and_checked(tmp_path) -> None:
    path = tmp_path / "k" / "signing.jwk"
    write_secret_file(path, b"{}")
    assert path.stat().st_mode & 0o777 == 0o600
    assert read_secret_file(path) == b"{}"
    with pytest.raises(ConfigError):
        write_secret_file(path, b"{}")  # never overwrite silently
    path.chmod(0o644)
    with pytest.raises(ConfigError):
        read_secret_file(path)
    link = tmp_path / "link"
    link.symlink_to(path)
    with pytest.raises(ConfigError):
        read_secret_file(link)


# ---- JWS --------------------------------------------------------------------------------------


@pytest.fixture
def signer() -> EcSigner:
    return EcSigner.generate("k1")


def test_jws_roundtrip(signer: EcSigner) -> None:
    token = sign_json(signer, {"x": 1}, typ="t+jws")
    verified = verify_compact(token, jwks_lookup([signer.public_jwk()]), typ="t+jws")
    assert verified.claims() == {"x": 1}
    assert decode_header(token) == {"alg": "ES256", "kid": "k1", "typ": "t+jws"}


def test_jws_rejects_tampering(signer: EcSigner) -> None:
    keys = jwks_lookup([signer.public_jwk()])
    token = sign_json(signer, {"x": 1}, typ="t")
    h, p, s = token.split(".")
    with pytest.raises(VerificationError):
        forged = b64url_encode(b'{"x":2}')
        verify_compact(f"{h}.{forged}.{s}", keys, typ="t")
    with pytest.raises(VerificationError, match="typ"):
        verify_compact(token, keys, typ="other")
    other = EcSigner.generate("k1")
    with pytest.raises(VerificationError):
        verify_compact(token, jwks_lookup([other.public_jwk()]), typ="t")
    with pytest.raises(VerificationError):
        verify_compact(token, jwks_lookup([]), typ="t")
    none_header = b64url_encode(json.dumps({"alg": "none", "kid": "k1", "typ": "t"}).encode())
    with pytest.raises(VerificationError):
        verify_compact(f"{none_header}.{p}.", keys, typ="t")
    jwk_header = b64url_encode(
        json.dumps({"alg": "ES256", "kid": "k1", "typ": "t", "jwk": signer.public_jwk()}).encode()
    )
    with pytest.raises(VerificationError):
        verify_compact(f"{jwk_header}.{p}.{s}", keys, typ="t")


def test_jws_detached(signer: EcSigner) -> None:
    token = sign_compact(signer, b"payload", typ="d", detached=True)
    assert token.split(".")[1] == ""
    keys = jwks_lookup([signer.public_jwk()])
    verify_compact(token, keys, typ="d", detached_payload=b"payload")
    with pytest.raises(VerificationError):
        verify_compact(token, keys, typ="d", detached_payload=b"payloaX")


def test_es384_verification_only_when_allowed() -> None:
    from cryptography.hazmat.primitives.asymmetric import ec

    s384 = EcSigner(ec.generate_private_key(ec.SECP384R1()), "p384")
    token = sign_json(s384, {"a": 1}, typ="t")
    keys = jwks_lookup([s384.public_jwk()])
    with pytest.raises(VerificationError):
        verify_compact(token, keys, typ="t")
    assert verify_compact(token, keys, typ="t", algs=("ES256", "ES384")).claims() == {"a": 1}


def test_jwt_times() -> None:
    check_jwt_times({"exp": 1000, "iat": 900}, now=1000)
    with pytest.raises(VerificationError):
        check_jwt_times({"exp": 100}, now=1000)
    with pytest.raises(VerificationError):
        check_jwt_times({"iat": 5000, "exp": 6000}, now=1000)
    with pytest.raises(VerificationError):
        check_jwt_times({}, now=1000)


# ---- HPKE (RFC 9180 test vector: mode 0, KEM 0x0020, KDF 0x0001, AEAD 0x0002) ----------------
# From the CFRG test-vectors.json accompanying RFC 9180 (the RFC appendix only prints the
# AES-128-GCM / ChaCha20 suites; this is the official vector for the DEPA suite).

VECTOR = {
    "info": "4f6465206f6e2061204772656369616e2055726e",
    "ikmE": "2cd7c601cefb3d42a62b04b7a9041494c06c7843818e0ce28a8f704ae7ab20f9",
    "skRm": "497b4502664cfea5d5af0b39934dac72242a74f8480451e1aee7d6a53320333d",
    "pkRm": "430f4b9859665145a6b1ba274024487bd66f03a2dd577d7753c68d7d7d00c00c",
    "enc": "6c93e09869df3402d7bf231bf540fadd35cd56be14f97178f0954db94b7fc256",
    "aad": "436f756e742d30",
    "pt": "4265617574792069732074727574682c20747275746820626561757479",
    "ct": "e5d84cd531cfb583096e7cfa9641bd3079cf3a91cda813c52deb5f512be9931980a41de125a925cdad859d5b7a",
}


def test_hpke_rfc9180_vector_seal_and_open() -> None:
    v = {k: bytes.fromhex(x) for k, x in VECTOR.items()}
    assert (hpke.KEM_ID, hpke.KDF_ID, hpke.AEAD_ID) == (0x20, 0x01, 0x02)
    assert HpkeKeyPair.from_private_bytes("r", v["skRm"]).public_key == v["pkRm"]
    enc, ct = hpke.seal_base(v["pkRm"], info=v["info"], aad=v["aad"], plaintext=v["pt"], _ephemeral_ikm=v["ikmE"])
    assert enc == v["enc"] and ct == v["ct"]
    assert hpke.open_base(v["skRm"], enc=v["enc"], info=v["info"], aad=v["aad"], ciphertext=v["ct"]) == v["pt"]


def test_hpke_failures() -> None:
    pair = HpkeKeyPair.generate("h1")
    enc, ct = hpke.seal_base(pair.public_key, info=b"i", aad=b"", plaintext=b"k" * 32)
    with pytest.raises(CryptoError):
        hpke.open_base(pair.private_key, enc=enc, info=b"other", aad=b"", ciphertext=ct)
    with pytest.raises(CryptoError):
        hpke.open_base(HpkeKeyPair.generate("x").private_key, enc=enc, info=b"i", aad=b"", ciphertext=ct)
    with pytest.raises(CryptoError):
        hpke.open_base(pair.private_key, enc=enc, info=b"i", aad=b"", ciphertext=ct[:-1] + bytes([ct[-1] ^ 1]))


# ---- signed requests (§3.2) -----------------------------------------------------------------


def test_signed_request_roundtrip_and_checks() -> None:
    signer = EcSigner.generate("k1", "bank-a#k1")
    lookup = jwks_lookup([dict(signer.public_jwk(), kid="bank-a#k1")])
    req = build_signed_request(signer, aud="depa-kms", op="dataset.create", body={"dataset_id": "bank-a/x"}, iat=1000)
    payload = json.loads(b64url_decode(req["jws"].split(".")[1]))
    assert set(payload) == {"aud", "op", "iat", "jti", "body"}
    assert decode_header(req["jws"])["typ"] == "depa-request+jws"
    ok = verify_signed_request(req, lookup, aud="depa-kms", op="dataset.create", now=1100)
    assert ok.kid == "bank-a#k1" and ok.body == {"dataset_id": "bank-a/x"}
    with pytest.raises(VerificationError):
        verify_signed_request(req, lookup, aud="depa-contract-service", op="dataset.create", now=1100)
    with pytest.raises(VerificationError):
        verify_signed_request(req, lookup, aud="depa-kms", op="dataset.rotate", now=1100)
    with pytest.raises(VerificationError):
        verify_signed_request(req, lookup, aud="depa-kms", op="dataset.create", now=1301)
