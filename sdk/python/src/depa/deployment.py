"""Environment-driven operations: ``depa dc setup --env`` and ``depa scenario deploy``.

``scenario deploy`` chains the per-party steps for pilots and demos where one operator holds
several parties' keys. Every step is idempotent and is the same code the individual commands
run (``depa dataset ...``, ``depa contract ...``, ``depa job submit``, ``depa model ...``), so in
production each party runs only its own steps. Steps for parties whose keys are not held (or
not selected with ``--parties``) are skipped and reported.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import tempfile
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from depa.clients.base import BaseClient
from depa.clients.cs import CsClient
from depa.clients.http import ServiceEndpoint
from depa.clients.kms import KmsClient
from depa.contract.authoring import DatasetTemplate, build_contract, load_dataset_template, map_dataset_id
from depa.contract.recipe import load_recipe
from depa.contract.verify import verify_contract
from depa.contract.yamlio import load_template
from depa.core import dedf
from depa.core.encoding import canonicalize, digest, dumps_pretty, loads_object
from depa.core.ids import dataset_owner
from depa.core.timeutil import parse_rfc3339
from depa.deploy import Runner, aks, cancellable_runner, gcp, subprocess_runner
from depa.deploy.local import run_local
from depa.deploy.release import ReleaseManifest
from depa.deploy.spec import JobSpec
from depa.dev.stack import VIRTUAL_RELEASE_ID, govern, virtual_release
from depa.env import DepaEnv, load_env
from depa.errors import (
    ConfigError,
    DeployCancelled,
    NotFoundError,
    ServiceError,
    StorageError,
    ValidationError,
    VerificationError,
)
from depa.profile import Profile, generate_party
from depa.receipts import receipt_jws_of, verify_execution_receipt
from depa.storage.factory import federation_audience, party_connector
from depa.workflows import (
    build_trust_bundle,
    catalog_resolver,
    decrypt_output,
    encrypt_dataset,
    fetch_output,
    latest_accepted_args,
    pinned_peer_jwks,
    publish_dataset,
    storage_manifest_loader,
    trust_bundle_settings_actions,
    upload_directory,
)

STATE_FILE = "state.json"
PLATFORM_IDS = {"aks": "aci-snp", "gcp": "gcp-cs-tdx", "local": "virtual"}
Log = Callable[[str], object]


def _held(profile: Profile, party_id: str) -> bool:
    return (profile.keys_dir(party_id) / "signing.jwk").exists()


def _public_record(profile: Profile, party_id: str) -> dict[str, Any] | None:
    path = profile.keys_dir(party_id) / "party.json"
    return profile.party_record(party_id) if path.exists() else None


# ---------------------------------------------------------------------------------------------
# depa dc setup


DC_STEPS = ("bootstrap-kms", "bootstrap-cs", "link", "register")


def parse_dc_steps(values: Iterable[str]) -> frozenset[str] | None:
    """``--step`` values (repeatable or comma lists) -> a step set; ``None`` means all."""
    names = {v.strip() for value in values for v in value.split(",") if v.strip()}
    unknown = names - {*DC_STEPS, "all"}
    if unknown:
        raise ConfigError(f"unknown step(s) {sorted(unknown)}; choose from {', '.join(DC_STEPS)}, all")
    return None if not names or "all" in names else frozenset(names)


def dc_setup(
    env: DepaEnv,
    profile: Profile,
    *,
    operator: tuple[str, str] | None = None,
    operator_token: Callable[[str], str | None] | None = None,
    allow_virtual: bool = False,
    steps: frozenset[str] | None = None,
    log: Log = print,
) -> dict[str, Any]:
    """Data Collaborative governance from an environment file, as idempotent steps (default: all):

    * ``bootstrap-kms`` / ``bootstrap-cs``: operator bootstrap of that service (env dc-members,
      majority threshold, ``base_url`` and ``allow_virtual_attestation``); skipped when done.
      ``operator`` (client cert/key) or ``operator_token(service_url)`` authenticate it.
    * ``link`` (both bootstrapped): pin each service's keys in the other, set
      ``trust_bundle_digest`` in both and the KMS OIDC settings.
    * ``register`` (both bootstrapped): register every env party and approve every release in
      ``releases_dir`` in both services (identical ones skipped).

    Each step only reads the env file and the services, so it can run in a fresh process.
    """
    selected = steps or frozenset(DC_STEPS)
    kms, cs = profile.kms_client(), profile.cs_client()
    members = [p.party_id for p in env.parties_with("dc-member")]
    summary: dict[str, Any] = {
        "steps": [s for s in DC_STEPS if s in selected],
        "bootstrapped": [],
        "proposals": {"kms": [], "cs": []},
        "skipped": [],
    }

    for client, which in ((kms, "kms"), (cs, "cs")):
        if f"bootstrap-{which}" not in selected:
            continue
        if client.info().bootstrapped:
            summary["skipped"].append(f"{which}: already bootstrapped")
            continue
        records = [r for m in members if (r := _public_record(profile, m)) is not None]
        if not records:
            raise ConfigError("bootstrap needs the party.json of at least one dc-member party in the env file")
        endpoint = client.http.endpoint
        token = operator_token(endpoint.base) if operator is None and operator_token is not None else None
        if operator is None and not token:
            raise ConfigError(
                f"{which} is not bootstrapped: pass --client-cert/--client-key or an operator bearer token"
            )
        op_endpoint = ServiceEndpoint(
            endpoint.name, endpoint.url, ca_pem=endpoint.ca_pem, client_cert=operator, bearer_token=token
        )
        op_client: BaseClient = KmsClient(op_endpoint) if which == "kms" else CsClient(op_endpoint)
        settings = {"base_url": client.base_url, "allow_virtual_attestation": allow_virtual}
        op_client.gov_bootstrap(records, {"default": "majority"}, settings)
        summary["bootstrapped"].append(which)
        log(f"{which}: bootstrapped with members {members}")

    governing = selected & {"link", "register"}
    if not governing:
        return summary
    infos = {"kms": kms.info(), "cs": cs.info()}
    for which in ("kms", "cs"):
        if not infos[which].bootstrapped:
            step = sorted(governing)[0]
            raise StageError(f"{step} needs both services bootstrapped: run --step bootstrap-{which} first")
    held_members = [m for m in members if _held(profile, m)]
    if not held_members:
        raise ConfigError("link/register need the keys of at least one dc-member party")

    def act(client: BaseClient, which: str, actions: list[dict[str, Any]]) -> None:
        if not actions:
            return
        result = govern(client, profile, actions, held_members, require_accept=False)
        summary["proposals"][which].append(
            {
                "proposal_id": result.get("proposal_id"),
                "state": result.get("state"),
                "actions": [a["name"] for a in actions],
            }
        )
        log(f"{which}: {[a['name'] for a in actions]} -> {result.get('state')}")

    if "link" in selected:
        for client, which, peer_url, peer in ((kms, "kms", cs.base_url, "cs"), (cs, "cs", kms.base_url, "kms")):
            actions: list[dict[str, Any]] = []
            peer_info = infos[peer]
            if pinned_peer_jwks(client, peer_info.service) != list(peer_info.jwks):
                pin = {"name": peer_info.service, "url": peer_url, "jwks": list(peer_info.jwks)}
                actions.append({"name": "set_peer_service", "args": pin})
            if which == "kms":
                actions += _oidc_actions(env, client, summary)
            act(client, which, actions)
        for client, which in ((kms, "kms"), (cs, "cs")):  # after the pins: the bundle includes both JWKS
            act(client, which, trust_bundle_settings_actions(profile, client, kms, cs))

    if "register" in selected:
        for client, which in ((kms, "kms"), (cs, "cs")):
            actions = []
            for p in env.parties:
                record = _public_record(profile, p.party_id)
                if record is None:
                    summary["skipped"].append(f"{p.party_id}: no party.json in {p.key_dir}")
                    continue
                if "dc-member" in p.roles:
                    continue  # members only via bootstrap / set_member (unanimous)
                try:
                    if client.get_party(p.party_id).raw == record:
                        continue
                except NotFoundError:
                    pass
                actions.append({"name": "set_party", "args": record})
            for manifest in env.releases():
                try:
                    entry = client.get_release(manifest.release_id)
                    if entry.approved and digest(entry.release) == digest(manifest.release):
                        continue
                except NotFoundError:
                    pass
                actions.append({"name": "approve_ccr_release", "args": manifest.release})
            if which == "kms":
                # The output audiences come from collaborators deployed after link ran, so refresh
                # the OIDC settings here too (no proposal when unchanged).
                actions += _oidc_actions(env, client, summary)
            act(client, which, actions)
    return summary


def _oidc_actions(env: DepaEnv, kms: BaseClient, summary: dict[str, Any]) -> list[dict[str, Any]]:
    issuer = env.service("kms").get("oidc_issuer")
    if not issuer:
        summary["skipped"].append("kms: collaborative.kms.oidc_issuer not set; OIDC settings unchanged")
        return []
    audiences = sorted(
        {
            a
            for loc in (env.raw.get("outputs") or {}).values()
            if loc.get("federation") and (a := federation_audience(loc["connector"], loc["federation"]))
        }
    )
    oidc = {"issuer_url": str(issuer).rstrip("/"), "token_ttl_s": 900, "allowed_audiences_global": audiences}
    if latest_accepted_args(kms, "set_oidc_settings") == oidc:
        return []
    return [{"name": "set_oidc_settings", "args": oidc}]


# ---------------------------------------------------------------------------------------------
# depa scenario deploy


@dataclass(frozen=True)
class DeployOptions:
    platform: str  # aks | gcp | local
    parties: frozenset[str] | None = None  # None = every party whose keys are held
    storage_token: str | None = None
    output_token: str | None = None
    prepare: str = "auto"  # auto | always | never
    run: bool = True
    wait_timeout_s: float | None = None
    trainer_cmd: str | None = None  # local only (dev)
    work_dir: Path | None = None
    cancel: threading.Event | None = None  # set to stop the deployment promptly (DeployCancelled)
    stages: frozenset[str] | None = None  # subset of STAGES; None = all (one-shot behaviour)


def _env_tdc(env: DepaEnv) -> str:
    tdcs = env.parties_with("tdc")
    if len(tdcs) != 1:
        raise ConfigError("the environment must define exactly one party with role tdc")
    return tdcs[0].party_id


def locate_dataset_template(
    env: DepaEnv,
    template: DatasetTemplate,
    *,
    kms_url: str | None = None,
    tdc: str | None = None,
    platform: str | None = None,
) -> DatasetTemplate:
    """Place a dataset template under its owner's storage in ``env``.

    Location = ``storage.<owner>.uri + <name>/<version>/`` with the owner's federation block;
    ``kms.url`` = the environment's KMS. The release policy is narrowed to the environment's TDC
    and the owner's storage audience, and allows ``platform`` when given.
    """
    owner = dataset_owner(template.dataset_id)
    storage = env.storage(owner)
    name = template.dataset_id.split("/", 1)[1]
    location = {k: v for k, v in storage.items() if k in ("connector", "region", "endpoint")}
    location["uri"] = f"{storage['uri']}{name}/{template.version}/"
    descriptor = dict(template.descriptor, location=location)
    descriptor.pop("federation", None)
    if storage.get("federation"):
        descriptor["federation"] = storage["federation"]
    descriptor["kms"] = {
        "url": kms_url or str(env.service("kms")["url"]),
        "kid": template.descriptor.get("kms", {}).get("kid", ""),
    }
    policy = dict(template.policy) if template.policy else None
    if policy is not None:
        platforms = list(policy.get("allowed_platforms", []))
        if platform is not None and platform not in platforms:
            platforms.append(platform)
        policy["allowed_platforms"] = platforms
        policy["allowed_tdcs"] = [tdc or _env_tdc(env)]
        federation = storage.get("federation")
        audience = federation_audience(location["connector"], federation) if federation else None
        policy["allowed_audiences"] = [audience] if audience else []
    return DatasetTemplate(descriptor=descriptor, policy=policy, source=template.source)


def compute_party_map(native_owners: Iterable[str], native_tdc: str, env: DepaEnv) -> dict[str, str]:
    """Map a scenario's native party ids onto the environment's parties (party slots).

    Identity when every native id (dataset owners and the TDC) is a party of ``env``. Otherwise:
    the native TDC -> the env's party with role ``tdc``; the other native dataset owners, sorted by
    id -> the env's ``tdp`` parties other than the TDC, sorted by id. More owners than slots is a
    configuration error (exit 2).
    """
    owners = set(native_owners)
    env_ids = {p.party_id for p in env.parties}
    if owners | {native_tdc} <= env_ids:
        return {pid: pid for pid in sorted(owners | {native_tdc})}
    tdc = _env_tdc(env)
    others = sorted(owners - {native_tdc})
    slots = sorted(p.party_id for p in env.parties_with("tdp") if p.party_id != tdc)
    if len(others) > len(slots):
        raise ConfigError(
            f"the scenario has {len(others)} data providers {others} but the environment offers only "
            f"{len(slots)} tdp slots {slots}"
        )
    return {native_tdc: tdc, **dict(zip(others, slots, strict=False))}


STAGES = ("keys", "data", "contract", "train", "model")


class StageError(ConfigError):
    """A stage's prerequisite has not run (exit 2)."""

    code = "stage_prerequisite"


