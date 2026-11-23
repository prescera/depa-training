"""SDK exceptions -> short, human-readable problems (and HTTP statuses); secret redaction."""

from __future__ import annotations

import re
from dataclasses import dataclass

from depa.errors import (
    AttestationError,
    ConfigError,
    DepaError,
    NotFoundError,
    ServiceError,
    StorageError,
    ValidationError,
    VerificationError,
)

_SECRET_PARAM = re.compile(
    r"(?i)\b(sig|token|access_token|id_token|code|x-amz-signature|x-amz-credential|x-amz-security-token)=[^&\s\"']+"
)
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*")
MAX_MESSAGE = 4000


def redact(text: str) -> str:
    """Strip bearer tokens, JWTs and signed-URL credentials from text shown in pages or task logs."""
    text = _SECRET_PARAM.sub(lambda m: f"{m.group(1)}=[redacted]", text)
    text = _BEARER.sub("Bearer [redacted]", text)
    text = _JWT.sub("[redacted-jwt]", text)
    return text if len(text) <= MAX_MESSAGE else text[:MAX_MESSAGE] + " …"


@dataclass(frozen=True)
class Problem:
    title: str
    detail: str
    status: int

    @property
    def text(self) -> str:
        return f"{self.title}: {self.detail}"


def explain(exc: DepaError) -> Problem:
    detail = redact(str(exc)) or exc.code
    if isinstance(exc, NotFoundError):
        return Problem("Not found", detail, 404)
    if isinstance(exc, ServiceError):
        where = {"depa-kms": "KMS", "depa-contract-service": "Contract Service"}.get(exc.service, exc.service)
        parts = [p for p in (f"HTTP {exc.status}" if exc.status else "", exc.remote_code or "") if p]
        return Problem(f"{where} error" + (f" ({', '.join(parts)})" if parts else ""), detail, 502)
    if isinstance(exc, VerificationError):
        return Problem("Verification failed", detail, 409)
    if isinstance(exc, ValidationError):
        return Problem("Invalid input", detail, 422)
    if isinstance(exc, ConfigError):
        return Problem("Configuration problem", detail, 500)
    if isinstance(exc, StorageError):
        return Problem("Storage error", detail, 502)
    if isinstance(exc, AttestationError):
        return Problem("Attestation error", detail, 502)
    return Problem("Error", detail, 500)
