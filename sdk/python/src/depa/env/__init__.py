"""``depa-env.yaml``: one file describing a deployed Data Collaborative and its parties.

Generated from Terraform outputs (``deploy/scripts``), consumed by ``depa --env``,
``depa dc setup`` and ``depa scenario deploy``. Schema: ``depa/env/schema.json``.
Relative paths are resolved against the file's directory; ``service_cert`` may be a path or an
inline PEM (materialised under ``$DEPA_HOME/env-certs`` so TLS pinning can load it).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cache
from importlib import resources
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from depa.contract.yamlio import load_yaml_text
from depa.core.encoding import sha256_hex
from depa.deploy.release import ReleaseManifest, load_release_manifest
from depa.errors import ConfigError, DepaError, ValidationError
from depa.profile import Profile
from depa.storage.factory import validate_location


@cache
def _validator() -> Draft202012Validator:
    schema = json.loads(resources.files("depa.env").joinpath("schema.json").read_text("utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


@dataclass(frozen=True)
class EnvParty:
    party_id: str
    roles: tuple[str, ...]
    key_dir: Path
    name: str


@dataclass(frozen=True)
class DepaEnv:
    path: Path
    raw: dict[str, Any]
    parties: tuple[EnvParty, ...]

    @property
    def base(self) -> Path:
        return self.path.parent

    def _resolve(self, value: str) -> Path:
        p = Path(value).expanduser()
        return p if p.is_absolute() else (self.base / p).resolve()

    def service(self, which: str) -> dict[str, Any]:
        return dict(self.raw["collaborative"][which])

    def cert_pem(self, which: str) -> str:
        value = str(self.service(which)["service_cert"])
        if "-----BEGIN CERTIFICATE-----" in value:
            return value
        path = self._resolve(value)
        try:
            pem = path.read_text("ascii")
        except OSError as exc:
            raise ConfigError(f"{which} service_cert {path} is not readable") from exc
        if "-----BEGIN CERTIFICATE-----" not in pem:
            raise ConfigError(f"{path} is not a PEM certificate")
        return pem

    def party(self, party_id: str) -> EnvParty:
        for p in self.parties:
            if p.party_id == party_id:
                return p
        raise ConfigError(f"party {party_id} is not in {self.path}")

    def parties_with(self, role: str) -> list[EnvParty]:
        return [p for p in self.parties if role in p.roles]

    def storage(self, party_id: str) -> dict[str, Any]:
        value = (self.raw.get("storage") or {}).get(party_id)
        if value is None:
            raise ConfigError(f"no storage for {party_id} in {self.path}")
        return dict(value)

    def outputs(self, party_id: str) -> dict[str, Any]:
        value = (self.raw.get("outputs") or {}).get(party_id)
        if value is None:
            raise ConfigError(f"no outputs location for {party_id} in {self.path}")
        return dict(value)

    def compute(self, kind: str) -> dict[str, Any]:
        value = (self.raw.get("compute") or {}).get(kind)
        if value is None:
            raise ConfigError(f"no compute.{kind} in {self.path}")
        return dict(value)

    def releases(self) -> list[ReleaseManifest]:
        directory = self.raw.get("releases_dir")
        if not directory:
            return []
        root = self._resolve(str(directory))
        if not root.is_dir():
            raise ConfigError(f"releases_dir {root} does not exist")
        return [load_release_manifest(p) for p in sorted(root.glob("*.json"))]

    def profile(self, home: Path, party_id: str | None = None) -> Profile:
        """A profile with the env's services (pinned certs) and per-party key directories."""
        cert_dir = home / "env-certs"
        cert_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        certs = {}
        for which in ("kms", "cs"):
            pem = self.cert_pem(which)
            target = cert_dir / f"{which}-{sha256_hex(pem.encode())[:16]}.pem"
            if not target.exists():
                target.write_text(pem)
            certs[which] = str(target)
        base = Profile.load(home)
        return base.with_overrides(
            kms_url=self.service("kms")["url"],
            kms_cert=certs["kms"],
            cs_url=self.service("cs")["url"],
            cs_cert=certs["cs"],
            key_dirs={p.party_id: str(p.key_dir) for p in self.parties},
            party_id=party_id,
        )


def _parse(path: Path) -> dict[str, Any]:
    try:
        doc = load_yaml_text(path.read_text("utf-8"), source=str(path))
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc.strerror}") from exc
    if not isinstance(doc, dict):
        raise ValidationError(f"{path}: expected a mapping", code="bad_env")
    return doc


def env_problems(path: Path) -> tuple[list[str], list[str]]:
    """``(errors, warnings)`` for an environment file: schema plus semantic checks."""
    doc = _parse(path)
    errors = [
        f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message[:300]}"
        for e in sorted(_validator().iter_errors(doc), key=lambda e: list(map(str, e.absolute_path)))
    ]
    if errors:
        return errors, []
    env = _build(path, doc)
    warnings: list[str] = []
    ids = [p.party_id for p in env.parties]
    if len(ids) != len(set(ids)):
        errors.append("parties: duplicate party_id")
    roles = {p.party_id: set(p.roles) for p in env.parties}
    for which in ("kms", "cs"):
        try:
            env.cert_pem(which)
        except DepaError as exc:
            errors.append(str(exc))
    for section, role in (("storage", "tdp"), ("outputs", "tdc")):
        for party_id, location in (doc.get(section) or {}).items():
            if party_id not in roles:
                errors.append(f"{section}.{party_id}: not a party in this file")
            elif role not in roles[party_id] and not (section == "storage" and "tdc" in roles[party_id]):
                errors.append(f"{section}.{party_id}: party lacks role {role}")
            try:
                validate_location(location, for_output=True)
            except DepaError as exc:
                errors.append(f"{section}.{party_id}: {exc}")
    for p in env.parties:
        if not (p.key_dir / "party.json").exists():
            warnings.append(f"{p.party_id}: no keys in {p.key_dir} (only parties whose keys you hold can act)")
    if not env.parties_with("dc-member"):
        warnings.append("no dc-member party: `depa dc setup` cannot govern")
    try:
        env.releases()
    except DepaError as exc:
        errors.append(f"releases_dir: {exc}")
    return errors, warnings


def _build(path: Path, doc: dict[str, Any]) -> DepaEnv:
    base = path.parent
    parties = []
    for p in doc["parties"]:
        key_dir = Path(p["key_dir"]).expanduser()
        parties.append(
            EnvParty(
                party_id=p["party_id"],
                roles=tuple(p["roles"]),
                key_dir=key_dir if key_dir.is_absolute() else (base / key_dir).resolve(),
                name=str(p.get("name") or p["party_id"]),
            )
        )
    return DepaEnv(path=path.resolve(), raw=doc, parties=tuple(parties))


def load_env(path: Path) -> DepaEnv:
    errors, _ = env_problems(path)
    if errors:
        raise ValidationError(f"{path} is invalid:\n  " + "\n  ".join(errors), code="bad_env")
    return _build(path, _parse(path))
