"""Strict encodings: base64url (spec §2), canonical JSON (RFC 8785) and digests."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any

import rfc8785

from depa.errors import ValidationError

_B64URL_RE = re.compile(r"^[A-Za-z0-9_-]*$")
_B64STD_RE = re.compile(r"^[A-Za-z0-9+/]*={0,2}$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")


def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(text: str) -> bytes:
    """Decode unpadded base64url, rejecting padding, foreign characters and non-canonical input."""
    if not isinstance(text, str) or not _B64URL_RE.fullmatch(text) or len(text) % 4 == 1:
        raise ValidationError("invalid base64url value", code="bad_base64url")
    raw = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    if b64url_encode(raw) != text:
        raise ValidationError("non-canonical base64url value", code="bad_base64url")
    return raw


def b64_encode(data: bytes) -> str:
    """Standard padded base64 (used where the spec says ``<b64>``, e.g. attestation evidence)."""
    return base64.b64encode(data).decode("ascii")


def b64_decode(text: str) -> bytes:
    """Decode standard padded base64, strictly."""
    cleaned = "".join(text.split())
    if not _B64STD_RE.fullmatch(cleaned) or len(cleaned) % 4 != 0:
        raise ValidationError("invalid base64 value", code="bad_base64")
    try:
        return base64.b64decode(cleaned, validate=True)
    except binascii.Error as exc:
        raise ValidationError("invalid base64 value", code="bad_base64") from exc


def canonicalize(obj: Any) -> bytes:
    """RFC 8785 JSON Canonicalization Scheme."""
    try:
        return bytes(rfc8785.dumps(obj))
    except (rfc8785.CanonicalizationError, TypeError, ValueError) as exc:
        raise ValidationError(f"value cannot be canonicalized: {exc}", code="bad_json") from exc


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def digest(obj: Any) -> str:
    """``"sha256:" + hex(SHA-256(JCS(obj)))`` (spec §2)."""
    return "sha256:" + sha256_hex(canonicalize(obj))


def is_digest(value: object) -> bool:
    return isinstance(value, str) and _DIGEST_RE.fullmatch(value) is not None


def is_hex32(value: object) -> bool:
    """Lowercase hex encoding of 32 bytes."""
    return isinstance(value, str) and _HEX64_RE.fullmatch(value) is not None


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ValidationError(f"duplicate JSON member {key!r}", code="bad_json")
        out[key] = value
    return out


def _reject_constant(name: str) -> Any:
    raise ValidationError(f"non-finite number {name} in JSON", code="bad_json")


def loads_strict(data: bytes | str) -> Any:
    """Parse JSON rejecting duplicate members and NaN/Infinity (prevents parser differentials)."""
    try:
        return json.loads(data, object_pairs_hook=_reject_duplicates, parse_constant=_reject_constant)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValidationError(f"invalid JSON: {exc.msg if hasattr(exc, 'msg') else exc}", code="bad_json") from exc
    except RecursionError as exc:
        raise ValidationError("JSON nesting too deep", code="bad_json") from exc


def loads_object(data: bytes | str) -> dict[str, Any]:
    value = loads_strict(data)
    if not isinstance(value, dict):
        raise ValidationError("expected a JSON object", code="bad_json")
    return value


def dumps_pretty(obj: Any) -> str:
    return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def require_mapping(value: object, what: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValidationError(f"{what} must be an object", code="bad_type")
    return value
