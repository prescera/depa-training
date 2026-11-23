"""Run material generated in TEE memory and the run binding (spec §9.1)."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from typing import Any

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from depa.core.encoding import b64_decode, canonicalize, sha256_hex
from depa.core.keys import EcSigner
from depa.errors import CryptoError

RSA_BITS = 3072


@dataclass(frozen=True)
class RunMaterial:
    run_id: str
    wrapping_key: rsa.RSAPrivateKey = field(repr=False)
    wrapping_key_pem: str
    run_signer: EcSigner = field(repr=False)

    @classmethod
    def generate(cls) -> RunMaterial:
        run_id = str(uuid.uuid4())
        key = rsa.generate_private_key(public_exponent=65537, key_size=RSA_BITS)
        pem = (
            key.public_key()
            .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
            .decode("ascii")
        )
        return cls(run_id, key, pem, EcSigner.generate(f"run-{run_id}"))

    @property
    def wrapping_key_sha256(self) -> str:
        """Hex SHA-256 of the exact PEM string sent as ``wrappingKey`` (spec §2)."""
        return sha256_hex(self.wrapping_key_pem.encode("ascii"))

    def binding(
        self,
        *,
        contract_id: str,
        contract_digest: str,
        recipe_digest: str,
        platform: str,
        trust_bundle_digest: str | None = None,
    ) -> dict[str, Any]:
        """Spec §9.1; ``trust_bundle_digest`` is mandatory on non-virtual platforms."""
        binding: dict[str, Any] = {
            "v": 1,
            "contract_id": contract_id,
            "contract_digest": contract_digest,
            "run_id": self.run_id,
            "recipe_digest": recipe_digest,
            "platform": platform,
            "wrapping_key_sha256": self.wrapping_key_sha256,
            "run_key": self.run_signer.public_jwk(),
        }
        if trust_bundle_digest is not None:
            binding["trust_bundle_digest"] = trust_bundle_digest
        return binding

    def unwrap(self, wrapped_b64: str) -> bytes:
        """RSA-OAEP-SHA256 unwrap of a released raw 32-byte X25519 private key."""
        try:
            raw = self.wrapping_key.decrypt(
                b64_decode(wrapped_b64),
                padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
            )
        except ValueError as exc:
            raise CryptoError("released key cannot be unwrapped", code="unwrap_failed") from exc
        if len(raw) != 32:
            raise CryptoError("released key has the wrong length", code="unwrap_failed")
        return raw


def binding_hash(binding: dict[str, Any]) -> bytes:
    return hashlib.sha256(canonicalize(binding)).digest()
