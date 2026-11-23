"""CCR runtime inputs (spec §12): operator-supplied environment and the trust bundle.

The environment is NOT trusted for security: every decision is re-derived from the contract
and enforced by the CS/KMS. The trust bundle pins the services' TLS identity and JWKS for the
reads the CCR makes itself (output recipients' HPKE keys, the tee-only output key, manifest
owner keys, RAT and receipt signatures). Its digest is part of the attested binding (§9.1) and
both services refuse a binding whose ``trust_bundle_digest`` differs from the governance
setting, so a substituted bundle never obtains data keys. It is mandatory on production
platforms and needs no separate measurement.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from depa.clients.http import ServiceEndpoint
from depa.core.encoding import b64_decode, b64url_decode, digest, is_digest, loads_object
from depa.core.ids import CS_SERVICE, KMS_SERVICE, check_platform, check_uuid4
from depa.core.keys import JWK, public_key_from_jwk
from depa.errors import ConfigError, DepaError

LOG_LEVELS = ("error", "warn", "info", "debug")
WORK_DIR = Path("/depa")
MAX_BUNDLE_BYTES = 256 * 1024


@dataclass(frozen=True)
class PinnedService:
    url: str
    service_cert_pem: str
    jwks: tuple[JWK, ...] | None


@dataclass(frozen=True)
class TrustBundle:
    cs: PinnedService
    kms: PinnedService
    digest: str

    @classmethod
    def parse(cls, value: str) -> TrustBundle:
        raw: bytes
        if value.startswith("/"):
            try:
                raw = Path(value).read_bytes()[: MAX_BUNDLE_BYTES + 1]
            except OSError as exc:
                raise ConfigError(f"cannot read trust bundle file: {exc.strerror}") from exc
        else:
            try:
                raw = (
                    b64_decode(value) if ("+" in value or "/" in value or value.endswith("=")) else b64url_decode(value)
                )
            except DepaError as exc:
                raise ConfigError("DEPA_TRUST_BUNDLE is neither a path nor base64 JSON") from exc
        if len(raw) > MAX_BUNDLE_BYTES:
            raise ConfigError("trust bundle too large")
        try:
            doc = loads_object(raw)
        except DepaError as exc:
            raise ConfigError("trust bundle is not valid JSON") from exc
        if doc.get("v") != 1 or set(doc) != {"v", "cs", "kms"}:
            raise ConfigError("trust bundle must be {v: 1, cs: {...}, kms: {...}}")
        return cls(cs=_pinned(doc["cs"], "cs"), kms=_pinned(doc["kms"], "kms"), digest=digest(doc))

    def to_json(self) -> dict[str, Any]:
        def one(p: PinnedService) -> dict[str, Any]:
            out: dict[str, Any] = {"url": p.url, "service_cert_pem": p.service_cert_pem}
            if p.jwks is not None:
                out["jwks"] = list(p.jwks)
            return out

        return {"v": 1, "cs": one(self.cs), "kms": one(self.kms)}


def _pinned(obj: object, name: str) -> PinnedService:
    if not isinstance(obj, dict) or not set(obj) <= {"url", "service_cert_pem", "jwks"}:
        raise ConfigError(f"trust bundle {name} entry is malformed")
    url, pem, jwks = obj.get("url"), obj.get("service_cert_pem"), obj.get("jwks")
    if not isinstance(url, str) or not isinstance(pem, str) or "BEGIN CERTIFICATE" not in pem:
        raise ConfigError(f"trust bundle {name} needs url and service_cert_pem")
    if jwks is not None:
        if not isinstance(jwks, list) or not jwks:
            raise ConfigError(f"trust bundle {name}.jwks must be a non-empty list")
        for jwk in jwks:
            try:
                public_key_from_jwk(jwk)
            except DepaError as exc:
                raise ConfigError(f"trust bundle {name}.jwks has an invalid key") from exc
    return PinnedService(url=url.rstrip("/"), service_cert_pem=pem, jwks=tuple(jwks) if jwks else None)


@dataclass(frozen=True)
class CcrConfig:
    contract_id: str
    recipe_digest: str
    cs_url: str
    kms_url: str
    platform: str
    log_level: str
    trust_bundle: TrustBundle | None
    work_dir: Path = WORK_DIR

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None, *, work_dir: Path = WORK_DIR) -> CcrConfig:
        e = os.environ if env is None else env
        try:
            contract_id = check_uuid4(e.get("DEPA_CONTRACT_ID", ""))
            platform = check_platform(e.get("DEPA_PLATFORM", ""))
        except DepaError as exc:
            raise ConfigError(str(exc)) from exc
        recipe_digest = e.get("DEPA_RECIPE_DIGEST", "")
        if not is_digest(recipe_digest):
            raise ConfigError("DEPA_RECIPE_DIGEST must be sha256:<hex>")
        cs_url = e.get("DEPA_CS_URL", "").rstrip("/")
        kms_url = e.get("DEPA_KMS_URL", "").rstrip("/")
        level = e.get("DEPA_LOG_LEVEL", "info") or "info"
        if level not in LOG_LEVELS:
            raise ConfigError(f"DEPA_LOG_LEVEL must be one of {LOG_LEVELS}")
        bundle_raw = e.get("DEPA_TRUST_BUNDLE", "")
        bundle = TrustBundle.parse(bundle_raw) if bundle_raw else None
        if bundle is None and platform != "virtual":
            raise ConfigError("DEPA_TRUST_BUNDLE is required on production platforms")
        if bundle is not None and (bundle.cs.url != cs_url or bundle.kms.url != kms_url):
            raise ConfigError("service URLs differ from the pinned trust bundle")
        config = cls(contract_id, recipe_digest, cs_url, kms_url, platform, level, bundle, work_dir)
        config.cs_endpoint()  # validates the URLs (https only)
        config.kms_endpoint()
        return config

    def cs_endpoint(self) -> ServiceEndpoint:
        pem = self.trust_bundle.cs.service_cert_pem if self.trust_bundle else None
        return ServiceEndpoint(CS_SERVICE, self.cs_url, ca_pem=pem)

    def kms_endpoint(self) -> ServiceEndpoint:
        pem = self.trust_bundle.kms.service_cert_pem if self.trust_bundle else None
        return ServiceEndpoint(KMS_SERVICE, self.kms_url, ca_pem=pem)

    @property
    def dev(self) -> bool:
        return self.platform == "virtual"
