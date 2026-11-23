"""Signing keys (ES256) and HPKE X25519 keys.

``Signer`` is the abstraction every signing path goes through, so hardware-backed signers
(Azure Key Vault / Managed HSM, PKCS#11) can be added without touching protocol code: they
only need to return a raw ``r||s`` ES256 signature over the given bytes.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, x25519
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

from depa.core.encoding import b64url_decode, b64url_encode, loads_object
from depa.errors import ConfigError, ValidationError

JWK = dict[str, Any]

_CURVES: dict[str, tuple[ec.EllipticCurve, int, str]] = {
    "P-256": (ec.SECP256R1(), 32, "ES256"),
    "P-384": (ec.SECP384R1(), 48, "ES384"),
}
_HASHES: dict[str, hashes.HashAlgorithm] = {"ES256": hashes.SHA256(), "ES384": hashes.SHA384()}


@runtime_checkable
class Signer(Protocol):
    """Produces JWS signatures. ``kid`` is the value placed in the JWS protected header."""

    @property
    def kid(self) -> str: ...

    @property
    def alg(self) -> str: ...

    def public_jwk(self) -> JWK: ...

    def sign(self, data: bytes) -> bytes:
        """Return the raw JWS signature (``r||s`` for ECDSA) over ``data``."""
        ...


def _int_to_bytes(value: int, size: int) -> bytes:
    return value.to_bytes(size, "big")


def public_key_from_jwk(jwk: object) -> ec.EllipticCurvePublicKey:
    """Validate and load a public EC JWK (``kty=EC``, ``crv`` P-256/P-384). Rejects private members."""
    if not isinstance(jwk, dict):
        raise ValidationError("JWK must be an object", code="bad_jwk")
    if jwk.get("kty") != "EC" or jwk.get("crv") not in _CURVES:
        raise ValidationError("unsupported JWK (need kty=EC, crv=P-256|P-384)", code="bad_jwk")
    if "d" in jwk:
        raise ValidationError("public JWK must not contain a private member", code="bad_jwk")
    curve, size, _ = _CURVES[jwk["crv"]]
    x = b64url_decode(str(jwk.get("x", "")))
    y = b64url_decode(str(jwk.get("y", "")))
    if len(x) != size or len(y) != size:
        raise ValidationError("JWK coordinates have the wrong length", code="bad_jwk")
    try:
        return ec.EllipticCurvePublicKey.from_encoded_point(curve, b"\x04" + x + y)
    except ValueError as exc:
        raise ValidationError("JWK point is not on the curve", code="bad_jwk") from exc


def jwk_alg(jwk: JWK) -> str:
    return _CURVES[str(jwk["crv"])][2]


def public_jwk_from_key(key: ec.EllipticCurvePublicKey, kid: str) -> JWK:
    curve_name = {"secp256r1": "P-256", "secp384r1": "P-384"}.get(key.curve.name)
    if curve_name is None:
        raise ValidationError(f"unsupported curve {key.curve.name}", code="bad_jwk")
    size = _CURVES[curve_name][1]
    numbers = key.public_numbers()
    return {
        "kty": "EC",
        "crv": curve_name,
        "x": b64url_encode(_int_to_bytes(numbers.x, size)),
        "y": b64url_encode(_int_to_bytes(numbers.y, size)),
        "kid": kid,
    }


class EcSigner:
    """In-process ECDSA signer. ``key_ref`` (e.g. ``bank-a#k1``) becomes the JWS header kid."""

    def __init__(self, private_key: ec.EllipticCurvePrivateKey, jwk_kid: str, key_ref: str | None = None):
        curve_name = {"secp256r1": "P-256", "secp384r1": "P-384"}.get(private_key.curve.name)
        if curve_name is None:
            raise ValidationError("unsupported signing curve", code="bad_jwk")
        self._key = private_key
        self._crv = curve_name
        self._jwk_kid = jwk_kid
        self._key_ref = key_ref or jwk_kid

    @classmethod
    def generate(cls, jwk_kid: str, key_ref: str | None = None) -> EcSigner:
        return cls(ec.generate_private_key(ec.SECP256R1()), jwk_kid, key_ref)

    @classmethod
    def from_private_jwk(cls, jwk: JWK, key_ref: str | None = None) -> EcSigner:
        public = {k: v for k, v in jwk.items() if k != "d"}
        pub = public_key_from_jwk(public)
        d = b64url_decode(str(jwk.get("d", "")))
        size = _CURVES[str(jwk["crv"])][1]
        if len(d) != size:
            raise ValidationError("JWK private scalar has the wrong length", code="bad_jwk")
        priv = ec.derive_private_key(int.from_bytes(d, "big"), pub.curve)
        if priv.public_key().public_numbers() != pub.public_numbers():
            raise ValidationError("JWK private key does not match its public key", code="bad_jwk")
        kid = jwk.get("kid")
        if not isinstance(kid, str) or not kid:
            raise ValidationError("JWK has no kid", code="bad_jwk")
        return cls(priv, kid, key_ref)

    @property
    def kid(self) -> str:
        return self._key_ref

    @property
    def alg(self) -> str:
        return _CURVES[self._crv][2]

    def public_jwk(self) -> JWK:
        return public_jwk_from_key(self._key.public_key(), self._jwk_kid)

    def private_jwk(self) -> JWK:
        size = _CURVES[self._crv][1]
        jwk = self.public_jwk()
        jwk["d"] = b64url_encode(_int_to_bytes(self._key.private_numbers().private_value, size))
        return jwk

    def sign(self, data: bytes) -> bytes:
        der = self._key.sign(data, ec.ECDSA(_HASHES[self.alg]))
        r, s = decode_dss_signature(der)
        size = _CURVES[self._crv][1]
        return _int_to_bytes(r, size) + _int_to_bytes(s, size)


@dataclass(frozen=True)
class HpkeKeyPair:
    """X25519 key pair for HPKE (spec §2). ``kid`` is the party-local key id, e.g. ``h1``."""

    kid: str
    private_key: bytes
    public_key: bytes

    @classmethod
    def generate(cls, kid: str) -> HpkeKeyPair:
        sk = x25519.X25519PrivateKey.generate()
        return cls.from_private_bytes(kid, sk.private_bytes_raw())

    @classmethod
    def from_private_bytes(cls, kid: str, private_key: bytes) -> HpkeKeyPair:
        if len(private_key) != 32:
            raise ValidationError("X25519 private key must be 32 bytes", code="bad_key")
        sk = x25519.X25519PrivateKey.from_private_bytes(private_key)
        return cls(kid, private_key, sk.public_key().public_bytes_raw())

    def public_record(self) -> dict[str, str]:
        """Entry for a party's ``hpke_public_keys`` (spec §3.1)."""
        return {"kid": self.kid, "x25519": b64url_encode(self.public_key)}

    def to_jwk(self) -> JWK:
        return {
            "kty": "OKP",
            "crv": "X25519",
            "kid": self.kid,
            "x": b64url_encode(self.public_key),
            "d": b64url_encode(self.private_key),
        }

    @classmethod
    def from_jwk(cls, jwk: JWK) -> HpkeKeyPair:
        if jwk.get("kty") != "OKP" or jwk.get("crv") != "X25519":
            raise ValidationError("expected an X25519 OKP JWK", code="bad_key")
        pair = cls.from_private_bytes(str(jwk.get("kid", "")), b64url_decode(str(jwk.get("d", ""))))
        if b64url_encode(pair.public_key) != jwk.get("x"):
            raise ValidationError("X25519 JWK public part does not match", code="bad_key")
        if not pair.kid:
            raise ValidationError("X25519 JWK has no kid", code="bad_key")
        return pair


def x25519_public_from_b64(value: object) -> bytes:
    if not isinstance(value, str):
        raise ValidationError("x25519 public key must be a base64url string", code="bad_key")
    raw = b64url_decode(value)
    if len(raw) != 32:
        raise ValidationError("x25519 public key must be 32 bytes", code="bad_key")
    return raw


# ---------------------------------------------------------------------------------------------
# Secret files


def write_secret_file(path: Path, data: bytes, *, overwrite: bool = False) -> None:
    """Create ``path`` with mode 0600 (no symlink following, never widens permissions)."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    flags = os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    flags |= os.O_TRUNC if overwrite else os.O_EXCL
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise ConfigError(f"refusing to overwrite existing key file {path}") from exc
    try:
        os.fchmod(fd, 0o600)
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            view = view[written:]
    finally:
        os.close(fd)


def read_secret_file(path: Path) -> bytes:
    """Read a key file, refusing symlinks, non-regular files and group/other-accessible modes."""
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(path, flags)
    except FileNotFoundError as exc:
        raise ConfigError(f"key file not found: {path}") from exc
    except OSError as exc:
        raise ConfigError(f"cannot open key file {path}: {exc.strerror}") from exc
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise ConfigError(f"key file {path} is not a regular file")
        if st.st_mode & 0o077:
            raise ConfigError(f"key file {path} is accessible by group/others (chmod 600 it)")
        if st.st_uid != os.geteuid():
            raise ConfigError(f"key file {path} is not owned by the current user")
        chunks = []
        while chunk := os.read(fd, 65536):
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


def load_signer_file(path: Path, key_ref: str | None = None) -> EcSigner:
    return EcSigner.from_private_jwk(loads_object(read_secret_file(path)), key_ref)


def load_hpke_file(path: Path) -> HpkeKeyPair:
    return HpkeKeyPair.from_jwk(loads_object(read_secret_file(path)))
