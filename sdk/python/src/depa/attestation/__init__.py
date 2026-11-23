"""In-CCR attestation providers (spec §9.2).

A provider turns a 32-byte ``binding_hash`` into the request field ``attestation`` (and, on
``gcp-cs-tdx``, a bearer token for the ``Authorization`` header). Evidence is requested per
service ``audience`` (the service base URL) because GCP tokens are audience-bound; SNP reports
are audience-independent and cached per binding.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from depa.core.ids import check_platform


@dataclass(frozen=True)
class Evidence:
    envelope: dict[str, Any]
    bearer_token: str | None = field(default=None, repr=False)


class AttestationProvider(Protocol):
    platform: str

    def evidence(self, binding_hash: bytes, audience: str) -> Evidence: ...


def get_provider(platform: str) -> AttestationProvider:
    check_platform(platform)
    if platform == "aci-snp":
        from depa.attestation.snp import SnpProvider  # noqa: PLC0415 - avoids an import cycle

        return SnpProvider()
    if platform == "gcp-cs-tdx":
        from depa.attestation.gcp import GcpConfidentialSpaceProvider  # noqa: PLC0415 - avoids an import cycle

        return GcpConfidentialSpaceProvider()
    from depa.attestation.virtual import VirtualProvider  # noqa: PLC0415 - avoids an import cycle

    return VirtualProvider()
