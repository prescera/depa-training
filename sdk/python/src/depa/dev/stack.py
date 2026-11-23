"""Local development stack: KMS + CS CCF sandboxes, bootstrapped governance, dev profile.

``depa dev up`` runs each service repository's ``scripts/sandbox-up.sh <port>`` (which prints
one JSON line: ``app_url``, ``service_cert``, ``user_cert``/``user_key`` for the operator...),
writes a dev profile (``$DEPA_HOME``), generates a ``dc-member`` identity, bootstraps both
services and, through member governance: enables ``allow_virtual_attestation`` (dev only),
pins the peers, approves the ``virtual`` CCR release and sets the KMS OIDC settings.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from depa import source_repo
from depa.clients.base import BaseClient
from depa.core.encoding import dumps_pretty, loads_object
from depa.errors import ConfigError, NotFoundError, ServiceError
from depa.profile import Profile, generate_party

DEFAULT_KMS_PORT = 18010
DEFAULT_CS_PORT = 18011
MEMBER_ID = "dc-member-1"
VIRTUAL_RELEASE_ID = "depa-ccr-virtual-dev"


def virtual_release() -> dict[str, Any]:
    """The development-only ``virtual`` CCR release record (provenance source is configurable)."""
    return {
        "release_id": VIRTUAL_RELEASE_ID,
        "platform": "virtual",
        "families": ["classic", "torch", "llm", "custom"],
        "tiers": [1, 2],
        "measurements": {"virtual_ids": ["virtual-dev"]},
        "provenance": {"source": source_repo(), "commit": "dev", "build": "local"},
    }


def default_repo(name: str) -> Path:
    env = {"kms": "DEPA_KMS_REPO", "cs": "DEPA_CS_REPO"}[name]
    default = {"kms": "depa-training-kms-azure", "cs": "contract-service"}[name]
    return Path(os.environ.get(env) or Path.home() / "depa_training" / default)


@dataclass(frozen=True)
class SandboxInfo:
    app_url: str
    service_cert: str
    user_cert: str
    user_key: str
    port: int

    @classmethod
    def parse(cls, obj: Mapping[str, Any], port: int) -> SandboxInfo:
        try:
            return cls(
                str(obj["app_url"]),
                str(obj["service_cert"]),
                str(obj["user_cert"]),
                str(obj["user_key"]),
                port,
            )
        except KeyError as exc:
            raise ConfigError(f"sandbox-up.sh output lacks {exc}") from exc


def _sandbox_env(state_dir: Path | None) -> dict[str, str]:
    """Keep sandbox workspaces (ledger, certs) under the dev profile, not inside the service repos."""
    env = dict(os.environ)
    if state_dir is not None:
        state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        env["DEPA_SANDBOX_DIR"] = str(state_dir)
    return env


def start_sandbox(repo: Path, port: int, *, state_dir: Path | None = None, timeout_s: float = 900) -> SandboxInfo:
    script = repo / "scripts" / "sandbox-up.sh"
    if not script.exists():
        raise ConfigError(f"{script} not found (is the service repository checked out and up to date?)")
    proc = subprocess.run(
        [str(script), str(port), "--no-build"]
        if (repo / "dist" / "bundle.json").exists()
        else [str(script), str(port)],
        capture_output=True,
        text=True,
        timeout=timeout_s,
        check=False,
        env=_sandbox_env(state_dir),
    )
    if proc.returncode != 0:
        raise ServiceError(f"{script} failed:\n{proc.stderr[-4000:]}", service=repo.name, code="sandbox_failed")
    lines = [line for line in proc.stdout.splitlines() if line.strip().startswith("{")]
    if not lines:
        raise ServiceError(f"{script} printed no JSON", service=repo.name, code="sandbox_failed")
    return SandboxInfo.parse(json.loads(lines[-1]), port)


def stop_sandbox(repo: Path, port: int, *, state_dir: Path | None = None, purge: bool = False) -> None:
    script = repo / "scripts" / "sandbox-down.sh"
    if script.exists():
        argv = [str(script), str(port), *(["--purge"] if purge else [])]
        subprocess.run(argv, capture_output=True, timeout=300, check=False, env=_sandbox_env(state_dir))


def ensure_member(profile: Profile) -> dict[str, Any]:
    if (profile.keys_dir(MEMBER_ID) / "party.json").exists():
        return profile.party_record(MEMBER_ID)
    return generate_party(profile, party_id=MEMBER_ID, name="Dev DC member", roles=["dc-member"])


def govern(
    client: BaseClient,
    profile: Profile,
    actions: Sequence[Mapping[str, Any]],
    members: Sequence[str] = (MEMBER_ID,),
    *,
    require_accept: bool = True,
) -> dict[str, Any]:
    """Propose as the first member and approve with every member whose keys are held."""
    proposal = client.gov_propose(profile.signer(members[0]), actions)
    result: dict[str, Any] = proposal
    for member in members:
        result = client.gov_vote(profile.signer(member), str(proposal["proposal_id"]), "approve")
        if result.get("state") == "accepted":
            return result
    if require_accept:
        raise ServiceError(
            f"proposal {proposal['proposal_id']} not accepted: {result.get('state')}",
            service=client.service_name,
            code="proposal_not_accepted",
        )
    return result


def register_party(profile: Profile, record: Mapping[str, Any], clients: Sequence[BaseClient]) -> None:
    for client in clients:
        try:
            existing = client.get_party(str(record["party_id"]))
            if existing.raw == dict(record):
                continue
        except NotFoundError:
            pass
        govern(client, profile, [{"name": "set_party", "args": dict(record)}])


def up(
    home: Path,
    *,
    kms_port: int = DEFAULT_KMS_PORT,
    cs_port: int = DEFAULT_CS_PORT,
    kms_repo: Path | None = None,
    cs_repo: Path | None = None,
) -> dict[str, Any]:
    kms_box = start_sandbox(kms_repo or default_repo("kms"), kms_port, state_dir=home / "sandboxes" / "kms")
    cs_box = start_sandbox(cs_repo or default_repo("cs"), cs_port, state_dir=home / "sandboxes" / "cs")
    profile = Profile.load(home).with_overrides(
        kms_url=kms_box.app_url,
        kms_cert=kms_box.service_cert,
        cs_url=cs_box.app_url,
        cs_cert=cs_box.service_cert,
        operator_cert=kms_box.user_cert,
        operator_key=kms_box.user_key,
    )
    profile.save()
    operators: list[BaseClient] = [
        profile.kms_client(operator=True),
        profile.with_overrides(operator_cert=cs_box.user_cert, operator_key=cs_box.user_key).cs_client(operator=True),
    ]
    configure_dev_governance(profile, operators, profile.kms_client(), profile.cs_client())
    state = {
        "home": str(home),
        "kms": kms_box.__dict__,
        "cs": cs_box.__dict__,
        "member": MEMBER_ID,
        "virtual_release": VIRTUAL_RELEASE_ID,
    }
    (home / "dev-stack.json").write_text(dumps_pretty(state))
    return state


def configure_dev_governance(
    profile: Profile, operators: Sequence[BaseClient], kms: BaseClient, cs: BaseClient
) -> None:
    """Bootstrap (if needed) with a single dev member and virtual attestation enabled, pin the
    peers, approve the ``virtual`` release in both registries and set the KMS OIDC settings."""
    member = ensure_member(profile)
    for client in operators:
        if not client.info().bootstrapped:
            # base_url is the GCP token audience and the RAT / receipt issuer; virtual is dev-only.
            settings = {"base_url": client.base_url, "allow_virtual_attestation": True}
            client.gov_bootstrap([member], {"default": "majority"}, settings)
    kms_info, cs_info = kms.info(), cs.info()
    for client, peer, info in ((kms, cs, cs_info), (cs, kms, kms_info)):
        pin = {"name": info.service, "url": peer.base_url, "jwks": list(info.jwks)}
        govern(client, profile, [{"name": "set_peer_service", "args": pin}])
        if not _release_approved(client):
            govern(client, profile, [{"name": "approve_ccr_release", "args": virtual_release()}])
    from depa.workflows import trust_bundle_settings_actions  # noqa: PLC0415 - avoids an import cycle

    for client in (kms, cs):
        actions = trust_bundle_settings_actions(profile, client, kms, cs)
        if actions:
            govern(client, profile, actions)
    oidc = {"issuer_url": kms.base_url + "/oidc", "token_ttl_s": 900, "allowed_audiences_global": ["sts.amazonaws.com"]}
    govern(kms, profile, [{"name": "set_oidc_settings", "args": oidc}])


def _release_approved(client: BaseClient) -> bool:
    try:
        return client.get_release(VIRTUAL_RELEASE_ID).approved
    except NotFoundError:
        return False


def down(
    home: Path, *, kms_repo: Path | None = None, cs_repo: Path | None = None, purge: bool = False
) -> dict[str, Any]:
    path = home / "dev-stack.json"
    if not path.exists():
        raise ConfigError(f"no dev stack recorded in {home}")
    state = loads_object(path.read_bytes())
    stop_sandbox(
        kms_repo or default_repo("kms"), int(state["kms"]["port"]), state_dir=home / "sandboxes" / "kms", purge=purge
    )
    stop_sandbox(
        cs_repo or default_repo("cs"), int(state["cs"]["port"]), state_dir=home / "sandboxes" / "cs", purge=purge
    )
    path.unlink()
    return {"stopped": [state["kms"]["port"], state["cs"]["port"]]}
