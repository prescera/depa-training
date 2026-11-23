"""The console's process state: which party it acts as, its profile, SDK clients and task registry.

The console acts as exactly one party at a time. Parties are those whose signing keys are held
locally: ``key_dir``s of an environment file (``depa-env.yaml``) or ``$DEPA_HOME/keys/*`` of a
plain profile (e.g. the one ``depa dev up`` writes). Keys are read by the SDK when signing and
never leave the process.
"""

from __future__ import annotations

import hashlib
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding
from depa.clients.cs import CsClient
from depa.clients.kms import KmsClient
from depa.core.encoding import loads_object
from depa.env import DepaEnv, load_env
from depa.errors import ConfigError
from depa.profile import Profile

from depa_console.tasks import TaskRegistry


@dataclass(frozen=True)
class Settings:
    home: Path
    env_path: Path | None = None
    party: str | None = None
    scenarios_root: Path = Path("scenarios")
    work_root: Path | None = None  # default: <home>/console
    storage_token_env: str | None = None  # names of env vars holding bearer tokens (never values)
    output_token_env: str | None = None
    dev_trainer_cmd: str | None = None  # local scenario runs only (development)
    workers: int = 2

    @property
    def work_dir(self) -> Path:
        return self.work_root or self.home / "console"


@dataclass(frozen=True)
class Identity:
    party_id: str
    name: str
    roles: tuple[str, ...]
    key_dir: Path
    signing_kid: str
    hpke_kid: str

    def has(self, *roles: str) -> bool:
        return any(r in self.roles for r in roles)


@dataclass(frozen=True)
class Check:
    """A cached verification result shown on a page (audit, ledger inclusion)."""

    at: float
    ok: bool
    lines: tuple[str, ...]


def _identity(party_id: str, key_dir: Path) -> Identity | None:
    record_path = key_dir / "party.json"
    if not (key_dir / "signing.jwk").exists() or not record_path.exists():
        return None
    record = loads_object(record_path.read_bytes())
    return Identity(
        party_id=party_id,
        name=str(record.get("name") or party_id),
        roles=tuple(str(r) for r in record.get("roles", [])),
        key_dir=key_dir,
        signing_kid=str((record.get("signing_keys") or [{}])[0].get("kid", "")),
        hpke_kid=str((record.get("hpke_public_keys") or [{}])[0].get("kid", "")),
    )


def cert_fingerprint(path: str | None) -> str | None:
    if not path:
        return None
    try:
        cert = x509.load_pem_x509_certificate(Path(path).expanduser().read_bytes())
    except (OSError, ValueError):
        return None
    return "sha256:" + hashlib.sha256(cert.public_bytes(Encoding.DER)).hexdigest()


@dataclass
class Console:
    settings: Settings
    env: DepaEnv | None = None
    base: Profile = field(init=False)
    tasks: TaskRegistry = field(init=False)
    last_audit: Check | None = None
    ledger_checks: dict[str, Check] = field(default_factory=dict)
    _party: str = ""
    _clients: tuple[KmsClient, CsClient] | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self) -> None:
        if self.settings.env_path is not None:
            self.env = load_env(self.settings.env_path)
            self.base = self.env.profile(self.settings.home)
        else:
            self.base = Profile.load(self.settings.home)
        held = self.held()
        if not held:
            where = self.settings.env_path or self.settings.home / "keys"
            raise ConfigError(f"no party keys found in {where} (create them with `depa party keygen`)")
        wanted = self.settings.party or self.base.party_id or held[0].party_id
        if wanted not in {i.party_id for i in held}:
            raise ConfigError(f"no keys for party {wanted}; held: {', '.join(i.party_id for i in held)}")
        self._party = wanted
        self.tasks = TaskRegistry(workers=self.settings.workers)

    # ---- parties ------------------------------------------------------------------------

    def held(self) -> list[Identity]:
        if self.env is not None:
            candidates = [(p.party_id, p.key_dir) for p in self.env.parties]
        else:
            root = self.settings.home / "keys"
            candidates = [(d.name, d) for d in sorted(root.iterdir()) if d.is_dir()] if root.is_dir() else []
        return [i for pid, d in candidates if (i := _identity(pid, d)) is not None]

    def identity(self) -> Identity:
        found = _identity(self._party, self.base.keys_dir(self._party))
        if found is None:
            raise ConfigError(f"keys for {self._party} are no longer readable")
        return found

    def switch(self, party_id: str) -> Identity:
        if party_id not in {i.party_id for i in self.held()}:
            raise ConfigError(f"no keys for party {party_id}")
        with self._lock:
            self._party = party_id
        return self.identity()

    def profile(self) -> Profile:
        return self.base.with_overrides(party_id=self._party)

    # ---- services -----------------------------------------------------------------------

    def clients(self) -> tuple[KmsClient, CsClient]:
        with self._lock:
            if self._clients is None:
                self._clients = (self.base.kms_client(), self.base.cs_client())
            return self._clients

    def environment(self) -> dict[str, Any]:
        """What the overview shows about where the console points (no secrets)."""
        services = {}
        for name, svc in (("KMS", self.base.kms), ("Contract Service", self.base.cs)):
            services[name] = {"url": svc.url, "cert": svc.cert, "fingerprint": cert_fingerprint(svc.cert)}
        return {
            "source": f"environment file {self.env.path}" if self.env else f"profile {self.settings.home}",
            "services": services,
            "scenarios_root": str(self.settings.scenarios_root),
            "work_dir": str(self.settings.work_dir),
        }

    def token(self, which: str) -> str | None:
        """Bearer token for party-side storage, read from the env var named at launch."""
        name = self.settings.storage_token_env if which == "storage" else self.settings.output_token_env
        if not name:
            return None
        value = os.environ.get(name)
        if not value:
            raise ConfigError(f"environment variable {name} (--{which}-token-env) is not set")
        return value

    def record_check(self, key: str | None, ok: bool, lines: list[str]) -> Check:
        check = Check(time.time(), ok, tuple(lines))
        with self._lock:
            if key is None:
                self.last_audit = check
            else:
                self.ledger_checks[key] = check
                while len(self.ledger_checks) > 200:
                    self.ledger_checks.pop(next(iter(self.ledger_checks)))
        return check

    def close(self) -> None:
        self.tasks.shutdown()
        with self._lock:
            if self._clients is not None:
                for client in self._clients:
                    client.close()
                self._clients = None
