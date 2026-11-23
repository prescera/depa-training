"""Verification of Execution Receipts (spec §9.5), key-release receipts (§9.4) and CCF ledger
receipts (``GET /receipt?transaction_id=``).

A CCF receipt proves a transaction is in the ledger: the leaf
``SHA-256(write_set_digest || SHA-256(commit_evidence) || claims_digest)`` is folded with the
Merkle proof into the root, which the node signed (ECDSA, prehashed SHA-256); the node
certificate must chain to the pinned service certificate (optionally through
``service_endorsements`` after a recovery). The services bind their signed claims to the
transaction with ``claims_digest = SHA-256(JCS(claims))``.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, utils

from depa.core.encoding import b64_decode, canonicalize, digest, sha256_hex
from depa.core.jws import KeyLookup, verify_compact
from depa.errors import DepaError, VerificationError

EXEC_RECEIPT_TYP = "depa-exec-receipt+jwt"
RELEASE_RECEIPT_TYP = "depa-key-release+jwt"


@dataclass(frozen=True)
class ReceiptCheck:
    claims: dict[str, Any]
    problems: list[str]
    signed_claims: dict[str, Any]

    @property
    def ok(self) -> bool:
        return not self.problems


def verify_execution_receipt(receipt_jws: str, cs_keys: KeyLookup, kms_keys: KeyLookup) -> ReceiptCheck:
    """Verify the CS-signed receipt (``{iss, iat, sub, receipt_digest, receipt}``) and every embedded
    KMS release receipt against the run. ``ReceiptCheck.claims`` is the inner Execution Receipt."""
    signed = verify_compact(receipt_jws, cs_keys, typ=EXEC_RECEIPT_TYP).claims()
    claims = signed.get("receipt")
    if not isinstance(claims, dict):
        raise VerificationError("execution receipt JWS carries no receipt", code="bad_receipt")
    problems: list[str] = []
    run_id, contract_id = claims.get("run_id"), claims.get("contract_id")
    if signed.get("receipt_digest") != digest(claims):
        problems.append("receipt_digest does not match the receipt")
    if signed.get("sub") != run_id:
        problems.append("receipt subject is not its run")
    if claims.get("status") != "succeeded":
        problems.append(f"receipt status is {claims.get('status')!r}")
    for item in claims.get("inputs", []):
        dataset_id = item.get("dataset_id") if isinstance(item, dict) else None
        try:
            release = verify_compact(str(item.get("release_receipt")), kms_keys, typ=RELEASE_RECEIPT_TYP).claims()
        except DepaError as exc:
            problems.append(f"{dataset_id}: release receipt invalid: {exc}")
            continue
        if (
            release.get("run_id") != run_id
            or release.get("contract_id") != contract_id
            or release.get("dataset_id") != dataset_id
            or release.get("measurement_id") != claims.get("measurement_id")
            or release.get("platform") != claims.get("platform")
        ):
            problems.append(f"{dataset_id}: release receipt does not belong to this run")
    if not claims.get("inputs"):
        problems.append("receipt lists no inputs")
    return ReceiptCheck(claims, problems, signed)


def _sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def ccf_receipt_root(receipt: Mapping[str, Any]) -> bytes:
    leaf_components = receipt.get("leaf_components")
    if not isinstance(leaf_components, dict):
        raise VerificationError("receipt has no leaf_components", code="bad_receipt")
    try:
        write_set = bytes.fromhex(str(leaf_components["write_set_digest"]))
        claims = bytes.fromhex(str(leaf_components["claims_digest"]))
        commit_evidence = str(leaf_components["commit_evidence"]).encode()
    except (KeyError, ValueError) as exc:
        raise VerificationError("malformed receipt leaf components", code="bad_receipt") from exc
    current = _sha256(write_set + _sha256(commit_evidence) + claims)
    for step in receipt.get("proof", []):
        if not isinstance(step, dict) or len(step) != 1:
            raise VerificationError("malformed receipt proof step", code="bad_receipt")
        ((side, value),) = step.items()
        sibling = bytes.fromhex(str(value))
        if side == "left":
            current = _sha256(sibling + current)
        elif side == "right":
            current = _sha256(current + sibling)
        else:
            raise VerificationError("malformed receipt proof step", code="bad_receipt")
    return current


def _check_signed_by(cert: x509.Certificate, issuer: x509.Certificate) -> None:
    pub = issuer.public_key()
    if not isinstance(pub, ec.EllipticCurvePublicKey) or cert.signature_hash_algorithm is None:
        raise VerificationError("unsupported certificate key type", code="bad_receipt")
    try:
        pub.verify(cert.signature, cert.tbs_certificate_bytes, ec.ECDSA(cert.signature_hash_algorithm))
    except InvalidSignature as exc:
        raise VerificationError("node certificate is not endorsed by the service", code="bad_receipt") from exc


def verify_ccf_receipt(receipt: Mapping[str, Any], service_cert_pem: str, *, expected_claims: Any = None) -> bytes:
    """Verify a CCF receipt against the pinned service certificate; returns the Merkle root."""
    root = ccf_receipt_root(receipt)
    try:
        node_cert = x509.load_pem_x509_certificate(str(receipt["cert"]).encode())
        service_cert = x509.load_pem_x509_certificate(service_cert_pem.encode())
        signature = b64_decode(str(receipt["signature"]))
        endorsements = [
            x509.load_pem_x509_certificate(str(e).encode()) for e in receipt.get("service_endorsements", [])
        ]
    except (KeyError, ValueError, DepaError) as exc:
        raise VerificationError("malformed receipt certificate or signature", code="bad_receipt") from exc
    node_key = node_cert.public_key()
    if not isinstance(node_key, ec.EllipticCurvePublicKey):
        raise VerificationError("unsupported node key type", code="bad_receipt")
    try:
        node_key.verify(signature, root, ec.ECDSA(utils.Prehashed(hashes.SHA256())))
    except InvalidSignature as exc:
        raise VerificationError("receipt root signature is invalid", code="bad_receipt") from exc
    chain = [node_cert, *endorsements]
    for cert, issuer in zip(chain, [*endorsements, service_cert], strict=True):
        _check_signed_by(cert, issuer)
    if expected_claims is not None:
        claims_digest = str(receipt["leaf_components"]["claims_digest"])
        if claims_digest != sha256_hex(canonicalize(expected_claims)):
            raise VerificationError("receipt claims_digest does not match the expected claims", code="bad_receipt")
    return root


def receipt_jws_of(run: Mapping[str, Any]) -> str:
    """The CS-signed receipt from a ``GET /runs/{id}`` record (top level or under ``receipt``)."""
    receipt = run.get("receipt")
    nested: Mapping[str, Any] = receipt if isinstance(receipt, dict) else {}
    for candidate in (run.get("receipt_jws"), run.get("receipt"), nested.get("receipt_jws")):
        if isinstance(candidate, str):
            return candidate
    raise VerificationError("run has no execution receipt (not completed?)", code="no_receipt")