def parse_stages(values: Iterable[str]) -> frozenset[str] | None:
    """``--stage`` values (repeatable or comma lists) -> a stage set; ``None`` means all."""
    names = {v.strip() for value in values for v in value.split(",") if v.strip()}
    unknown = names - {*STAGES, "all"}
    if unknown:
        raise ConfigError(f"unknown stage(s) {sorted(unknown)}; choose from {', '.join(STAGES)}, all", code="bad_stage")
    return None if not names or "all" in names else frozenset(names)


class _Deployer:
    def __init__(self, scenario: Path, env: DepaEnv, profile: Profile, opts: DeployOptions, log: Log) -> None:
        self.scenario, self.env, self.profile, self.opts, self.log = scenario, env, profile, opts, log
        self.kms: KmsClient = profile.kms_client()
        self.cs: CsClient = profile.cs_client()
        self.platform = PLATFORM_IDS[opts.platform]
        self.tdc = _env_tdc(env)
        native = scenario_parties(scenario, {**os.environ, "DEPA_KMS_URL": self.kms.base_url})
        native_tdc = next(p for p, roles in native.items() if "tdc" in roles)
        native_owners = [p for p, roles in native.items() if "tdp" in roles]
        self.party_map = compute_party_map(native_owners, native_tdc, env)
        self.mapped = any(k != v for k, v in self.party_map.items())
        self.native_of = {v: k for k, v in self.party_map.items()}
        # Published versions of mapped datasets carry the scenario name, so another scenario mapped
        # onto the same slot can never silently reuse them.
        self.version_suffix = f".{scenario.name}" if self.mapped else ""
        self.template_env = {**os.environ, "DEPA_TDC": self.tdc, "DEPA_KMS_URL": self.kms.base_url}
        self.work = opts.work_dir or Path(tempfile.mkdtemp(prefix=f"depa-{scenario.name}-"))
        self.state_path = self.work / STATE_FILE
        self.state = self.load_state()
        self.summary: dict[str, Any] = {
            "scenario": scenario.name,
            "platform": self.platform,
            "steps": [],
            "skipped": [],
            "party_map": self.party_map,
        }
        if self.mapped:
            log(f"party map {self.party_map} (dataset versions suffixed {self.version_suffix!r})")

    def load_state(self) -> dict[str, Any]:
        """Resume state from ``--work-dir/state.json`` (only if it belongs to this scenario/platform)."""
        if not self.state_path.exists():
            return {}
        state = loads_object(self.state_path.read_bytes())
        if state.get("scenario") != self.scenario.name or state.get("platform") != self.platform:
            return {}
        return state

    def save_state(self) -> None:
        self.state.update(scenario=self.scenario.name, platform=self.platform, party_map=self.party_map)
        self.work.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(dumps_pretty(self.state))

    def resolve_contract(self) -> dict[str, Any]:
        """The active contract: from state, else an identical usable contract in the CS."""
        contract_id = self.state.get("contract_id")
        if contract_id:
            record = self.cs.get_contract(str(contract_id))
        else:
            found = self.find_reusable(self.candidate())
            if found is None:
                raise StageError("no contract for this scenario: run --stage contract first")
            record = self.cs.get_contract(str(found["contract_id"]))
        if record.get("state") != "active":
            raise StageError(
                f"contract {record['contract']['contract_id']} is {record.get('state')}, not active: "
                "run --stage contract (all signatures) first"
            )
        self.state["contract_id"] = record["contract"]["contract_id"]
        return dict(record["contract"])

    def resolve_run(self, contract: Mapping[str, Any]) -> dict[str, Any]:
        """The completed run: from state, else the latest completed run of the contract."""
        run_id = self.state.get("run_id")
        if run_id:
            run = self.cs.get_run(str(run_id))
        else:
            done = [r for r in self.cs.list_runs(str(contract["contract_id"])) if r.get("state") == "completed"]
            if not done:
                raise StageError("no completed run for this contract: run --stage train first")
            run = max(done, key=lambda r: r.get("terminal_at") or r.get("authorized_at") or 0)
        if run.get("state") != "completed":
            raise StageError(f"run {run.get('run_id')} is {run.get('state')}: run --stage train first")
        self.state["run_id"] = run["run_id"]
        return dict(run)

    def acts_for(self, party_id: str) -> bool:
        selected = self.opts.parties is None or party_id in self.opts.parties
        return selected and _held(self.profile, party_id)

    def check_cancel(self) -> None:
        if self.opts.cancel is not None and self.opts.cancel.is_set():
            raise DeployCancelled("deployment cancelled")

    def pause(self, seconds: float) -> None:
        """Sleep that wakes up (and raises ``DeployCancelled``) as soon as cancellation is requested."""
        if self.opts.cancel is None:
            time.sleep(seconds)
        elif self.opts.cancel.wait(seconds):
            raise DeployCancelled("deployment cancelled")

    def runner(self) -> Runner:
        return cancellable_runner(self.opts.cancel) if self.opts.cancel is not None else subprocess_runner

    def step(self, what: str, **detail: Any) -> None:
        self.check_cancel()
        self.summary["steps"].append({"step": what, **detail})
        self.log(f"{what} {detail if detail else ''}".rstrip())

    # ---- TDP --------------------------------------------------------------------------------

    def dataset_template(self, path: Path) -> DatasetTemplate:
        t = load_dataset_template(path, self.template_env)
        if self.mapped:
            mapped_id = map_dataset_id(t.dataset_id, self.party_map)
            kid = str(t.descriptor.get("kms", {}).get("kid", ""))
            descriptor = dict(
                t.descriptor,
                dataset_id=mapped_id,
                version=t.version + self.version_suffix,
                kms=dict(t.descriptor.get("kms") or {}, kid=mapped_id + kid[len(t.dataset_id) :]),
            )
            t = DatasetTemplate(descriptor=descriptor, policy=t.policy, source=t.source)
        return locate_dataset_template(
            self.env,
            t,
            kms_url=self.kms.base_url,
            tdc=self.tdc,
            platform=self.platform,
        )

    def prepare_data(self, templates: Sequence[DatasetTemplate]) -> None:
        """Run ``prepare/prepare.sh --party <owner>`` for each owner whose source data is missing
        (``--prepare auto``) or always (``--prepare always``); ``prepare_all.sh`` as a fallback."""

        def missing(t: DatasetTemplate) -> bool:
            return t.source is not None and not (t.source.path.is_dir() and any(t.source.path.iterdir()))

        if self.opts.prepare == "never":
            return
        # prepare.sh is the scenario's own script: it takes the NATIVE party id.
        owners = sorted(
            {
                self.native_of.get(dataset_owner(t.dataset_id), dataset_owner(t.dataset_id))
                for t in templates
                if self.opts.prepare == "always" or missing(t)
            }
        )
        per_party = self.scenario / "prepare" / "prepare.sh"
        fallback = self.scenario / "prepare" / "prepare_all.sh"
        runs = (
            [[str(per_party), "--party", o] for o in owners]
            if per_party.exists()
            else ([[str(fallback)]] if owners and fallback.exists() else [])
        )
        if owners and not runs:
            raise ConfigError(f"source data missing for {owners} and {self.scenario}/prepare has no script")
        for argv in runs:
            self.check_cancel()
            proc = self.runner()(argv)
            if proc.returncode != 0:
                raise ServiceError(
                    f"{' '.join(argv[1:]) or 'prepare'} failed:\n{proc.stderr.decode('utf-8', 'replace')[-3000:]}",
                    service="prepare",
                    code="prepare_failed",
                )
            self.step("prepare", argv=[Path(argv[0]).name, *argv[1:]])

    def stage_keys(self, templates: Sequence[DatasetTemplate]) -> None:
        """TDPs: dataset key in the KMS (created once) and the release policy (set when changed)."""
        for t in templates:
            owner = dataset_owner(t.dataset_id)
            if not self.acts_for(owner):
                self.summary["skipped"].append(f"{t.dataset_id}: keys for {owner} (keys not held/selected)")
                continue
            signer = self.profile.signer(owner)
            try:
                self.kms.get_dataset(t.dataset_id)
            except NotFoundError:
                self.kms.create_dataset(signer, t.dataset_id, str(t.descriptor.get("name", "")))
                self.step("dataset.create-key", dataset_id=t.dataset_id)
            if t.policy is not None:
                current = self.kms.get_dataset(t.dataset_id).policy or {}
                if any(current.get(k) != v for k, v in t.policy.items()):
                    self.kms.set_policy(signer, t.dataset_id, t.policy)
                    self.step("dataset.policy-set", dataset_id=t.dataset_id)

    def stage_data(self, templates: Sequence[DatasetTemplate]) -> None:
        """TDPs: prepare (if needed), encrypt, upload and publish versions not yet in the catalog."""
        pending = []
        for t in templates:
            owner = dataset_owner(t.dataset_id)
            if not self.acts_for(owner):
                self.summary["skipped"].append(f"{t.dataset_id}: data for {owner} (keys not held/selected)")
                continue
            try:
                published = self.cs.get_dataset_descriptor(t.dataset_id, t.version)
            except NotFoundError:
                pending.append(t)
                continue
            if published.get("name") != t.descriptor.get("name"):
                raise ValidationError(
                    f"{t.dataset_id}@{t.version} is already published with different content "
                    f"({published.get('name')!r}); refusing to reuse it",
                    code="dataset_version_collision",
                )
            self.state.setdefault("published", {})[t.dataset_id] = t.version
        for t in pending:  # prerequisite: stage keys (key and policy exist)
            try:
                record = self.kms.get_dataset(t.dataset_id)
            except NotFoundError as exc:
                raise StageError(f"{t.dataset_id} has no KMS key: run --stage keys first") from exc
            if t.policy is not None and record.policy is None:
                raise StageError(f"{t.dataset_id} has no release policy: run --stage keys first")
        if pending:
            self.prepare_data(pending)
        for t in pending:
            signer = self.profile.signer(dataset_owner(t.dataset_id))
            out = self.work / "encrypted" / t.dataset_id / t.version
            if out.exists():  # left over by an interrupted run: re-encrypt from scratch
                shutil.rmtree(out)
            manifest = encrypt_dataset(t, kms=self.kms, signer=signer, out_dir=out)
            self.step("dataset.encrypt", dataset_id=t.dataset_id, manifest_digest=manifest.digest)
            upload_directory(
                party_connector(t.descriptor["location"], access_token=self.opts.storage_token), out, manifest
            )
            self.step("dataset.upload", dataset_id=t.dataset_id, location=t.descriptor["location"]["uri"])
            publish_dataset(self.cs, signer, t, out)
            self.step("dataset.publish", dataset_id=t.dataset_id, version=t.version)
            self.state.setdefault("published", {})[t.dataset_id] = t.version

    # ---- TDC: contract --------------------------------------------------------------------

    def releases(self, families: set[str]) -> list[ReleaseManifest]:
        found = [m for m in self.env.releases() if m.platform == self.platform and families <= set(m.families)]
        return found

    def candidate(self) -> dict[str, Any]:
        """The contract this deployment would propose (fresh id/validity; same substance on re-runs)."""
        recipe = load_recipe(self.scenario / "recipe.yaml")
        families = {str(recipe.get("family"))}
        release_ids = [m.release_id for m in self.releases(families)]
        if not release_ids and self.platform == "virtual":
            release_ids = [VIRTUAL_RELEASE_ID]
        if not release_ids:
            raise ConfigError(f"no release in releases_dir for {self.platform} with family {sorted(families)}")
        env = {
            **self.template_env,
            "DEPA_PLATFORMS": f"[{self.platform}]",
            "DEPA_CCR_RELEASES": "[" + ", ".join(release_ids) + "]",
        }
        output_location = self.env.outputs(self.tdc)

        def customize(doc: dict[str, Any]) -> None:
            doc["output"]["location"] = {k: v for k, v in output_location.items() if v not in (None, {})}

        try:
            return build_contract(
                self.scenario / "contract.yaml",
                env=env,
                resolve_manifest=catalog_resolver(self.cs),
                customize=customize,
                party_map=self.party_map if self.mapped else None,
                version_suffix=self.version_suffix,
            )
        except NotFoundError as exc:
            raise StageError(f"datasets are not published yet: run --stage data first ({exc})") from exc

    def contract(self) -> dict[str, Any]:
        candidate = self.candidate()
        existing = self.find_reusable(candidate)
        if existing is not None:
            self.step("contract.reuse", contract_id=existing["contract_id"])
            return existing
        self.cs.propose_contract(self.profile.signer(self.tdc), candidate)
        self.step("contract.propose", contract_id=candidate["contract_id"], contract_digest=digest(candidate))
        return candidate

    def find_reusable(self, candidate: Mapping[str, Any]) -> dict[str, Any] | None:
        """An existing usable contract of this TDC with identical substance (everything but id/validity)."""

        def substance(c: Mapping[str, Any]) -> bytes:
            return canonicalize({k: v for k, v in c.items() if k not in ("contract_id", "validity")})

        listing = self.cs.list_contracts(self.tdc)
        items = listing.get("contracts", []) if isinstance(listing, dict) else listing
        now = time.time()
        for item in items or []:
            record = item if "contract" in item else self.cs.get_contract(str(item.get("contract_id")))
            c = record.get("contract", {})
            if record.get("state") not in ("draft", "signed", "active"):
                continue
            if record.get("runs_used", 0) >= c.get("computation", {}).get("max_runs", 0):
                continue
            if parse_rfc3339(c["validity"]["not_after"]).timestamp() < now + 3600:
                continue
            if substance(c) == substance(candidate):
                return dict(c)
        return None

    def sign(self, contract: Mapping[str, Any]) -> dict[str, Any]:
        contract_id = str(contract["contract_id"])
        record = self.cs.get_contract(contract_id)
        signed = {s.get("party") for s in record.get("signatures", [])}
        owners = sorted({dataset_owner(d["dataset_id"]) for d in contract["datasets"]})
        required = list(dict.fromkeys([self.tdc, *owners]))  # a TDC may also own a dataset
        for party in required:
            if party in signed:
                continue
            if not self.acts_for(party):
                self.summary["skipped"].append(f"contract.sign by {party} (keys not held/selected)")
                continue
            report = verify_contract(
                contract,
                party_id=party,
                kms=self.kms,
                cs=self.cs,
                record=record,
                manifest_loader=storage_manifest_loader(self.opts.storage_token),
            )
            if not report.ok:
                raise VerificationError(f"{party} refuses to sign: {report.errors}", code="verify_failed")
            self.cs.sign_contract(self.profile.signer(party), contract_id, report.contract_digest)
            self.step("contract.sign", party=party, warnings=len(report.warnings))
        deadline = time.monotonic() + 60
        while True:
            record = self.cs.get_contract(contract_id)
            if record.get("state") == "active" or time.monotonic() > deadline:
                return record
            if record.get("state") == "draft":
                return record  # signatures missing (held by other parties)
            self.pause(2)

    # ---- job ------------------------------------------------------------------------------

    def run(self, contract: Mapping[str, Any]) -> dict[str, Any]:
        contract_id = str(contract["contract_id"])
        computation = contract["computation"]
        max_minutes = int(computation["max_duration_minutes"])
        spec = JobSpec(
            contract_id=contract_id,
            recipe_digest=str(computation["recipes"][0]["digest"]),
            cs_url=self.cs.base_url,
            kms_url=self.kms.base_url,
            platform=self.platform,
            max_duration_minutes=max_minutes,
            trust_bundle=build_trust_bundle(self.profile, self.kms, self.cs),
        ).ensure_name()
        before = {r.get("run_id") for r in self.cs.list_runs(contract_id)}
        if self.opts.platform == "local":
            extra = {"DEPA_WORK_DIR": str(self.work / "ccr")}
            (self.work / "ccr").mkdir(parents=True, exist_ok=True)
            if self.opts.trainer_cmd:
                extra["DEPA_TRAINER_CMD"] = self.opts.trainer_cmd
            job = run_local(spec, image=None, env_extra=extra, runner=self.runner())
            self.step("job.local", name=job.name, returncode=job.returncode)
        else:
            manifest = next(
                (
                    m
                    for m in self.releases({str(computation["recipes"][0]["document"].get("family"))})
                    if m.release_id in computation["ccr_releases"]
                ),
                None,
            )
            if manifest is None:
                raise ConfigError("no release manifest in releases_dir matches the contract")
            if self.opts.platform == "aks":
                cfg = self.env.compute("aks")
                name = aks.submit(
                    spec,
                    manifest,
                    namespace=cfg["namespace"],
                    context=cfg.get("context"),
                    contract_max_duration_minutes=max_minutes,
                )
            else:
                cfg = self.env.compute("gcp")
                name = gcp.submit(
                    spec,
                    manifest,
                    project=cfg["project"],
                    zone=cfg["zone"],
                    service_account=cfg["service_account"],
                    network=cfg.get("network"),
                    subnet=cfg.get("subnetwork"),
                    machine_type=cfg.get("machine_type", "c3-standard-4"),
                    contract_max_duration_minutes=max_minutes,
                )
            self.step("job.submit", name=name, platform=self.platform)
        timeout = self.opts.wait_timeout_s or (max_minutes * 60 + 900)
        deadline = time.monotonic() + timeout
        while True:
            runs = [r for r in self.cs.list_runs(contract_id) if r.get("run_id") not in before]
            done = [r for r in runs if r.get("state") in ("completed", "failed")]
            if done:
                run = done[0]
                break
            if time.monotonic() > deadline:
                raise ServiceError("timed out waiting for the run", service="cs", code="run_timeout")
            self.pause(5 if self.opts.platform == "local" else 20)
        self.step("run.finished", run_id=run["run_id"], state=run["state"])
        if run["state"] != "completed":
            # The run list omits events; the run record carries the CCR's coarse failure event
            # ({code, stage}), which is safe to show (no data or log content).
            events = self.cs.get_run(str(run["run_id"])).get("events", [])
            failure = next((e.get("data") for e in events if e.get("type") == "failed"), None) or {}
            reason = ", ".join(f"{k}={failure[k]}" for k in ("stage", "code") if failure.get(k)) or "no failure event"
            raise ServiceError(f"run {run['run_id']} failed ({reason})", service="ccr", code="run_failed")
        return dict(run)

    def outputs(self, run: Mapping[str, Any], contract: Mapping[str, Any]) -> dict[str, Any]:
        full = self.cs.get_run(str(run["run_id"]))
        check = verify_execution_receipt(
            receipt_jws_of(full), self.cs.info().key_lookup(), self.kms.info().key_lookup()
        )
        if not check.ok:
            raise VerificationError(f"receipt invalid: {check.problems}", code="bad_receipt")
        self.step("receipt.verify", run_id=run["run_id"])
        # Public evidence only (the workflow uploads `*receipt*` files from the work dir).
        receipt_file = self.work / f"receipt-{run['run_id']}.json"
        receipt_file.parent.mkdir(parents=True, exist_ok=True)
        receipt_file.write_text(
            dumps_pretty(
                {
                    "run_id": run["run_id"],
                    "contract_id": contract["contract_id"],
                    "receipt_jws": receipt_jws_of(full),
                    "signed_claims": check.signed_claims,
                }
            )
        )
        key = self.profile.hpke_key(self.tdc)
        files: dict[str, list[str]] = {}
        for entry in check.claims["outputs"]:
            enc = self.work / "outputs" / str(run["run_id"]) / entry["name"] / "encrypted"
            fetch_output(
                check.claims,
                entry["name"],
                enc,
                location=contract["output"]["location"],
                access_token=self.opts.output_token,
            )
            run_key = full.get("run_key")
            written = decrypt_output(
                enc,
                enc.parent / "plain",
                key=key,
                recipient=f"party:{self.tdc}#{key.kid}",
                expected_digest=entry["manifest_digest"],
                run_key=run_key if isinstance(run_key, dict) else None,
            )
            files[entry["name"]] = [str(f) for f in written]
            self.step("model.decrypt", output=entry["name"], files=len(written))
        return {
            "run_id": run["run_id"],
            "receipt_file": str(receipt_file),
            "metrics": check.claims.get("metrics"),
            "privacy_spent": check.claims.get("privacy_spent"),
            "outputs": files,
        }


