"""Environment-driven operations: ``depa dc setup --env`` and ``depa scenario deploy``.

``scenario deploy`` chains the per-party steps for pilots and demos where one operator holds
several parties' keys. Every step is idempotent and is the same code the individual commands
run (``depa dataset ...``, ``depa contract ...``, ``depa job submit``, ``depa model ...``), so in
production each party runs only its own steps. Steps for parties whose keys are not held (or
not selected with ``--parties``) are skipped and reported.
"""

from __future__ import annotations

import os
import tempfile
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from depa.clients.base import BaseClient
from depa.clients.cs import CsClient
from depa.clients.http import ServiceEndpoint
from depa.clients.kms import KmsClient
from depa.contract.authoring import DatasetTemplate, build_contract, load_dataset_template
from depa.contract.recipe import load_recipe
from depa.contract.verify import verify_contract
from depa.contract.yamlio import load_template
from depa.core.encoding import canonicalize, digest, dumps_pretty
from depa.core.ids import dataset_owner
from depa.core.timeutil import parse_rfc3339
from depa.deploy import Runner, aks, cancellable_runner, gcp, subprocess_runner
from depa.deploy.local import run_local
from depa.deploy.release import ReleaseManifest
from depa.deploy.spec import JobSpec
from depa.dev.stack import VIRTUAL_RELEASE_ID, govern, virtual_release
from depa.env import DepaEnv, load_env
from depa.errors import ConfigError, DeployCancelled, NotFoundError, ServiceError, ValidationError, VerificationError
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

PLATFORM_IDS = {"aks": "aci-snp", "gcp": "gcp-cs-tdx", "local": "virtual"}
Log = Callable[[str], object]


def _held(profile: Profile, party_id: str) -> bool:
    return (profile.keys_dir(party_id) / "signing.jwk").exists()


def _public_record(profile: Profile, party_id: str) -> dict[str, Any] | None:
    path = profile.keys_dir(party_id) / "party.json"
    return profile.party_record(party_id) if path.exists() else None


# ---------------------------------------------------------------------------------------------
# depa dc setup


def dc_setup(
    env: DepaEnv,
    profile: Profile,
    *,
    operator: tuple[str, str] | None = None,
    operator_token: Callable[[str], str | None] | None = None,
    allow_virtual: bool = False,
    log: Log = print,
) -> dict[str, Any]:
    """``operator`` (client cert/key) or ``operator_token(service_url)`` (ACL Administrator bearer)
    authenticate ``/gov/bootstrap``; they are only used when a service is not yet bootstrapped."""
    kms, cs = profile.kms_client(), profile.cs_client()
    members = [p.party_id for p in env.parties_with("dc-member")]
    held_members = [m for m in members if _held(profile, m)]
    if not held_members:
        raise ConfigError("dc setup needs the keys of at least one dc-member party")
    summary: dict[str, Any] = {"bootstrapped": [], "proposals": {"kms": [], "cs": []}, "skipped": []}
    for client, which in ((kms, "kms"), (cs, "cs")):
        if client.info().bootstrapped:
            continue
        records = [r for m in members if (r := _public_record(profile, m)) is not None]
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

    infos = {"kms": kms.info(), "cs": cs.info()}
    for client, which, peer_url, peer in ((kms, "kms", cs.base_url, "cs"), (cs, "cs", kms.base_url, "kms")):
        actions: list[dict[str, Any]] = []
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
        peer_info = infos[peer]
        if pinned_peer_jwks(client, peer_info.service) != list(peer_info.jwks):
            actions.append(
                {
                    "name": "set_peer_service",
                    "args": {"name": peer_info.service, "url": peer_url, "jwks": list(peer_info.jwks)},
                }
            )
        for manifest in env.releases():
            try:
                entry = client.get_release(manifest.release_id)
                if entry.approved and digest(entry.release) == digest(manifest.release):
                    continue
            except NotFoundError:
                pass
            actions.append({"name": "approve_ccr_release", "args": manifest.release})
        if which == "kms":
            issuer = env.service("kms").get("oidc_issuer")
            if issuer:
                audiences = sorted(
                    {
                        a
                        for loc in (env.raw.get("outputs") or {}).values()
                        if loc.get("federation") and (a := federation_audience(loc["connector"], loc["federation"]))
                    }
                )
                oidc = {
                    "issuer_url": str(issuer).rstrip("/"),
                    "token_ttl_s": 900,
                    "allowed_audiences_global": audiences,
                }
                if latest_accepted_args(client, "set_oidc_settings") != oidc:
                    actions.append({"name": "set_oidc_settings", "args": oidc})
            else:
                summary["skipped"].append("kms: collaborative.kms.oidc_issuer not set; OIDC settings unchanged")
        act(client, which, actions)
    for client, which in ((kms, "kms"), (cs, "cs")):  # after peer pins: the bundle includes both JWKS
        act(client, which, trust_bundle_settings_actions(profile, client, kms, cs))
    return summary


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


