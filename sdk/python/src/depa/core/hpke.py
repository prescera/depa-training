"""HPKE base mode, suite DHKEM(X25519, HKDF-SHA256) / HKDF-SHA256 / AES-256-GCM (spec §2).

Single-shot seal/open of one message per context (DEK wrapping); ``seq = 0``.
"""

from __future__ import annotations

from pyhpke import AEADId, CipherSuite, KDFId, KEMId, KEMKeyPair, OpenError

from depa.errors import CryptoError

KEM_ID = 0x0020
KDF_ID = 0x0001
AEAD_ID = 0x0002
ENC_SIZE = 32

SUITE = CipherSuite.new(KEMId.DHKEM_X25519_HKDF_SHA256, KDFId.HKDF_SHA256, AEADId.AES256_GCM)


def seal_base(
    public_key: bytes, *, info: bytes, aad: bytes, plaintext: bytes, _ephemeral_ikm: bytes | None = None
) -> tuple[bytes, bytes]:
    """Return ``(enc, ct)``. ``_ephemeral_ikm`` exists only for RFC 9180 test-vector checks."""
    if len(public_key) != 32:
        raise CryptoError("HPKE recipient key must be a 32-byte X25519 public key")
    pkr = SUITE.kem.deserialize_public_key(public_key)
    eks: KEMKeyPair | None = SUITE.kem.derive_key_pair(_ephemeral_ikm) if _ephemeral_ikm is not None else None
    enc, ctx = SUITE.create_sender_context(pkr, info, eks=eks)
    ct = ctx.seal(plaintext, aad)
    return bytes(enc), bytes(ct)


def open_base(private_key: bytes, *, enc: bytes, info: bytes, aad: bytes, ciphertext: bytes) -> bytes:
    if len(private_key) != 32 or len(enc) != ENC_SIZE:
        raise CryptoError("HPKE key or encapsulation has the wrong size")
    try:
        skr = SUITE.kem.deserialize_private_key(private_key)
        ctx = SUITE.create_recipient_context(enc, skr, info)
        return bytes(ctx.open(ciphertext, aad))
    except OpenError as exc:
        raise CryptoError("HPKE open failed") from exc
    except Exception as exc:  # pyhpke raises assorted errors for malformed points
        raise CryptoError(f"HPKE open failed: {type(exc).__name__}") from exc