def scenario_parties(scenario: Path, env_vars: Mapping[str, str] | None = None) -> dict[str, set[str]]:
    """Parties a scenario needs and their roles (dataset owners: tdp; the contract's TDC: tdc)."""
    variables = dict(os.environ if env_vars is None else env_vars)
    roles: dict[str, set[str]] = {}
    for path in sorted((scenario / "datasets").glob("*.yaml")):
        owner = dataset_owner(load_dataset_template(path, variables).dataset_id)
        roles.setdefault(owner, set()).add("tdp")
    tdc = str(load_template(scenario / "contract.yaml", variables)["parties"]["tdc"])
    roles.setdefault(tdc, set()).add("tdc")
    return roles


def environment_from_profile(profile: Profile, *, scenarios: Sequence[Path] = ()) -> DepaEnv:
    """The environment equivalent of a ``depa dev up`` profile (development only).

    Parties are every key directory in the profile plus those the given scenarios need (keys are
    generated, roles widened in ``party.json`` when required); storage and outputs use the file
    connector under ``$DEPA_HOME/dev-storage``; ``releases_dir`` holds the ``virtual`` release.
    Register the parties with ``dc_setup`` (idempotent) before deploying.
    """
    if not (profile.kms.url and profile.kms.cert and profile.cs.url and profile.cs.cert):
        raise ConfigError("the profile has no services: run `depa dev up` first")
    keys_root = profile.home / "keys"
    needed: dict[str, set[str]] = {}
    for scenario in scenarios:
        for party_id, roles in scenario_parties(scenario, {**os.environ, "DEPA_KMS_URL": profile.kms.url}).items():
            needed.setdefault(party_id, set()).update(roles)
    existing = sorted(p.name for p in keys_root.glob("*") if (p / "party.json").exists()) if keys_root.exists() else []
    parties: list[dict[str, Any]] = []
    for party_id in sorted(set(existing) | set(needed)):
        key_dir = profile.keys_dir(party_id)
        roles = needed.get(party_id, set())
        if not (key_dir / "party.json").exists():
            generate_party(profile, party_id=party_id, name=party_id, roles=sorted(roles))
        record = profile.party_record(party_id)
        merged = sorted(set(record["roles"]) | roles)
        if merged != sorted(record["roles"]):
            record["roles"] = merged
            (key_dir / "party.json").write_text(dumps_pretty(record))
        parties.append({"party_id": party_id, "roles": merged, "key_dir": str(key_dir)})
    storage_root = (profile.home / "dev-storage").resolve()
    storage = {
        p["party_id"]: {"connector": "file", "uri": (storage_root / str(p["party_id"])).as_uri() + "/"}
        for p in parties
        if {"tdp", "tdc"} & set(p["roles"])
    }
    outputs = {
        p["party_id"]: {"connector": "file", "uri": (storage_root / "outputs" / str(p["party_id"])).as_uri() + "/"}
        for p in parties
        if "tdc" in p["roles"]
    }
    releases = profile.home / "dev-releases"
    releases.mkdir(parents=True, exist_ok=True)
    (releases / f"{VIRTUAL_RELEASE_ID}.json").write_text(
        dumps_pretty(
            {
                "release": virtual_release(),
                "deployment": {
                    "image": "local/depa-ccr@sha256:" + "0" * 64,
                    "resources": {"cpu": 4, "memory_gb": 16},
                    "env_allowlist": [
                        "DEPA_CONTRACT_ID",
                        "DEPA_RECIPE_DIGEST",
                        "DEPA_CS_URL",
                        "DEPA_KMS_URL",
                        "DEPA_PLATFORM",
                        "DEPA_TRUST_BUNDLE",
                        "DEPA_LOG_LEVEL",
                    ],
                },
            }
        )
    )
    doc = {
        "collaborative": {
            "kms": {
                "url": profile.kms.url,
                "service_cert": str(Path(profile.kms.cert).expanduser().resolve()),
                "oidc_issuer": profile.kms.url.rstrip("/") + "/oidc",
            },
            "cs": {"url": profile.cs.url, "service_cert": str(Path(profile.cs.cert).expanduser().resolve())},
        },
        "parties": parties,
        "storage": storage,
        "outputs": outputs,
        "releases_dir": str(releases),
    }
    path = profile.home / "dev-env.yaml"
    path.write_text(yaml.safe_dump(doc, sort_keys=False))
    return load_env(path)


