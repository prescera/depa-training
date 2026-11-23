"""Error hierarchy shared by the SDK, the CLI and the CCR agent.

Every error carries a short machine-readable ``code``. The CCR agent only ever logs codes
(never messages), so codes must not embed data; messages may carry context for operators.
"""

from __future__ import annotations


class DepaError(Exception):
    """Base class. ``exit_code`` is used by the CLI."""

    code: str = "error"
    exit_code: int = 1

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


class ConfigError(DepaError):
    code = "config"
    exit_code = 2


class ValidationError(DepaError):
    """A document (contract, recipe, policy, descriptor, manifest) is malformed or inconsistent."""

    code = "invalid"
    exit_code = 3


class VerificationError(DepaError):
    """A signature, digest, binding or policy check failed. Always fail closed on this."""

    code = "verification_failed"
    exit_code = 3


class CryptoError(VerificationError):
    code = "crypto"


class ServiceError(DepaError):
    """A KMS / CS call failed (transport or non-2xx)."""

    code = "service"
    exit_code = 4

    def __init__(
        self,
        message: str,
        *,
        service: str,
        status: int | None = None,
        remote_code: str | None = None,
        code: str | None = None,
    ) -> None:
        super().__init__(message, code=code)
        self.service = service
        self.status = status
        self.remote_code = remote_code


class NotFoundError(ServiceError):
    code = "not_found"
    exit_code = 5


class StorageError(DepaError):
    code = "storage"
    exit_code = 4


class AttestationError(DepaError):
    code = "attestation"
    exit_code = 4


class DeployCancelled(DepaError):
    """A deployment was cancelled by its caller (``DeployOptions.cancel``)."""

    code = "cancelled"
    exit_code = 130
