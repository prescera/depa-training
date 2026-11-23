"""The platform-independent description of one CCR job (spec §12 runtime inputs)."""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass, replace
from urllib.parse import urlsplit

from depa.core.encoding import is_digest
from depa.core.ids import check_platform, check_uuid4
from depa.errors import ValidationError

LOG_LEVELS = ("debug", "info", "warning", "error")
MAX_DURATION_MINUTES = 7 * 24 * 60
_NAME_RE = re.compile(r"^[a-z]([-a-z0-9]{0,61}[a-z0-9])?$")
_PRINTABLE_RE = re.compile(r"^[\x20-\x7e]*$")
MAX_TRUST_BUNDLE_LEN = 32 * 1024


def _https_url(value: object, what: str) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{what} must be an https:// base URL", code="bad_job_spec")
    parts = urlsplit(value)
    if parts.scheme != "https" or not parts.hostname or parts.query or parts.fragment:
        raise ValidationError(f"{what} must be an https:// base URL", code="bad_job_spec")
    return value


@dataclass(frozen=True)
class JobSpec:
    contract_id: str
    recipe_digest: str
    cs_url: str
    kms_url: str
    platform: str
    max_duration_minutes: int
    trust_bundle: str | None
    log_level: str = "info"
    name: str | None = None

    def __post_init__(self) -> None:
        check_uuid4(self.contract_id)
        if not is_digest(self.recipe_digest):
            raise ValidationError("recipe_digest must be sha256:<64 hex>", code="bad_job_spec")
        _https_url(self.cs_url, "cs_url")
        _https_url(self.kms_url, "kms_url")
        check_platform(self.platform)
        if (
            isinstance(self.max_duration_minutes, bool)
            or not isinstance(self.max_duration_minutes, int)
            or not 1 <= self.max_duration_minutes <= MAX_DURATION_MINUTES
        ):
            raise ValidationError("max_duration_minutes out of range", code="bad_job_spec")
        if self.trust_bundle is not None and (
            not self.trust_bundle
            or len(self.trust_bundle) > MAX_TRUST_BUNDLE_LEN
            or not _PRINTABLE_RE.fullmatch(self.trust_bundle)
        ):
            raise ValidationError("trust_bundle must be non-empty printable ASCII", code="bad_job_spec")
        if self.log_level not in LOG_LEVELS:
            raise ValidationError(f"log_level must be one of {LOG_LEVELS}", code="bad_job_spec")
        if self.name is not None and not _NAME_RE.fullmatch(self.name):
            raise ValidationError("job name must be a DNS-1123 label starting with a letter", code="bad_job_spec")

    def env(self) -> dict[str, str]:
        """Exactly the CCR runtime environment of spec §12 (non-secret, untrusted by the CCR)."""
        env = {
            "DEPA_CONTRACT_ID": self.contract_id,
            "DEPA_RECIPE_DIGEST": self.recipe_digest,
            "DEPA_CS_URL": self.cs_url,
            "DEPA_KMS_URL": self.kms_url,
            "DEPA_PLATFORM": self.platform,
        }
        if self.trust_bundle is not None:
            env["DEPA_TRUST_BUNDLE"] = self.trust_bundle
        env["DEPA_LOG_LEVEL"] = self.log_level
        return env

    def job_name(self) -> str:
        """``name`` if set, else a fresh ``depa-ccr-<contract id prefix>-<8 random hex>``.

        Call :meth:`ensure_name` once and reuse the result so every step uses the same name.
        """
        return self.name or f"depa-ccr-{self.contract_id[:8]}-{secrets.token_hex(4)}"

    def ensure_name(self) -> JobSpec:
        return self if self.name is not None else replace(self, name=self.job_name())

    def check_duration(self, contract_max_duration_minutes: int) -> None:
        """The platform deadline must not exceed the contract's ``max_duration_minutes``."""
        if self.max_duration_minutes > contract_max_duration_minutes:
            raise ValidationError("job duration exceeds the contract's max_duration_minutes", code="bad_job_spec")