def deploy_scenario(
    scenario: Path, env: DepaEnv, profile: Profile, opts: DeployOptions, log: Log = print
) -> dict[str, Any]:
    """Run the selected stages (default: all, one-shot). Stages are idempotent and resumable from
    ``<work_dir>/state.json``, falling back to the services when it is missing."""
    if opts.platform not in PLATFORM_IDS:
        raise ValidationError(f"unknown platform {opts.platform}", code="bad_platform")
    if opts.cancel is not None and opts.cancel.is_set():
        raise DeployCancelled("deployment cancelled")
    d = _Deployer(scenario, env, profile, opts, log)
    templates = [d.dataset_template(p) for p in sorted((scenario / "datasets").glob("*.yaml"))]
    if not templates:
        raise ConfigError(f"{scenario}/datasets has no dataset templates")
    staged = opts.stages is not None
    stages = opts.stages or frozenset(STAGES)
    if staged:
        d.summary["stages"] = {}

    def done(stage: str, **detail: Any) -> None:
        d.save_state()
        if staged:
            d.summary["stages"][stage] = {"status": "done", **detail}

    d.check_cancel()
    if "keys" in stages:
        d.stage_keys(templates)
        done("keys")
    if "data" in stages:
        d.stage_data(templates)
        done("data", published=d.state.get("published", {}))
    later = stages & {"contract", "train", "model"}
    if later and not d.acts_for(d.tdc):
        d.summary["skipped"].append(f"contract, job and outputs (TDC {d.tdc} keys not held/selected)")
        d.summary["status"] = "datasets-published"
        return d.summary
    if "contract" in stages:
        contract = d.contract()
        record = d.sign(contract)
        d.state["contract_id"] = contract["contract_id"]
        d.summary["contract_id"] = contract["contract_id"]
        d.summary["contract_state"] = record.get("state")
        done("contract", contract_id=contract["contract_id"], state=record.get("state"))
        if record.get("state") != "active":
            d.summary["status"] = "awaiting-signatures"
            return d.summary
        if not opts.run and not staged:
            d.summary["status"] = "contract-active"
            return d.summary
    if "train" in stages or "model" in stages:
        contract = d.resolve_contract()
        d.summary["contract_id"] = contract["contract_id"]
        if "train" in stages:
            d.check_cancel()
            run = d.run(contract)
            d.state["run_id"] = run["run_id"]
            done("train", run_id=run["run_id"], state=run["state"])
        if "model" in stages:
            run = d.resolve_run(contract)
            d.summary["result"] = d.outputs(run, contract)
            done("model", run_id=run["run_id"], receipt_file=d.summary["result"]["receipt_file"])
    d.summary["status"] = "succeeded" if not staged or "model" in stages else "stages-completed"
    return d.summary


