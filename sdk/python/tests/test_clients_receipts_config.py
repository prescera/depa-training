"""Service clients (pinning, retries, error mapping, wire shapes), CCF receipts, CCR config."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import ssl

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils
from cryptography.x509.oid import NameOID

from depa.ccr.config import CcrConfig, TrustBundle
from depa.clients.http import ServiceEndpoint, pinned_ssl_context
from depa.clients.kms import KmsClient, dataset_path
from depa.core.encoding import b64_encode, b64url_encode, canonicalize, loads_strict
from depa.core.keys import EcSigner
from depa.errors import ConfigError, NotFoundError, ServiceError, VerificationError
from depa.receipts import verify_ccf_receipt


def _cert(subject: str, key: ec.EllipticCurvePrivateKey, issuer_key=None, issuer_name=None, ca=False):
    now = dt.datetime.now(dt.UTC)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject)])
    builder = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(issuer_name or name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + dt.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), True)
    )
    return builder.sign(issuer_key or key, hashes.SHA256())


# ---- HTTP layer ---------------------------------------------------------------------------------


def test_endpoint_requires_https() -> None:
    with pytest.raises(ConfigError):
        ServiceEndpoint("depa-kms", "http://127.0.0.1:1/app")


def test_pinned_context_trusts_only_the_pin() -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    pem = _cert("svc", key, ca=True).public_bytes(serialization.Encoding.PEM).decode()
    ctx = pinned_ssl_context(pem)
    assert ctx.check_hostname and ctx.verify_mode == ssl.CERT_REQUIRED
    assert len(ctx.get_ca_certs()) == 1
    with pytest.raises(ConfigError):
        pinned_ssl_context("not a cert")


def _client(handler, retries: int = 2) -> KmsClient:
    return KmsClient(
        ServiceEndpoint("depa-kms", "https://kms.test/app"),
        transport=httpx.MockTransport(handler),
        retries=retries,
        backoff_s=0,
    )


def test_retries_idempotent_gets_and_maps_errors() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if len(calls) < 3:
            return httpx.Response(503)
        return httpx.Response(404, json={"error": {"code": "not_found", "message": "nope"}})

    with pytest.raises(NotFoundError) as e:
        _client(handler).get_dataset("bank-a/loans")
    assert e.value.remote_code == "not_found" and len(calls) == 3
    assert calls[0] == "/app/datasets/bank-a/loans"


def test_posts_are_not_retried_after_sending() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(503)

    with pytest.raises(ServiceError):
        _client(handler).create_dataset(EcSigner.generate("k1", "bank-a#k1"), "bank-a/loans", "d")
    assert len(calls) == 1


def test_signed_bodies_bind_path_parameters() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        payload = loads_strict(
            __import__("base64").urlsafe_b64decode(json.loads(request.content)["jws"].split(".")[1] + "==")
        )
        seen[request.url.path] = payload["body"]
        return httpx.Response(200, json={"proposal_id": "p", "state": "open", "dataset_id": "bank-a/loans", "keys": []})

    client = _client(handler)
    signer = EcSigner.generate("k1", "bank-a#k1")
    client.gov_vote(signer, "sha256:" + "a" * 64, "approve")
    client.set_policy(signer, "bank-a/loans", {"version": 1})
    assert seen["/app/gov/proposals/sha256:" + "a" * 64 + "/ballots"]["proposal_id"] == "sha256:" + "a" * 64
    assert seen["/app/datasets/bank-a/loans/policy"] == {"dataset_id": "bank-a/loans", "policy": {"version": 1}}
    assert dataset_path("bank-a/x.y") == "/datasets/bank-a/x.y"


def test_response_size_cap() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"[" + b"1," * (17 << 20) + b"1]")

    with pytest.raises(ServiceError, match="too large"):
        _client(handler).http.get("/x")


# ---- CCF receipts ---------------------------------------------------------------------------------


def _receipt(claims: object):
    service_key = ec.generate_private_key(ec.SECP384R1())
    service_cert = _cert("service", service_key, ca=True)
    node_key = ec.generate_private_key(ec.SECP256R1())
    node_cert = _cert("node", node_key, issuer_key=service_key, issuer_name=service_cert.subject)
    write_set, commit_evidence = hashlib.sha256(b"ws").hexdigest(), "ce:2.10:abc"
    claims_digest = hashlib.sha256(canonicalize(claims)).hexdigest()
    leaf = hashlib.sha256(
        bytes.fromhex(write_set) + hashlib.sha256(commit_evidence.encode()).digest() + bytes.fromhex(claims_digest)
    ).digest()
    left, right = hashlib.sha256(b"L").digest(), hashlib.sha256(b"R").digest()
    root = hashlib.sha256(left + hashlib.sha256(leaf + right).digest()).digest()
    signature = node_key.sign(root, ec.ECDSA(utils.Prehashed(hashes.SHA256())))
    receipt = {
        "cert": node_cert.public_bytes(serialization.Encoding.PEM).decode(),
        "leaf_components": {
            "claims_digest": claims_digest,
            "commit_evidence": commit_evidence,
            "write_set_digest": write_set,
        },
        "node_id": "n",
        "proof": [{"right": right.hex()}, {"left": left.hex()}],
        "signature": b64_encode(signature),
    }
    return receipt, service_cert.public_bytes(serialization.Encoding.PEM).decode()


def test_ccf_receipt_verifies_and_detects_tampering() -> None:
    receipt, service_pem = _receipt({"run_id": "r"})
    verify_ccf_receipt(receipt, service_pem, expected_claims={"run_id": "r"})
    with pytest.raises(VerificationError):
        verify_ccf_receipt(receipt, service_pem, expected_claims={"run_id": "other"})
    bad = json.loads(json.dumps(receipt))
    bad["leaf_components"]["write_set_digest"] = "00" * 32
    with pytest.raises(VerificationError):
        verify_ccf_receipt(bad, service_pem)
    _, other_service = _receipt({})
    with pytest.raises(VerificationError):
        verify_ccf_receipt(receipt, other_service)


# ---- CCR configuration / trust bundle -------------------------------------------------------------


def _bundle(cs_url: str, kms_url: str) -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    pem = _cert("svc", key, ca=True).public_bytes(serialization.Encoding.PEM).decode()
    jwk = EcSigner.generate("k").public_jwk()
    doc = {
        "v": 1,
        "cs": {"url": cs_url, "service_cert_pem": pem, "jwks": [jwk]},
        "kms": {"url": kms_url, "service_cert_pem": pem},
    }
    return b64url_encode(canonicalize(doc))


ENV = {
    "DEPA_CONTRACT_ID": "0b7f0d4e-6a3a-4d6e-9c55-2f1b8a7c1e00",
    "DEPA_RECIPE_DIGEST": "sha256:" + "a" * 64,
    "DEPA_CS_URL": "https://cs.test/app",
    "DEPA_KMS_URL": "https://kms.test/app",
}


def test_config_requires_trust_bundle_on_production() -> None:
    with pytest.raises(ConfigError, match="TRUST_BUNDLE"):
        CcrConfig.from_env(ENV | {"DEPA_PLATFORM": "aci-snp"})
    cfg = CcrConfig.from_env(
        ENV | {"DEPA_PLATFORM": "aci-snp", "DEPA_TRUST_BUNDLE": _bundle("https://cs.test/app", "https://kms.test/app")}
    )
    assert cfg.trust_bundle is not None and cfg.trust_bundle.cs.jwks is not None
    assert cfg.cs_endpoint().ca_pem == cfg.trust_bundle.cs.service_cert_pem


def test_config_rejects_mismatched_bundle_and_bad_values() -> None:
    with pytest.raises(ConfigError):
        CcrConfig.from_env(
            ENV
            | {
                "DEPA_PLATFORM": "gcp-cs-tdx",
                "DEPA_TRUST_BUNDLE": _bundle("https://evil.test/app", "https://kms.test/app"),
            }
        )
    for key, value in (
        ("DEPA_PLATFORM", "sgx"),
        ("DEPA_RECIPE_DIGEST", "md5:x"),
        ("DEPA_CONTRACT_ID", "1"),
        ("DEPA_CS_URL", "http://cs.test"),
    ):
        with pytest.raises(ConfigError):
            CcrConfig.from_env(ENV | {"DEPA_PLATFORM": "virtual", key: value})
    with pytest.raises(ConfigError):
        TrustBundle.parse(b64url_encode(b'{"v":2}'))
