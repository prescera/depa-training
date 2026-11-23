"""Local profile: which party this is, where its keys live and how to reach (and pin) the services.

``$DEPA_HOME`` (default ``~/.depa``) holds ``config.yaml`` and ``keys/<party_id>/``:
``signing.jwk`` (ES256 private JWK), ``hpke.jwk`` (X25519 private JWK), both 0600, and
``party.json`` (the public party record to hand to the DC). Environment variables
``DEPA_PARTY``, ``DEPA_KMS_URL``, ``DEPA_KMS_CERT``, ``DEPA_CS_URL``, ``DEPA_CS_CERT`` override
the file; CLI options override both.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml

from depa.clients.cs import CsClient
from depa.clients.http import ServiceEndpoint
from depa.clients.kms import KmsClient
from depa.contract.yamlio import load_yaml_text
from depa.core.encoding import dumps_pretty, loads_object
from depa.core.ids import CS_SERVICE, KMS_SERVICE, ROLES, check_party_id
from depa.core.keys import EcSigner, HpkeKeyPair, load_hpke_file, load_signer_file, write_secret_file
from depa.errors import ConfigError, ValidationError


def depa_home(env: Mapping[str, str] | None = None) -> Path:
    e = os.environ if env is None else env
    return Path(e.get("DEPA_HOME") or Path.home() / ".depa").expanduser()


@dataclass(frozen=True)
class ServiceConfig:
    url: str | None = None
    cert: str | None = None  # path to the pinned service certificate (PEM)


@dataclass(frozen=True)
class Profile:
    home: Path
    party_id: str | None = None
    kms: ServiceConfig = field(default_factory=ServiceConfig)
    cs: ServiceConfig = field(default_factory=ServiceConfig)
    operator_cert: str | None = None
    operator_key: str | None = None
    key_dirs: Mapping[str, str] = field(default_factory=dict)  # per-party overrides (env file)

    @classmethod
    def load(cls, home: Path | None = None, env: Mapping[str, str] | None = None) -> Profile:
        e = os.environ if env is None else env
        root = home or depa_home(e)
        doc: dict[str, Any] = {}
        cfg = root / "config.yaml"
        if cfg.exists():
            loaded = load_yaml_text(cfg.read_text("utf-8"), source=str(cfg))
            if not isinstance(loaded, dict):
                raise ConfigError(f"{cfg} must be a mapping")
            doc = loaded
        services = doc.get("services") or {}
        operator = doc.get("operator") or {}

        def svc(name: str, env_prefix: str) -> ServiceConfig:
            s = services.get(name) or {}
            return ServiceConfig(
                url=e.get(f"{env_prefix}_URL") or s.get("url"),
                cert=e.get(f"{env_prefix}_CERT") or s.get("cert"),
            )

        return cls(
            home=root,
            party_id=e.get("DEPA_PARTY") or doc.get("party_id"),
            kms=svc("kms", "DEPA_KMS"),
            cs=svc("cs", "DEPA_CS"),
            operator_cert=operator.get("client_cert"),
            operator_key=operator.get("client_key"),
        )

    def with_overrides(self, **values: Any) -> Profile:
        clean = {k: v for k, v in values.items() if v is not None}
        kms = replace(self.kms, **{k[4:]: v for k, v in clean.items() if k.startswith("kms_")})
        cs = replace(self.cs, **{k[3:]: v for k, v in clean.items() if k.startswith("cs_")})
        rest = {k: v for k, v in clean.items() if not k.startswith(("kms_", "cs_"))}
        return replace(self, kms=kms, cs=cs, **rest)

    def save(self) -> Path:
        self.home.mkdir(parents=True, exist_ok=True, mode=0o700)
        doc: dict[str, Any] = {
            "party_id": self.party_id,
            "services": {
                "kms": {"url": self.kms.url, "cert": self.kms.cert},
                "cs": {"url": self.cs.url, "cert": self.cs.cert},
            },
        }
        if self.operator_cert:
            doc["operator"] = {"client_cert": self.operator_cert, "client_key": self.operator_key}
        path = self.home / "config.yaml"
        path.write_text(yaml.safe_dump(doc, sort_keys=False))
        return path

    # ---- services -------------------------------------------------------------------------

    def _endpoint(self, name: str, svc: ServiceConfig, *, operator: bool = False) -> ServiceEndpoint:
        if not svc.url:
            raise ConfigError(f"{name} URL is not configured (config.yaml services or DEPA_*_URL)")
        if not svc.cert:
            raise ConfigError(f"{name} service certificate is not configured (pinning is mandatory)")
        pem = Path(svc.cert).expanduser().read_text("ascii")
        client_cert = None
        if operator:
            if not (self.operator_cert and self.operator_key):
                raise ConfigError("operator client certificate/key are required for this call")
            client_cert = (self.operator_cert, self.operator_key)
        return ServiceEndpoint(name, svc.url, ca_pem=pem, client_cert=client_cert)

    def kms_client(self, *, operator: bool = False) -> KmsClient:
        return KmsClient(self._endpoint(KMS_SERVICE, self.kms, operator=operator))

    def cs_client(self, *, operator: bool = False) -> CsClient:
        return CsClient(self._endpoint(CS_SERVICE, self.cs, operator=operator))

    def service_cert_pem(self, which: str) -> str:
        svc = self.kms if which == "kms" else self.cs
        if not svc.cert:
            raise ConfigError(f"{which} service certificate is not configured")
        return Path(svc.cert).expanduser().read_text("ascii")

    # ---- keys -----------------------------------------------------------------------------

    def require_party(self) -> str:
        if not self.party_id:
            raise ConfigError("no party selected (--party, DEPA_PARTY or config.yaml party_id)")
        return check_party_id(self.party_id)

    def keys_dir(self, party_id: str | None = None) -> Path:
        pid = party_id or self.require_party()
        override = self.key_dirs.get(pid)
        return Path(override).expanduser() if override else self.home / "keys" / pid

    def signer(self, party_id: str | None = None) -> EcSigner:
        pid = party_id or self.require_party()
        record = self.party_record(pid)
        kid = record["signing_keys"][0]["kid"]
        return load_signer_file(self.keys_dir(pid) / "signing.jwk", key_ref=f"{pid}#{kid}")

    def hpke_key(self, party_id: str | None = None) -> HpkeKeyPair:
        return load_hpke_file(self.keys_dir(party_id) / "hpke.jwk")

    def party_record(self, party_id: str | None = None) -> dict[str, Any]:
        path = self.keys_dir(party_id) / "party.json"
        if not path.exists():
            raise ConfigError(f"no keys for party {party_id or self.party_id}: run `depa party keygen`")
        return loads_object(path.read_bytes())


def generate_party(
    profile: Profile,
    *,
    party_id: str,
    name: str,
    roles: Sequence[str],
    signing_kid: str = "k1",
    hpke_kid: str = "h1",
) -> dict[str, Any]:
    """Create keys (0600) and the public party record (spec §3.1)."""
    check_party_id(party_id)
    bad = [r for r in roles if r not in ROLES]
    if bad or not roles:
        raise ValidationError(f"roles must be a non-empty subset of {ROLES}", code="bad_roles")
    directory = profile.keys_dir(party_id)
    if (directory / "signing.jwk").exists():
        raise ConfigError(f"keys for {party_id} already exist in {directory}")
    signer = EcSigner.generate(signing_kid)
    hpke = HpkeKeyPair.generate(hpke_kid)
    write_secret_file(directory / "signing.jwk", dumps_pretty(signer.private_jwk()).encode())
    write_secret_file(directory / "hpke.jwk", dumps_pretty(hpke.to_jwk()).encode())
    record = {
        "party_id": party_id,
        "name": name,
        "roles": list(dict.fromkeys(roles)),
        "signing_keys": [signer.public_jwk()],
        "hpke_public_keys": [hpke.public_record()],
        "status": "active",
    }
    (directory / "party.json").write_text(dumps_pretty(record))
    return record
