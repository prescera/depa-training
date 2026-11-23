"""The clean-room agent: spec §12 end to end.

1. fetch the contract; require ``active``, digest match and full validity; select and validate
   the recipe (fail closed);
2. generate run material, bind, attest, ``POST /contracts/{id}/runs``; verify the RAT;
3. per dataset: catalog descriptor, federated storage access (KMS OIDC token -> cloud STS),
   manifest + owner JWS + digest, ``POST /keys/release`` with a verified release receipt;
   prepare the output recipients and output storage while the RAT is valid; then
   stream-decrypt the recipe inputs into the work directory;
4. run the trainer in its sandbox with a deadline watchdog;
5. output gate; ``dedf/1`` encryption to the recipients (or the KMS output key); upload;
   signed ``completed`` event carrying the Execution Receipt; verify the CS-signed receipt;
6. wipe the work directory and exit. Only allowlisted status lines are written.
"""

from __future__ import annotations

import contextlib
import io
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from depa.attestation import AttestationProvider, Evidence, get_provider
from depa.ccr.config import CcrConfig
from depa.ccr.gate import GateResult, apply_gate, read_result
from depa.ccr.log import StatusLog
from depa.ccr.material import RunMaterial, binding_hash
from depa.ccr.trainer import (
    SandboxLayout,
    TrainerIdentity,
    make_read_only,
    network_isolation_available,
    prepare_layout,
    run_trainer,
    trainer_identity,
    wipe,
)
from depa.clients.base import Party
from depa.clients.cs import CsClient
from depa.clients.kms import KmsClient
from depa.contract.recipe import against_contract_errors, select_recipe, structural_errors, trainer_errors
from depa.contract.schema import schema_errors
from depa.contract.validate import contract_errors
from depa.core import dedf
from depa.core.encoding import canonicalize, digest
from depa.core.ids import KMS_SERVICE, dataset_owner, parse_recipient
from depa.core.jws import KeyLookup, check_jwt_times, jwks_lookup, sign_json, verify_compact
from depa.core.timeutil import format_rfc3339, now_rfc3339, parse_rfc3339
from depa.errors import DepaError, VerificationError
from depa.storage import Connector, TokenSource
from depa.storage.factory import ccr_connector

RAT_TYP = "depa-rat+jwt"
RELEASE_RECEIPT_TYP = "depa-key-release+jwt"
EVENT_TYP = "depa-run-event+jws"
EXEC_RECEIPT_TYP = "depa-exec-receipt+jwt"
MAX_MANIFEST_JWS = 64 * 1024
MIN_UPLOAD_RESERVE_S = 120
RAT_REFRESH_MARGIN_S = 60
MAX_UPLOAD_RESERVE_S = 900

ConnectorFactory = Callable[..., Connector]


class AgentFailure(DepaError):
    code = "agent_failed"


def _fail(code: str, message: str) -> AgentFailure:
    return AgentFailure(message, code=code)


@dataclass
class _Input:
    spec: dict[str, Any]
    manifest: dedf.Manifest
    dek: bytes
    connector: Connector
    release_receipt: str