# ---------------------------------------------------------------------------------------------
# depa scenario teardown / check


@dataclass(frozen=True)
class TeardownOptions:
    work_dir: Path | None = None
    keep_outputs: bool = False
    output_token: str | None = None
    parties: frozenset[str] | None = None


TEARDOWN_REASON = "scenario teardown"


def scenario_contracts(d: _Deployer, templates: Sequence[DatasetTemplate]) -> list[dict[str, Any]]:
    """CS records of this scenario's contracts: proposed by the env's TDC over exactly the scenario's
    (mapped) dataset versions, plus any contract recorded in the work dir state."""
    expected = {(t.dataset_id, t.version) for t in templates}
    listing = d.cs.list_contracts(d.tdc)
    items = listing.get("contracts", []) if isinstance(listing, dict) else listing
    records: dict[str, dict[str, Any]] = {}
    for item in items or []:
        record = item if "contract" in item else d.cs.get_contract(str(item.get("contract_id")))
        contract = record.get("contract", {})
        datasets = {(x.get("dataset_id"), x.get("version")) for x in contract.get("datasets", [])}
        if contract.get("parties", {}).get("tdc") == d.tdc and datasets == expected:
            records[str(contract["contract_id"])] = record
    recorded = d.state.get("contract_id")
    if recorded and str(recorded) not in records:
        with contextlib.suppress(NotFoundError):
            records[str(recorded)] = d.cs.get_contract(str(recorded))
    return [records[k] for k in sorted(records)]