class _Deployer:
    def __init__(self, scenario: Path, env: DepaEnv, profile: Profile, opts: DeployOptions, log: Log) -> None:
        self.scenario, self.env, self.profile, self.opts, self.log = scenario, env, profile, opts, log
        self.kms: KmsClient = profile.kms_client()
        self.cs: CsClient = profile.cs_client()
        self.platform = PLATFORM_IDS[opts.platform]
        self.tdc = _env_tdc(env)
        self.template_env = {**os.environ, "DEPA_TDC": self.tdc, "DEPA_KMS_URL": self.kms.base_url}
        self.work = opts.work_dir or Path(tempfile.mkdtemp(prefix=f"depa-{scenario.name}-"))
        self.summary: dict[str, Any] = {
            "scenario": scenario.name,
            "platform": self.platform,
            "steps": [],
            "skipped": [],
        }

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
        return locate_dataset_template(
            self.env,
            load_dataset_template(path, self.template_env),
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
        owners = sorted({dataset_owner(t.dataset_id) for t in templates if self.opts.prepare == "always" or missing(t)})
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

    def publish_datasets(self, templates: Sequence[DatasetTemplate]) -> None:
        pending = []
        for t in templates:
            owner = dataset_owner(t.dataset_id)
            if not self.acts_for(owner):
                self.summary["skipped"].append(f"{t.dataset_id}: steps for {owner} (keys not held/selected)")
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
            try:
                self.cs.get_dataset_version(t.dataset_id, t.version)
            except NotFoundError:
                pending.append(t)
        if pending:
            self.prepare_data(pending)
        for t in pending:
            signer = self.profile.signer(dataset_owner(t.dataset_id))
            out = self.work / "encrypted" / t.dataset_id / t.version
            manifest = encrypt_dataset(t, kms=self.kms, signer=signer, out_dir=out)
            self.step("dataset.encrypt", dataset_id=t.dataset_id, manifest_digest=manifest.digest)
            upload_directory(
                party_connector(t.descriptor["location"], access_token=self.opts.storage_token), out, manifest
            )
            self.step("dataset.upload", dataset_id=t.dataset_id, location=t.descriptor["location"]["uri"])
            publish_dataset(self.cs, signer, t, out)
            self.step("dataset.publish", dataset_id=t.dataset_id, version=t.version)

    # ---- TDC: contract --------------------------------------------------------------------

    def releases(self, families: set[str]) -> list[ReleaseManifest]:
        found = [m for m in self.env.releases() if m.platform == self.platform and families <= set(m.families)]
        return found

    def contract(self) -> dict[str, Any]:
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

        candidate = build_contract(
            self.scenario / "contract.yaml", env=env, resolve_manifest=catalog_resolver(self.cs), customize=customize
        )
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
            failure = next((e.get("data") for e in run.get("events", []) if e.get("type") == "failed"), None)
            raise ServiceError(f"run {run['run_id']} failed: {failure}", service="ccr", code="run_failed")
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
    if opts.platform not in PLATFORM_IDS:
        raise ValidationError(f"unknown platform {opts.platform}", code="bad_platform")
    if opts.cancel is not None and opts.cancel.is_set():
        raise DeployCancelled("deployment cancelled")
    d = _Deployer(scenario, env, profile, opts, log)
    templates = [d.dataset_template(p) for p in sorted((scenario / "datasets").glob("*.yaml"))]
    if not templates:
        raise ConfigError(f"{scenario}/datasets has no dataset templates")
    d.check_cancel()
    d.publish_datasets(templates)
    if not d.acts_for(d.tdc):
        d.summary["skipped"].append(f"contract, job and outputs (TDC {d.tdc} keys not held/selected)")
        d.summary["status"] = "datasets-published"
        return d.summary
    contract = d.contract()
    record = d.sign(contract)
    d.summary["contract_id"] = contract["contract_id"]
    d.summary["contract_state"] = record.get("state")
    if record.get("state") != "active":
        d.summary["status"] = "awaiting-signatures"
        return d.summary
    if not opts.run:
        d.summary["status"] = "contract-active"
        return d.summary
    d.check_cancel()
    run = d.run(contract)
    d.summary["result"] = d.outputs(run, contract)
    d.summary["status"] = "succeeded"
    return d.summary