class Agent:
    def __init__(
        self,
        config: CcrConfig,
        *,
        log: StatusLog,
        provider: AttestationProvider | None = None,
        cs: CsClient | None = None,
        kms: KmsClient | None = None,
        connector_factory: ConnectorFactory = ccr_connector,
        trainer_command: list[str],
        require_root: bool | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.cfg = config
        self.log = log
        self.provider = provider or get_provider(config.platform)
        self.cs = cs or CsClient(config.cs_endpoint())
        self.kms = kms or KmsClient(config.kms_endpoint())
        self.connector_factory = connector_factory
        self.trainer_command = trainer_command
        self.require_root = (not config.dev) if require_root is None else require_root
        self.clock = clock
        self.layout = SandboxLayout(config.work_dir)
        self.stage = "init"
        self._material: RunMaterial | None = None
        self._seq = 0
        self._cs_keys: KeyLookup | None = None
        self._kms_keys: KeyLookup | None = None
        self._rat_state: tuple[str, dict[str, Any]] = ("", {})
        self._rat_ctx: tuple[Mapping[str, Any], str, str] = ({}, "", "")

    # -----------------------------------------------------------------------------------------

    def run(self) -> int:
        bundle = self.cfg.trust_bundle.digest if self.cfg.trust_bundle else "none"
        self.log.emit("agent.start", contract_id=self.cfg.contract_id, platform=self.cfg.platform, trust_bundle=bundle)
        try:
            self._run()
        except Exception as exc:  # noqa: BLE001 - every failure is reported by code, then exit 1
            code = exc.code if isinstance(exc, DepaError) else "internal_error"
            self.log.emit("agent.failed", "error", stage=self.stage, code=code)
            self.log.debug_detail(self.stage, f"{type(exc).__name__}: {exc}")
            self._report_failure(code)
            return 1
        finally:
            wipe(self.layout.root)
            self.cs.close()
            self.kms.close()
        self.log.emit("agent.done", contract_id=self.cfg.contract_id, run_id=self._run_id())
        return 0

    def _run_id(self) -> str | None:
        return self._material.run_id if self._material else None

    def _report_failure(self, code: str) -> None:
        if self._material is None:
            return
        try:
            self._post_event("failed", {"code": code, "stage": self.stage})
        except Exception as exc:  # noqa: BLE001 - best effort; the CS deadline expires the run anyway
            self.log.emit("event.posted", "error", type="failed", code="post_failed")
            self.log.debug_detail("report_failure", repr(exc))

    # -----------------------------------------------------------------------------------------

    def _run(self) -> None:
        cfg = self.cfg
        self.stage = "contract"
        contract, contract_digest = self._fetch_contract()
        self.stage = "recipe"
        recipe = self._select_recipe(contract)
        self.stage = "sandbox"
        ident = trainer_identity(require_drop=self.require_root)
        prepare_layout(self.layout, ident)
        self.log.emit("workdir.info", fstype=_fstype(self.layout.root))

        self.stage = "authorize"
        material = RunMaterial.generate()
        binding = material.binding(
            contract_id=cfg.contract_id,
            contract_digest=contract_digest,
            recipe_digest=cfg.recipe_digest,
            platform=cfg.platform,
            trust_bundle_digest=cfg.trust_bundle.digest if cfg.trust_bundle else None,
        )
        bhash = binding_hash(binding)
        ev_cs = self.provider.evidence(bhash, self.cs.base_url)
        auth = self.cs.authorize_run(
            cfg.contract_id, binding=binding, attestation=ev_cs.envelope, bearer=ev_cs.bearer_token
        )
        if auth["run_id"] != material.run_id:
            raise _fail("run_id_mismatch", "the CS authorized a different run_id")
        self._material = material
        started_at = now_rfc3339()
        self.log.emit("run.authorized", run_id=material.run_id, txid=str(auth.get("txid", "")))
        self._rat_ctx = (contract, contract_digest, bhash.hex())
        rat = self._verify_rat(auth["rat"], contract, contract_digest, bhash.hex())
        self._rat_state = (str(auth["rat"]), rat)
        max_s = contract["computation"]["max_duration_minutes"] * 60
        deadline = min(float(rat["deadline"]), self.clock() + max_s)
        self._post_event("started", {})

        self.stage = "keys"
        ev_kms = self.provider.evidence(bhash, self.kms.base_url)
        inputs = self._release_inputs(contract, binding, ev_kms)

        self.stage = "decrypt"
        self._decrypt_inputs(recipe, inputs)
        self._post_event("progress", {"stage": "inputs_ready"})

        self.stage = "train"
        self.layout.recipe.write_bytes(canonicalize(recipe))
        self.layout.recipe.chmod(0o444)
        self._train(ident, deadline, max_s, tier=int(contract["computation"]["tier"]))

        self.stage = "gate"
        gate = apply_gate(
            read_result(self.layout.result), outputs_dir=self.layout.outputs, recipe=recipe, contract=contract
        )
        self.log.emit("gate.passed", count=len(gate.outputs), bytes=gate.total_bytes)

        # Output keys and credentials are obtained only now, with a refreshed RAT (spec §9.3a).
        self.stage = "output_prepare"
        recipients = self._output_recipients(contract, binding, ev_kms)
        out_location = {k: v for k, v in contract["output"]["location"].items() if k != "federation"}
        out_connector = self.connector_factory(
            out_location,
            contract["output"]["location"].get("federation"),
            self._token_source(binding, ev_kms, {"output": True}),
            allow_file=cfg.dev,
            session_name=f"depa-{material.run_id}",
            write=True,
        )
        self.log.emit("output.prepared", count=len(recipients))

        self.stage = "upload"
        outputs = self._seal_and_upload(contract, gate, recipients, out_connector)

        self.stage = "receipt"
        receipt = {
            "run_id": material.run_id,
            "contract_id": cfg.contract_id,
            "contract_digest": contract_digest,
            "recipe_digest": cfg.recipe_digest,
            "platform": cfg.platform,
            "measurement_id": self._rat_state[1]["measurement_id"],
            "started_at": started_at,
            "finished_at": now_rfc3339(),
            "inputs": [
                {
                    "dataset_id": i.spec["dataset_id"],
                    "version": i.spec["version"],
                    "manifest_digest": i.spec["manifest_digest"],
                    "release_receipt": i.release_receipt,
                }
                for i in inputs
            ],
            "privacy_spent": gate.privacy_spent,
            "outputs": outputs,
            "metrics": gate.metrics,
            "status": "succeeded",
        }
        response = self._post_event("completed", receipt)
        self._verify_exec_receipt(response, receipt)

    # ---- step 1 ------------------------------------------------------------------------------

    def _fetch_contract(self) -> tuple[dict[str, Any], str]:
        record = self.cs.get_contract(self.cfg.contract_id)
        contract = record["contract"]
        contract_digest = digest(contract)
        if record.get("contract_digest") != contract_digest:
            raise _fail("contract_digest_mismatch", "contract digest does not match the CS record")
        if contract.get("contract_id") != self.cfg.contract_id:
            raise _fail("contract_id_mismatch", "CS returned a different contract")
        if record.get("state") != "active":
            raise _fail("contract_not_active", "contract is not active")
        if contract_errors(contract):
            raise _fail("invalid_contract", "contract fails validation")
        now = self.clock()
        validity = contract["validity"]
        if (
            not parse_rfc3339(validity["not_before"]).timestamp()
            <= now
            < parse_rfc3339(validity["not_after"]).timestamp()
        ):
            raise _fail("contract_not_valid_now", "contract is outside its validity window")
        computation = contract["computation"]
        if self.cfg.platform not in computation["platforms"]:
            raise _fail("platform_not_allowed", "platform is not allowed by the contract")
        custom = computation.get("custom_ccr")
        if computation["tier"] == 3 and custom and custom["platform"] != self.cfg.platform:
            raise _fail("platform_not_allowed", "custom CCR platform differs")
        self.log.emit("contract.verified", contract_id=self.cfg.contract_id)
        return contract, contract_digest

    def _select_recipe(self, contract: Mapping[str, Any]) -> dict[str, Any]:
        recipe = select_recipe(contract, self.cfg.recipe_digest)
        errs = structural_errors(recipe) + against_contract_errors(recipe, contract) + trainer_errors(recipe)
        if errs:
            self.log.debug_detail("recipe", "; ".join(errs))
            raise _fail("invalid_recipe", "recipe fails validation against the contract")
        self.log.emit("recipe.selected", recipe_digest=self.cfg.recipe_digest)
        return recipe

    # ---- step 2 ------------------------------------------------------------------------------

    def _service_keys(self, service: str) -> KeyLookup:
        bundle = self.cfg.trust_bundle
        if service == KMS_SERVICE:
            if self._kms_keys is None:
                pinned = bundle.kms.jwks if bundle else None
                self._kms_keys = jwks_lookup(pinned) if pinned else self.kms.info().key_lookup()
            return self._kms_keys
        if self._cs_keys is None:
            pinned = bundle.cs.jwks if bundle else None
            self._cs_keys = jwks_lookup(pinned) if pinned else self.cs.info().key_lookup()
        return self._cs_keys

    def _verify_rat(
        self, token: str, contract: Mapping[str, Any], contract_digest: str, bhash_hex: str
    ) -> dict[str, Any]:
        claims = verify_compact(token, self._service_keys("cs"), typ=RAT_TYP).claims()
        check_jwt_times(claims, now=int(self.clock()))
        material = self._material
        assert material is not None  # noqa: S101 - set before the RAT is requested
        computation = contract["computation"]
        expected = {
            "aud": "depa-kms",
            "sub": material.run_id,
            "run_id": material.run_id,
            "contract_id": self.cfg.contract_id,
            "contract_digest": contract_digest,
            "binding_hash": bhash_hex,
            "recipe_digest": self.cfg.recipe_digest,
            "platform": self.cfg.platform,
            "tier": computation["tier"],
            "custom_measurement": computation["tier"] == 3,
            "tdc": contract["parties"]["tdc"],
            "purpose": contract["purpose"],
            "datasets": contract["datasets"],
            "privacy": contract["privacy"],
            "output": {
                "mode": contract["output"]["mode"],
                "recipients": contract["output"].get("recipients", []),
            },
        }
        for key, value in expected.items():
            if canonicalize(claims.get(key)) != canonicalize(value):
                raise VerificationError(f"RAT claim {key} does not match the run", code="rat_mismatch")
        releases = claims.get("ccr_releases")
        if not isinstance(releases, list) or not set(releases) <= set(computation.get("ccr_releases", [])):
            raise VerificationError("RAT ccr_releases are not the contract's", code="rat_mismatch")
        if claims.get("iss") != self.cs.base_url:
            raise VerificationError("RAT issuer is not the pinned CS", code="rat_mismatch")
        deadline = claims.get("deadline")
        if not isinstance(deadline, int) or isinstance(deadline, bool) or deadline <= self.clock():
            raise VerificationError("RAT deadline is missing or past", code="rat_mismatch")
        if not isinstance(claims.get("measurement_id"), str):
            raise VerificationError("RAT has no measurement_id", code="rat_mismatch")
        self.log.emit("rat.verified", run_id=material.run_id)
        return claims

    def _post_event(self, kind: str, data: Mapping[str, Any]) -> dict[str, Any]:
        material = self._material
        assert material is not None  # noqa: S101 - events exist only after authorization
        self._seq += 1
        payload = {
            "run_id": material.run_id,
            "seq": self._seq,
            "type": kind,
            "at": format_rfc3339(datetime.fromtimestamp(self.clock(), UTC)),
            "data": dict(data),
        }
        jws = sign_json(material.run_signer, payload, typ=EVENT_TYP)
        response = self.cs.post_run_event(self.cfg.contract_id, material.run_id, jws)
        self.log.emit("event.posted", type=kind, seq=self._seq, txid=str(response.get("txid") or ""))
        return response

    # ---- step 3 ------------------------------------------------------------------------------

    def _current_rat(self) -> tuple[str, dict[str, Any]]:
        """The RAT, refreshed (spec §9.3a) when within ``RAT_REFRESH_MARGIN_S`` of expiry."""
        token, claims = self._rat_state
        if float(claims["exp"]) - self.clock() >= RAT_REFRESH_MARGIN_S:
            return token, claims
        material = self._material
        assert material is not None  # noqa: S101 - only after authorization
        payload = {
            "op": "run.refresh_rat",
            "contract_id": self.cfg.contract_id,
            "run_id": material.run_id,
            "iat": int(self.clock()),
            "jti": str(uuid.uuid4()),
        }
        response = self.cs.refresh_rat(
            self.cfg.contract_id, material.run_id, sign_json(material.run_signer, payload, typ=EVENT_TYP)
        )
        fresh = self._verify_rat(str(response["rat"]), *self._rat_ctx)
        stable = set(claims) | set(fresh)
        for key in stable - {"iat", "exp"}:
            if canonicalize(claims.get(key)) != canonicalize(fresh.get(key)):
                raise VerificationError(f"refreshed RAT changed claim {key}", code="rat_mismatch")
        self._rat_state = (str(response["rat"]), fresh)
        self.log.emit("rat.refreshed", run_id=material.run_id)
        return self._rat_state

    def _token_source(self, binding: Mapping[str, Any], ev: Evidence, resource: Mapping[str, Any]) -> TokenSource:
        def source(audience: str) -> str:
            return self.kms.oidc_token(
                binding=binding,
                attestation=ev.envelope,
                rat=self._current_rat()[0],
                resource=resource,
                audience=audience,
                bearer=ev.bearer_token,
            )

        return source

    def _release_inputs(
        self,
        contract: Mapping[str, Any],
        binding: Mapping[str, Any],
        ev: Evidence,
    ) -> list[_Input]:
        material = self._material
        assert material is not None  # noqa: S101
        owners: dict[str, Party] = {}
        out: list[_Input] = []
        for spec in contract["datasets"]:
            dataset_id, version = spec["dataset_id"], spec["version"]
            descriptor = self.cs.get_dataset_descriptor(dataset_id, version)
            if (
                schema_errors(descriptor, "dataset-descriptor")
                or descriptor.get("manifest_digest") != spec["manifest_digest"]
            ):
                raise _fail("bad_descriptor", "catalog descriptor is invalid or not bound to the contract")
            owner = dataset_owner(dataset_id)
            if owner not in owners:
                owners[owner] = self.cs.get_party(owner)
            connector = self.connector_factory(
                descriptor["location"],
                descriptor.get("federation"),
                self._token_source(binding, ev, {"dataset_id": dataset_id}),
                allow_file=self.cfg.dev,
                session_name=f"depa-{material.run_id}",
                write=False,
            )
            raw = connector.read_bytes(dedf.MANIFEST_NAME, dedf.MAX_MANIFEST_BYTES)
            jws = connector.read_bytes(dedf.MANIFEST_JWS_NAME, MAX_MANIFEST_JWS).decode("ascii")
            manifest = dedf.verify_manifest(
                raw,
                jws,
                owners[owner].key_lookup(),
                expected_digest=spec["manifest_digest"],
                expected_dataset_id=dataset_id,
                expected_version=version,
            )
            self.log.emit("dataset.manifest_verified", dataset_id=dataset_id, manifest_digest=manifest.digest)
            kid = str(manifest.wrapped_dek()["kid"])
            rat_token, rat = self._current_rat()
            response = self.kms.release_keys(
                binding=binding,
                attestation=ev.envelope,
                rat=rat_token,
                dataset_id=dataset_id,
                wrapping_key_pem=material.wrapping_key_pem,
                kids=[kid],
                bearer=ev.bearer_token,
            )
            receipt = response["release_receipt"]
            self._verify_release_receipt(receipt, rat, dataset_id, kid)
            wrapped = [k for k in response["keys"] if isinstance(k, dict) and k.get("kid") == kid]
            if len(wrapped) != 1:
                raise _fail("key_not_released", "KMS did not release the manifest key")
            private_key = material.unwrap(str(wrapped[0].get("wrapped")))
            dek = dedf.unwrap_dek(manifest.wrapped_dek(), private_key, dataset_id=dataset_id, version=version)
            del private_key
            self.log.emit("dataset.keys_released", dataset_id=dataset_id)
            out.append(_Input(dict(spec), manifest, dek, connector, receipt))
        return out

    def _verify_release_receipt(self, token: str, rat: Mapping[str, Any], dataset_id: str, kid: str) -> None:
        claims = verify_compact(token, self._service_keys(KMS_SERVICE), typ=RELEASE_RECEIPT_TYP).claims()
        kids = claims.get("kids")
        ok = (
            claims.get("run_id") == rat["run_id"]
            and claims.get("contract_id") == rat["contract_id"]
            and claims.get("binding_hash") == rat["binding_hash"]
            and claims.get("dataset_id") == dataset_id
            and claims.get("measurement_id") == rat["measurement_id"]
            and claims.get("platform") == rat["platform"]
            and isinstance(kids, list)
            and kid in kids
        )
        if not ok:
            raise VerificationError("release receipt does not match this run", code="release_receipt_mismatch")

    def _output_recipients(
        self,
        contract: Mapping[str, Any],
        binding: Mapping[str, Any],
        ev: Evidence,
    ) -> list[dedf.Recipient]:
        output = contract["output"]
        rat_token, rat = self._current_rat()
        if output["mode"] == "tee-only":
            kid, public_key = self.kms.output_key(
                self.cfg.contract_id,
                binding=binding,
                attestation=ev.envelope,
                rat=rat_token,
                bearer=ev.bearer_token,
            )
            return [dedf.Recipient(f"kms:{kid}", kid, public_key)]
        recipients = []
        for ref in rat["output"]["recipients"]:
            _, party_id, kid = parse_recipient(ref)
            keys = []
            for client in (self.cs, self.kms):
                party = client.get_party(party_id)
                if not party.active:
                    raise _fail("recipient_inactive", "an output recipient is not active")
                keys.append(party.hpke_key(kid))
            if keys[0] != keys[1]:
                raise _fail("recipient_key_mismatch", "recipient HPKE key differs between the CS and KMS")
            recipients.append(dedf.Recipient(ref, kid, keys[0]))
        if not recipients:
            raise _fail("no_recipients", "no output recipients")
        return recipients

    def _decrypt_inputs(self, recipe: Mapping[str, Any], inputs: list[_Input]) -> None:
        by_id = {i.spec["dataset_id"]: i for i in inputs}
        used = set()
        for item in recipe["inputs"]:
            alias, dataset_id = item["alias"], item["dataset_id"]
            source = by_id[dataset_id]
            used.add(dataset_id)
            target = self.layout.inputs / alias
            target.mkdir(mode=0o755)
            for entry in source.manifest.files:
                with source.connector.open_read(dedf.object_key(entry.path)) as stream:
                    dedf.decrypt_to_path(
                        source.dek,
                        entry,
                        stream,
                        target / entry.path,
                        segment_size=source.manifest.segment_size,
                    )
            self.log.emit(
                "dataset.decrypted",
                dataset_id=dataset_id,
                files=len(source.manifest.files),
                bytes=source.manifest.total_plaintext_size,
            )
        for dataset_id in by_id.keys() - used:
            self.log.emit("dataset.skipped", dataset_id=dataset_id)
        for i in inputs:
            i.dek = b""
        make_read_only(self.layout.inputs)

    # ---- step 4 ------------------------------------------------------------------------------

    def _train(self, ident: TrainerIdentity, deadline: float, max_s: int, *, tier: int) -> None:
        isolate = network_isolation_available(ident)
        self.log.emit("trainer.isolation", network="isolated" if isolate else "unavailable", privdrop=ident.dropped)
        if tier == 3 and not isolate:  # custom code never runs with network access (spec §12)
            raise _fail("network_isolation_unavailable", "tier-3 code requires network isolation")
        reserve = min(MAX_UPLOAD_RESERVE_S, max(MIN_UPLOAD_RESERVE_S, max_s // 10))
        timeout = deadline - self.clock() - reserve
        self.log.emit("trainer.start", seconds=int(max(timeout, 0)))
        outcome = run_trainer(
            self.trainer_command,
            self.layout,
            ident,
            timeout_s=timeout,
            isolate_network=isolate,
            passthrough_output=self.cfg.dev and self.cfg.log_level == "debug",
            clock=self.clock,
        )
        self.log.emit(
            "trainer.exit",
            returncode=outcome.returncode if outcome.returncode is not None else -1,
            seconds=round(outcome.seconds, 1),
        )
        if outcome.timed_out:
            raise _fail("trainer_timeout", "trainer exceeded the run deadline")
        if outcome.returncode != 0:
            with contextlib.suppress(DepaError):  # development detail only (virtual + debug)
                self.log.debug_detail("train", str(read_result(self.layout.result).get("error", ""))[:2000])
            raise _fail("trainer_failed", "trainer exited unsuccessfully")

    # ---- step 5 ------------------------------------------------------------------------------

    def _seal_and_upload(
        self,
        contract: Mapping[str, Any],
        gate: GateResult,
        recipients: list[dedf.Recipient],
        connector: Connector,
    ) -> list[dict[str, Any]]:
        material = self._material
        assert material is not None  # noqa: S101
        tdc = contract["parties"]["tdc"]
        base_uri = str(contract["output"]["location"]["uri"])
        dataset_id = f"{tdc}/outputs/{self.cfg.contract_id}/{material.run_id}"
        results = []
        for out in gate.outputs:
            dek = dedf.new_dek()
            sealed = self.layout.sealed / out.name
            entries = []
            for rel, path in out.files:
                target = sealed / dedf.object_key(rel)
                target.parent.mkdir(parents=True, exist_ok=True)
                with path.open("rb") as src, target.open("xb") as dst:
                    entries.append(dedf.encrypt_stream(dek, rel, src, dst))
            manifest = dedf.build_manifest(
                dataset_id=dataset_id,
                version=out.name,
                owner=tdc,
                segment_size=dedf.DEFAULT_SEGMENT_SIZE,
                files=entries,
                wrapped_deks=dedf.wrap_dek_for_recipients(dek, recipients, dataset_id=dataset_id, version=out.name),
            )
            manifest_raw = dedf.manifest_bytes(manifest)
            manifest_jws = dedf.sign_manifest(manifest, material.run_signer).encode("ascii")
            prefix = f"{self.cfg.contract_id}/{material.run_id}/{out.name}/"
            for entry in entries:
                obj = sealed / dedf.object_key(entry.path)
                with obj.open("rb") as src:
                    connector.upload(prefix + dedf.object_key(entry.path), src, entry.ciphertext_size)
            _upload_bytes(connector, prefix + dedf.MANIFEST_JWS_NAME, manifest_jws)
            _upload_bytes(connector, prefix + dedf.MANIFEST_NAME, manifest_raw)  # last: commit marker
            self.log.emit("output.uploaded", output=out.name, format=out.format, bytes=out.size)
            results.append(
                {
                    "name": out.name,
                    "format": out.format,
                    "manifest_digest": digest(manifest),
                    "location": base_uri + prefix,
                    "recipients": [r.recipient for r in recipients],
                }
            )
        return results

    def _verify_exec_receipt(self, response: Mapping[str, Any], receipt: Mapping[str, Any]) -> None:
        """CS-signed ``{iss, iat, sub: run_id, receipt_digest, receipt}`` must carry exactly our receipt."""
        token = response.get("receipt_jws")
        if not isinstance(token, str):
            raise _fail("no_receipt", "CS returned no execution receipt")
        claims = verify_compact(token, self._service_keys("cs"), typ=EXEC_RECEIPT_TYP).claims()
        run_id = str(receipt["run_id"])
        if (
            claims.get("sub") != run_id
            or claims.get("iss") != self.cs.base_url
            or claims.get("receipt_digest") != digest(dict(receipt))
            or canonicalize(claims.get("receipt")) != canonicalize(dict(receipt))
        ):
            raise VerificationError("execution receipt does not match this run", code="receipt_mismatch")
        self.log.emit("receipt.verified", run_id=run_id, txid=str(response.get("txid", "")))


def _upload_bytes(connector: Connector, key: str, data: bytes) -> None:
    connector.upload(key, io.BytesIO(data), len(data))


def _fstype(path: Path) -> str:
    """Filesystem type backing ``path`` (recorded so operators can audit scratch confidentiality)."""
    try:
        mounts = Path("/proc/mounts").read_text().splitlines()
    except OSError:
        return "unknown"
    best, fstype = "", "unknown"
    target = str(path.resolve())
    for line in mounts:
        parts = line.split()
        covers = len(parts) >= 3 and (target == parts[1] or target.startswith(parts[1].rstrip("/") + "/"))
        if covers and len(parts[1]) > len(best):
            best, fstype = parts[1], parts[2]
    return fstype