def teardown_scenario(
    scenario: Path, env: DepaEnv, profile: Profile, opts: TeardownOptions, log: Log = print
) -> dict[str, Any]:
    """Remove one scenario's runs from the collaboration, keeping collaborators and DC services:
    the TDC revokes the scenario's live contracts and (unless ``keep_outputs``) deletes their outputs.
    Datasets, keys and policies stay, so a re-run reuses them and proposes a new contract. Idempotent."""
    d = _Deployer(
        scenario, env, profile, DeployOptions(platform="local", work_dir=opts.work_dir, parties=opts.parties), log
    )
    templates = [d.dataset_template(p) for p in sorted((scenario / "datasets").glob("*.yaml"))]
    summary: dict[str, Any] = {
        "scenario": scenario.name,
        "party_map": d.party_map,
        "revoked": [],
        "inactive": [],
        "deleted_outputs": [],
        "skipped": [],
    }
    if not d.acts_for(d.tdc):
        raise ConfigError(f"teardown is performed by the TDC: keys for {d.tdc} are not held/selected")
    signer = profile.signer(d.tdc)
    for record in scenario_contracts(d, templates):
        contract = record["contract"]
        contract_id = str(contract["contract_id"])
        if record.get("state") in ("draft", "signed", "active"):
            d.cs.revoke_contract(signer, contract_id, TEARDOWN_REASON)
            summary["revoked"].append(contract_id)
            log(f"contract {contract_id} revoked")
        else:
            summary["inactive"].append({"contract_id": contract_id, "state": record.get("state")})
        if opts.keep_outputs:
            continue
        for run in d.cs.list_runs(contract_id):
            full = d.cs.get_run(str(run["run_id"])) if "receipt" not in run else run
            receipt = full.get("receipt") if isinstance(full.get("receipt"), dict) else None
            for entry in (receipt or {}).get("outputs", []):
                deleted = _delete_output(contract["output"]["location"], entry, opts.output_token)
                if deleted:
                    summary["deleted_outputs"].append(
                        {
                            "contract_id": contract_id,
                            "run_id": full["run_id"],
                            "output": entry["name"],
                            "objects": deleted,
                        }
                    )
                    log(f"deleted {deleted} objects of {contract_id}/{full['run_id']}/{entry['name']}")
    d.state.pop("contract_id", None)
    d.state.pop("run_id", None)
    if d.state_path.exists():
        d.save_state()
    return summary


def _delete_output(location: Mapping[str, Any], entry: Mapping[str, Any], token: str | None) -> int:
    """Delete one output (manifest first: readers treat it as the commit marker). Idempotent."""
    base = {k: v for k, v in location.items() if k != "federation"}
    prefix_uri = str(entry.get("location", ""))
    if not prefix_uri.startswith(str(base["uri"])):
        raise VerificationError("receipt output location is outside the contract output location", code="bad_location")
    connector = party_connector(dict(base, uri=prefix_uri), access_token=token)
    try:
        manifest = dedf.parse_manifest(
            loads_object(connector.read_bytes(dedf.MANIFEST_NAME, dedf.MAX_MANIFEST_BYTES)), output=True
        )
    except StorageError as exc:
        if exc.code == "not_found":
            return 0
        raise
    count = int(connector.delete(dedf.MANIFEST_NAME))
    for f in manifest.files:
        count += int(connector.delete(dedf.object_key(f.path)))
    count += int(connector.delete(dedf.MANIFEST_JWS_NAME))
    return count


SETUP_HINT = "run Setup Training Collaborators"


def check_scenario(scenario: Path, env: DepaEnv, platform: str) -> dict[str, Any]:
    """Read-only preflight for ``scenario deploy``: storage for every mapped provider, outputs for the
    TDC, compute and a CCR release for the platform. Raises ``StageError`` (exit 2) naming what is missing."""
    if platform not in PLATFORM_IDS:
        raise ConfigError(f"unknown platform {platform}")
    native = scenario_parties(scenario, {**os.environ, "DEPA_KMS_URL": str(env.service("kms")["url"])})
    native_tdc = next(p for p, roles in native.items() if "tdc" in roles)
    party_map = compute_party_map([p for p, r in native.items() if "tdp" in r], native_tdc, env)
    storage, outputs = env.raw.get("storage") or {}, env.raw.get("outputs") or {}
    for native_id, roles in sorted(native.items()):
        party = party_map[native_id]
        if "tdp" in roles and party not in storage:
            raise StageError(f"no storage for {party}: {SETUP_HINT}")
    tdc = party_map[native_tdc]
    if tdc not in outputs:
        raise StageError(f"no outputs location for {tdc}: {SETUP_HINT}")
    platform_id = PLATFORM_IDS[platform]
    if platform in ("aks", "gcp"):
        if not (env.raw.get("compute") or {}).get(platform):
            raise StageError(f"no compute.{platform} in the environment: deploy the {platform} infrastructure")
        family = str(load_recipe(scenario / "recipe.yaml").get("family"))
        if not [m for m in env.releases() if m.platform == platform_id and family in m.families]:
            raise StageError(
                f"no CCR release for {platform_id} with family {family} in releases_dir: run Build CCR releases"
            )
    return {"ok": True, "scenario": scenario.name, "platform": platform_id, "party_map": party_map}
