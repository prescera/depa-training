"""A faithful-enough mock of the DEPA KMS and Contract Service for SDK / CCR tests.

It implements the protocol checks the SDK depends on (signed requests with replay and role
checks, governance, catalog, contracts, run authorization with virtual attestation, RAT,
key release with RSA-OAEP wrapping and release receipts, OIDC tokens, events and execution
receipts), so the client side can be exercised end to end without CCF. It is NOT the real
service; the real KMS sandbox is exercised by tests/e2e when available.

Use ``MockDC.transport("kms"|"cs")`` for in-process httpx clients, or ``MockDC.serve()`` for
a real HTTPS server (``https://127.0.0.1:<port>/kms`` and ``/cs``) with a self-signed cert.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import ssl
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding
from cryptography.x509.oid import NameOID

from depa.core.encoding import b64_encode, b64url_encode, canonicalize, digest, loads_strict
from depa.core.jws import jwks_lookup, sign_json, verify_compact
from depa.core.keys import EcSigner, HpkeKeyPair
from depa.core.signed_request import verify_signed_request
from depa.core.timeutil import parse_rfc3339
from depa.errors import DepaError

KMS, CS = "depa-kms", "depa-contract-service"


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.status, self.code = status, code


def _req(cond: object, status: int, code: str) -> None:
    if not cond:
        raise ApiError(status, code)


class Service:
    def __init__(self, name: str, dc: MockDC) -> None:
        self.name, self.dc = name, dc
        self.key = EcSigner.generate(f"{name}-k1")
        self.parties: dict[str, dict[str, Any]] = {}
        self.members: list[str] = []
        self.proposals: dict[str, dict[str, Any]] = {}
        self.settings: dict[str, Any] = {"allow_virtual_attestation": False}
        self.peers: dict[str, dict[str, Any]] = {}
        self.releases: dict[str, dict[str, Any]] = {}
        self.replay: set[str] = set()
        self.bootstrapped = False
        self.base_url = ""

    # ---- helpers ------------------------------------------------------------------------------
    def info(self) -> dict[str, Any]:
        return {
            "service": self.name,
            "version": "2.0.0-mock",
            "bundle_digest": "sha256:" + "0" * 64,
            "bundle_digest_approved": True,
            "bootstrapped": self.bootstrapped,
            "jwks": [self.key.public_jwk()],
        }

    def signed(self, body: Any, op: str, roles: tuple[str, ...]) -> tuple[str, dict[str, Any]]:
        _req(isinstance(body, dict), 400, "bad_request")

        def lookup(ref: str) -> dict[str, Any] | None:
            party_id, _, kid = ref.partition("#")
            party = self.parties.get(party_id)
            if not party or party["status"] != "active":
                return None
            return next((k for k in party["signing_keys"] if k["kid"] == kid), None)

        try:
            req = verify_signed_request(body, lookup, aud=self.name, op=op)
        except DepaError as exc:
            raise ApiError(401, exc.code) from exc
        _req(req.jti not in self.replay, 401, "replay")
        self.replay.add(req.jti)
        party_id = req.kid.split("#")[0]
        _req(any(r in self.parties[party_id]["roles"] for r in roles), 403, "missing_role")
        return party_id, req.body

    def apply(self, action: dict[str, Any]) -> None:
        name, args = action["name"], action.get("args", {})
        if name in ("set_party", "set_member"):
            self.parties[args["party_id"]] = args
            if name == "set_member":
                self.members.append(args["party_id"])
        elif name == "set_settings":
            self.settings.update(args)
        elif name == "set_peer_service":
            self.peers[args["name"]] = args
        elif name == "approve_ccr_release":
            _req(self.releases.get(args["release_id"], {}).get("state") != "revoked", 409, "release_revoked")
            self.releases[args["release_id"]] = {"release": args, "state": "approved"}
        elif name == "revoke_ccr_release":
            self.releases[args["release_id"]]["state"] = "revoked"
        elif name == "set_oidc_settings":
            self.settings["oidc"] = args
        elif name == "revoke_contract":
            self.dc.contracts[args["contract_id"]]["state"] = "revoked"
        else:
            raise ApiError(400, "unknown_action")

    def attest(self, binding: dict[str, Any], attestation: dict[str, Any]) -> tuple[str, str]:
        bh = hashlib.sha256(canonicalize(binding)).hexdigest()
        expected = self.settings.get("trust_bundle_digest")
        _req(expected is None or binding.get("trust_bundle_digest") == expected, 403, "trust_bundle_mismatch")
        _req(attestation.get("platform") == "virtual", 403, "attestation_failed")
        _req(self.settings.get("allow_virtual_attestation") is True, 403, "attestation_failed")
        _req(attestation.get("binding_hash") == bh, 403, "attestation_failed")
        return bh, str(attestation["virtual_id"])

    def release_for(self, measurement: str, names: list[str], tier: int) -> dict[str, Any] | None:
        for rid in names:
            e = self.releases.get(rid)
            if (
                e
                and e["state"] == "approved"
                and measurement in e["release"]["measurements"].get("virtual_ids", [])
                and tier in e["release"]["tiers"]
            ):
                return e["release"]
        return None

    # ---- common routes ---------------------------------------------------------------------------
    def common(self, method: str, parts: list[str], body: Any, query: dict[str, str]) -> Any:
        if parts == ["info"]:
            return self.info()
        if parts == ["gov", "bootstrap"] and method == "POST":
            _req(not self.bootstrapped, 403, "already_bootstrapped")
            for m in body["members"]:
                self.parties[m["party_id"]] = m
                self.members.append(m["party_id"])
            self.settings.update(body.get("settings") or {})
            self.bootstrapped = True
            return {"service": self.name, "jwks": [self.key.public_jwk()], "members": self.members}
        if parts == ["gov", "proposals"] and method == "POST":
            proposer, b = self.signed(body, "gov.propose", ("dc-member",))
            pid = digest(json.loads(json.dumps(b)) | {"proposer": proposer, "n": len(self.proposals)})
            now = int(time.time())
            self.proposals[pid] = {
                "proposal_id": pid,
                "proposer": proposer,
                "actions": b["actions"],
                "created_at": len(self.proposals),
                "expires_at": now + int(b.get("expires_in_s", 604800)),
                "state": "open",
                "ballots": {},
                "proposal_jws": body["jws"],
                "ballot_jws": {},
            }
            return {"proposal_id": pid, "state": "open"}
        if parts == ["gov", "proposals"]:
            return {"proposals": list(self.proposals.values())}
        if len(parts) == 3 and parts[:2] == ["gov", "proposals"] and method == "GET":
            _req(parts[2] in self.proposals, 404, "not_found")
            return self.proposals[parts[2]]
        if parts == ["gov", "constitution"]:
            unanimous = ("set_member", "remove_member", "set_constitution", "set_settings")
            names = [
                "set_member",
                "remove_member",
                "set_party",
                "remove_party",
                "set_constitution",
                "set_settings",
                "set_peer_service",
                "set_jwt_validation_policy",
                "approve_app_digest",
                "approve_ccr_release",
                "revoke_ccr_release",
            ]
            return {
                "constitution": {"default": "majority", "thresholds": {}},
                "effective_thresholds": {n: "unanimous" if n in unanimous else "majority" for n in names},
                "actions": names,
            }
        if len(parts) == 4 and parts[:2] == ["gov", "proposals"] and parts[3] == "ballots":
            voter, b = self.signed(body, "gov.vote", ("dc-member",))
            _req(b.get("proposal_id") == parts[2], 400, "invalid_input")
            p = self.proposals[parts[2]]
            _req(p["state"] == "open", 409, "proposal_closed")
            p["ballots"][voter] = b["vote"]
            if sum(v == "approve" for v in p["ballots"].values()) * 2 > len(self.members):
                for a in p["actions"]:
                    self.apply(a)
                p["state"] = "accepted"
            return p
        if parts == ["gov", "state"]:
            return {"bootstrapped": self.bootstrapped, "settings": self.settings, "peer_services": self.peers}
        if parts == ["gov", "members"]:
            return {"members": self.members}
        if parts and parts[0] == "parties":
            if len(parts) == 1:
                return {"parties": list(self.parties.values())}
            _req(parts[1] in self.parties, 404, "not_found")
            return self.parties[parts[1]]
        if parts and parts[0] == "releases":
            if len(parts) == 1:
                return {"releases": list(self.releases.values())}
            _req(parts[1] in self.releases, 404, "not_found")
            return self.releases[parts[1]]
        return None


class Kms(Service):
    def __init__(self, dc: MockDC) -> None:
        super().__init__(KMS, dc)
        self.datasets: dict[str, dict[str, Any]] = {}
        self.private: dict[str, bytes] = {}
        self.output_keys: dict[str, HpkeKeyPair] = {}
        self.runs_per_contract: dict[str, set[str]] = {}

    def view(self, d: dict[str, Any]) -> dict[str, Any]:
        hidden = ("suspended", "history")
        return {k: v for k, v in d.items() if k not in hidden} | {"state": "suspended" if d["suspended"] else "active"}

    def route(self, method: str, parts: list[str], body: Any, query: dict[str, str], headers: dict[str, str]) -> Any:
        if parts == ["datasets"] and method == "POST":
            owner, b = self.signed(body, "dataset.create", ("tdp", "tdc"))
            ds = b["dataset_id"]
            _req(ds.split("/")[0] == owner, 403, "not_owner")
            _req(ds not in self.datasets, 409, "exists")
            pair = HpkeKeyPair.generate(f"{ds}#1")
            self.private[pair.kid] = pair.private_key
            self.datasets[ds] = {
                "dataset_id": ds,
                "owner": owner,
                "keys": [
                    {"kid": pair.kid, "x25519": b64url_encode(pair.public_key), "created_at": "x", "state": "current"}
                ],
                "policy": None,
                "policy_version": 0,
                "history": [],
                "suspended": False,
            }
            return self.view(self.datasets[ds])
        if len(parts) >= 3 and parts[0] == "datasets":
            ds = f"{parts[1]}/{parts[2]}"
            _req(ds in self.datasets, 404, "not_found")
            d = self.datasets[ds]
            if len(parts) == 3:
                return self.view(d)
            if parts[3:] == ["policy", "history"]:
                return {"dataset_id": ds, "versions": d["history"]}
            op = {
                "policy": "dataset.set_policy",
                "rotate": "dataset.rotate",
                "suspend": "dataset.suspend",
                "resume": "dataset.resume",
            }[parts[3]]
            owner, b = self.signed(body, op, ("tdp", "tdc"))
            _req(b.get("dataset_id") == ds and owner == d["owner"], 403, "not_owner")
            if parts[3] == "policy":
                d["policy"], d["policy_version"] = b["policy"], d["policy_version"] + 1
                d["history"].append(
                    {
                        "version": d["policy_version"],
                        "policy": b["policy"],
                        "set_at": "2026-11-23T00:00:00Z",
                        "jws": body["jws"],
                    }
                )
            elif parts[3] == "rotate":
                for k in d["keys"]:
                    k["state"] = "previous"
                pair = HpkeKeyPair.generate(f"{ds}#{len(d['keys']) + 1}")
                self.private[pair.kid] = pair.private_key
                d["keys"].append({"kid": pair.kid, "x25519": b64url_encode(pair.public_key), "state": "current"})
            else:
                d["suspended"] = parts[3] == "suspend"
            return self.view(d)
        if parts == ["keys", "release"]:
            access = self.run_access(body)
            rat, ds = access["rat"], body["dataset_id"]
            _req(ds in [x["dataset_id"] for x in rat["datasets"]], 403, "dataset_not_in_rat")
            d = self.datasets.get(ds)
            _req(d and not d["suspended"], 403, "suspended")
            self.check_policy(d["policy"], rat)
            kids = body.get("kids") or [k["kid"] for k in d["keys"]]
            _req(all(k in self.private and k.startswith(ds + "#") for k in kids), 400, "unknown_kid")
            self.runs_per_contract.setdefault(rat["contract_id"], set()).add(rat["run_id"])
            pub = serialization.load_pem_public_key(body["wrappingKey"].encode())
            oaep = padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None)
            claims = {
                "iss": self.base_url,
                "release_id": str(uuid.uuid4()),
                "run_id": rat["run_id"],
                "contract_id": rat["contract_id"],
                "dataset_id": ds,
                "kids": kids,
                "binding_hash": access["bh"],
                "measurement_id": rat["measurement_id"],
                "platform": rat["platform"],
                "iat": int(time.time()),
            }
            self.dc.release_count += 1
            return {
                "keys": [{"kid": k, "wrapped": b64_encode(pub.encrypt(self.private[k], oaep))} for k in kids],  # type: ignore[union-attr]
                "release_receipt": sign_json(self.key, claims, typ="depa-key-release+jwt"),
            }
        if parts == ["oidc", "token"]:
            access = self.run_access(body)
            return {
                "token": sign_json(
                    self.key,
                    {"iss": "mock", "aud": body["audience"], "sub": "x", "exp": int(time.time()) + 300},
                    typ="JWT",
                )
            }
        if len(parts) == 3 and parts[0] == "outputs" and parts[2] == "key":
            access = self.run_access(body)
            _req(access["rat"]["output"]["mode"] == "tee-only", 403, "output_mode")
            pair = self.output_keys.setdefault(parts[1], HpkeKeyPair.generate(f"output:{parts[1]}#1"))
            return {"kid": pair.kid, "x25519": b64url_encode(pair.public_key), "recipient": f"kms:{pair.kid}"}
        return self.common(method, parts, body, query)

    def run_access(self, body: dict[str, Any]) -> dict[str, Any]:
        bh, measurement = self.attest(body["binding"], body["attestation"])
        _req(
            hashlib.sha256(body["wrappingKey"].encode()).hexdigest() == body["binding"]["wrapping_key_sha256"]
            if "wrappingKey" in body
            else True,
            403,
            "wrapping_key",
        )
        peer = self.peers.get(CS)
        _req(peer, 403, "no_peer")
        try:
            rat = verify_compact(body["rat"], jwks_lookup(peer["jwks"]), typ="depa-rat+jwt").claims()
        except DepaError as exc:
            raise ApiError(401, "bad_rat") from exc
        _req(rat["aud"] == KMS and rat["exp"] > time.time() and rat["iss"] == peer["url"], 401, "bad_rat")
        _req(
            rat["binding_hash"] == bh
            and rat["measurement_id"] == measurement
            and rat["run_id"] == body["binding"]["run_id"]
            and rat["platform"] == "virtual",
            403,
            "rat_mismatch",
        )
        _req(self.release_for(measurement, rat["ccr_releases"], rat["tier"]), 403, "measurement_not_approved")
        return {"rat": rat, "bh": bh}

    def check_policy(self, policy: dict[str, Any] | None, rat: dict[str, Any]) -> None:
        _req(policy is not None, 403, "no_policy")
        assert policy is not None
        _req(rat["platform"] in policy.get("allowed_platforms", []), 403, "policy_platform")
        _req(rat["tier"] in policy.get("allowed_tiers", [1]), 403, "policy_tier")
        tdcs = policy.get("allowed_tdcs", [])
        _req("*" in tdcs or rat["tdc"] in tdcs, 403, "policy_tdc")
        _req(rat["purpose"] in policy.get("allowed_purposes", [rat["purpose"]]), 403, "policy_purpose")
        priv = policy.get("privacy") or {}
        if priv.get("require_dp"):
            _req(
                rat["privacy"]["mode"] == "dp"
                and rat["privacy"]["epsilon"] <= priv.get("max_epsilon", 1e9)
                and rat["privacy"]["delta"] <= priv.get("max_delta", 1),
                403,
                "policy_privacy",
            )
        _req(rat["output"]["mode"] in policy.get("allowed_output_modes", ["tee-only"]), 403, "policy_output")


class Cs(Service):
    def __init__(self, dc: MockDC) -> None:
        super().__init__(CS, dc)
        self.catalog: dict[tuple[str, str], dict[str, Any]] = {}

    def route(self, method: str, parts: list[str], body: Any, query: dict[str, str], headers: dict[str, str]) -> Any:
        dc = self.dc
        if parts == ["catalog", "datasets"] and method == "GET":
            entries: dict[str, dict[str, Any]] = {}
            for (ds, version), _ in sorted(self.catalog.items()):
                e = entries.setdefault(ds, {"dataset_id": ds, "owner": ds.split("/")[0], "versions": []})
                e["versions"].append(version)
                e["latest_version"] = version
            return {"datasets": list(entries.values())}
        if parts == ["catalog", "datasets"] and method == "POST":
            owner, d = self.signed(body, "catalog.publish", ("tdp", "tdc"))
            _req(d["dataset_id"].split("/")[0] == owner, 403, "not_owner")
            _req((d["dataset_id"], d["version"]) not in self.catalog, 409, "exists")
            self.catalog[(d["dataset_id"], d["version"])] = d
            return {"ok": True}
        if len(parts) == 6 and parts[:2] == ["catalog", "datasets"] and parts[4] == "versions":
            key = (f"{parts[2]}/{parts[3]}", parts[5])
            _req(key in self.catalog, 404, "not_found")
            d = self.catalog[key]
            return {
                "dataset_id": d["dataset_id"],
                "version": d["version"],
                "descriptor": d,
                "descriptor_digest": digest(d),
                "manifest_digest": d["manifest_digest"],
                "publisher": "x",
            }
        if parts == ["contracts"] and method == "GET":
            party = query.get("party")
            return {
                "contracts": [
                    r
                    for r in dc.contracts.values()
                    if party in {r["contract"]["parties"]["tdc"], *r["contract"]["parties"]["tdps"]}
                ]
            }
        if parts == ["contracts"] and method == "POST":
            tdc, c = self.signed(body, "contract.propose", ("tdc",))
            _req(c["parties"]["tdc"] == tdc, 403, "not_tdc")
            for d in c["datasets"]:
                entry = self.catalog.get((d["dataset_id"], d["version"]))
                _req(entry and entry["manifest_digest"] == d["manifest_digest"], 400, "dataset_mismatch")
            for r in c["computation"]["recipes"]:
                _req(digest(r["document"]) == r["digest"], 400, "recipe_digest")
            dc.contracts[c["contract_id"]] = {
                "contract": c,
                "contract_digest": digest(c),
                "state": "draft",
                "signatures": [],
                "runs_used": 0,
            }
            return {"contract_id": c["contract_id"], "state": "draft"}
        if len(parts) == 3 and parts[0] == "contracts" and parts[2] == "runs" and method == "GET":
            return {"runs": [r for r in dc.runs.values() if r["contract_id"] == parts[1]]}
        if len(parts) >= 2 and parts[0] == "contracts":
            _req(parts[1] in dc.contracts, 404, "not_found")
            rec = dc.contracts[parts[1]]
            c = rec["contract"]
            if len(parts) == 2:
                return rec
            if parts[2] == "signatures":
                party, b = self.signed(body, "contract.sign", ("tdc", "tdp"))
                required = {c["parties"]["tdc"], *(d["dataset_id"].split("/")[0] for d in c["datasets"])}
                _req(party in required and b["contract_digest"] == rec["contract_digest"], 403, "not_required")
                rec["signatures"].append({"party": party, "jws": body["jws"], "at": "now"})
                if {s["party"] for s in rec["signatures"]} >= required:
                    rec["state"] = "active"
                return {"state": rec["state"]}
            if parts[2] == "revoke":
                party, b = self.signed(body, "contract.revoke", ("tdc", "tdp"))
                _req(b.get("contract_id") == parts[1], 400, "invalid_input")
                rec["state"] = "revoked"
                return {"state": "revoked"}
            if parts[2] == "runs" and len(parts) == 3:
                return self.authorize(rec, body)
            if parts[2] == "runs" and len(parts) == 5 and parts[4] == "rat":
                return self.refresh(rec, parts[3], body)
            if parts[2] == "runs" and len(parts) == 5 and parts[4] == "events":
                return self.event(rec, parts[3], body)
        if len(parts) == 2 and parts[0] == "runs":
            _req(parts[1] in dc.runs, 404, "not_found")
            return dc.runs[parts[1]]
        return self.common(method, parts, body, query)

    def authorize(self, rec: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
        c, b = rec["contract"], body["binding"]
        bh, measurement = self.attest(b, body["attestation"])
        comp = c["computation"]
        _req(rec["state"] == "active", 403, "contract_not_active")
        _req(b["contract_digest"] == rec["contract_digest"], 403, "contract_digest")
        _req(rec["runs_used"] < comp["max_runs"], 403, "exhausted")
        _req(b["recipe_digest"] in [r["digest"] for r in comp["recipes"]], 403, "recipe")
        _req(b["platform"] in comp["platforms"], 403, "platform")
        _req(self.release_for(measurement, comp["ccr_releases"], comp["tier"]), 403, "measurement")
        rec["runs_used"] += 1
        now = int(time.time())
        deadline = now + comp["max_duration_minutes"] * 60
        self.dc.runs[b["run_id"]] = {
            "run_id": b["run_id"],
            "contract_id": c["contract_id"],
            "binding_hash": bh,
            "run_key": b["run_key"],
            "measurement_id": measurement,
            "platform": b["platform"],
            "state": "authorized",
            "deadline": deadline,
            "events": [],
            "seq": 0,
        }
        rat = {
            "iss": self.base_url,
            "aud": KMS,
            "sub": b["run_id"],
            "iat": now,
            "exp": now + self.dc.rat_lifetime,
            "contract_id": c["contract_id"],
            "contract_digest": rec["contract_digest"],
            "run_id": b["run_id"],
            "binding_hash": bh,
            "platform": b["platform"],
            "measurement_id": measurement,
            "tier": comp["tier"],
            "custom_measurement": False,
            "ccr_releases": comp["ccr_releases"],
            "recipe_digest": b["recipe_digest"],
            "tdc": c["parties"]["tdc"],
            "purpose": c["purpose"],
            "datasets": c["datasets"],
            "privacy": c["privacy"],
            "output": {"mode": c["output"]["mode"], "recipients": c["output"].get("recipients", [])},
            "deadline": deadline,
        }
        if self.dc.rat_tamper:
            rat.update(self.dc.rat_tamper)
        self.dc.runs[b["run_id"]]["rat"] = rat
        return {"run_id": b["run_id"], "rat": sign_json(self.key, rat, typ="depa-rat+jwt"), "deadline": deadline}

    def refresh(self, rec: dict[str, Any], run_id: str, body: dict[str, Any]) -> dict[str, Any]:
        run = self.dc.runs.get(run_id)
        _req(run is not None, 404, "not_found")
        assert run is not None
        key = dict(run["run_key"])
        try:
            claims = verify_compact(body["jws"], lambda kid: key, typ="depa-run-event+jws").claims()
        except DepaError as exc:
            raise ApiError(401, "bad_signature") from exc
        _req(
            claims.get("op") == "run.refresh_rat"
            and claims.get("run_id") == run_id
            and claims.get("contract_id") == run["contract_id"],
            400,
            "invalid_input",
        )
        _req(abs(time.time() - claims["iat"]) <= 300 and claims["jti"] not in self.replay, 401, "replay")
        self.replay.add(claims["jti"])
        _req(run["state"] not in ("completed", "failed"), 409, "run_terminal")
        _req(time.time() < run["deadline"], 403, "deadline_passed")
        _req(rec["state"] == "active", 403, "contract_not_active")
        now = int(time.time())
        rat = dict(run["rat"], iat=now, exp=min(now + self.dc.rat_lifetime, run["deadline"]))
        self.dc.refresh_count += 1
        return {"run_id": run_id, "rat": sign_json(self.key, rat, typ="depa-rat+jwt"), "deadline": run["deadline"]}

    def event(self, rec: dict[str, Any], run_id: str, body: dict[str, Any]) -> dict[str, Any]:
        run = self.dc.runs[run_id]
        key = dict(run["run_key"])
        key.setdefault("kid", "run")
        try:
            ev = verify_compact(
                body["jws"], lambda kid: key if kid == key["kid"] else None, typ="depa-run-event+jws"
            ).claims()
        except DepaError as exc:
            raise ApiError(401, "bad_event") from exc
        _req(ev["run_id"] == run_id and ev["seq"] > run["seq"], 400, "bad_seq")
        parse_rfc3339(ev["at"])
        run["seq"] = ev["seq"]
        run["events"].append(ev)
        if ev["type"] == "failed":
            run["state"] = "failed"
        if ev["type"] != "completed":
            return {"run_id": run_id, "seq": ev["seq"], "state": run["state"]}
        receipt = ev["data"]
        peer = self.peers[KMS]
        datasets = {d["dataset_id"] for d in rec["contract"]["datasets"]}
        seen = set()
        for item in receipt["inputs"]:
            r = verify_compact(item["release_receipt"], jwks_lookup(peer["jwks"]), typ="depa-key-release+jwt").claims()
            _req(r["run_id"] == run_id and r["binding_hash"] == run["binding_hash"], 403, "receipt_mismatch")
            seen.add(r["dataset_id"])
        _req(seen == datasets, 403, "missing_release_receipt")
        run["state"] = "completed"
        run["receipt"] = receipt
        signed = {
            "iss": self.base_url,
            "iat": int(time.time()),
            "sub": run_id,
            "receipt_digest": digest(receipt),
            "receipt": receipt,
        }
        run["receipt_jws"] = sign_json(self.key, signed, typ="depa-exec-receipt+jwt")
        return {"receipt_jws": run["receipt_jws"], "run_id": run_id, "seq": ev["seq"], "state": "completed"}


class MockDC:
    def __init__(self) -> None:
        self.contracts: dict[str, dict[str, Any]] = {}
        self.runs: dict[str, dict[str, Any]] = {}
        self.release_count = 0
        self.rat_tamper: dict[str, Any] = {}
        self.rat_lifetime = 300
        self.refresh_count = 0
        self.kms = Kms(self)
        self.cs = Cs(self)
        self.kms.base_url = "https://mock-kms.test/app"
        self.cs.base_url = "https://mock-cs.test/app"
        self.server: ThreadingHTTPServer | None = None
        self.cert_pem = ""

    def handle(
        self, service: str, method: str, path: str, query: dict[str, str], raw: bytes, headers: dict[str, str]
    ) -> tuple[int, Any]:
        svc = self.kms if service == "kms" else self.cs
        parts = [unquote(p) for p in path.strip("/").split("/") if p]
        try:
            body = loads_strict(raw) if raw else None
            result = svc.route(method, parts, body, query, headers)
            if result is None:
                raise ApiError(404, "no_route")
            return 200, result
        except ApiError as exc:
            return exc.status, {"error": {"code": exc.code, "message": str(exc)}}
        except (KeyError, TypeError, DepaError) as exc:
            return 400, {"error": {"code": "bad_request", "message": repr(exc)}}

    def transport(self, service: str) -> httpx.MockTransport:
        base = urlsplit(getattr(self, service).base_url).path

        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path[len(base) :]
            query = {k: v[0] for k, v in parse_qs(request.url.query.decode()).items()}
            status, body = self.handle(service, request.method, path, query, request.content, dict(request.headers))
            return httpx.Response(status, json=body, headers={"x-ms-ccf-transaction-id": "2.42"})

        return httpx.MockTransport(handler)

    # ---- real HTTPS server ------------------------------------------------------------------------
    def serve(self, port: int = 0) -> tuple[str, str, Path]:
        """Start HTTPS on 127.0.0.1; returns (kms_url, cs_url, cert_path)."""
        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "mock-dc")])
        now = dt.datetime.now(dt.UTC)
        import ipaddress

        cert = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(minutes=5))
            .not_valid_after(now + dt.timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), True)
            .sign(key, hashes.SHA256())
        )
        tmp = Path(tempfile.mkdtemp(prefix="mockdc-"))
        cert_path, key_path = tmp / "cert.pem", tmp / "key.pem"
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
            )
        )
        dc = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: object) -> None:
                return

            def _do(self) -> None:
                url = urlsplit(self.path)
                service, _, rest = url.path.lstrip("/").partition("/")
                raw = self.rfile.read(int(self.headers.get("content-length") or 0))
                rest = rest[len("app") :] if rest.startswith("app") else rest
                query = {k: v[0] for k, v in parse_qs(url.query).items()}
                status, body = dc.handle(service, self.command, rest, query, raw, dict(self.headers))
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("x-ms-ccf-transaction-id", "2.42")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = do_PUT = _do

        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert_path, key_path)
        server.socket = ctx.wrap_socket(server.socket, server_side=True)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.server = server
        actual = server.server_address[1]
        self.kms.base_url = f"https://127.0.0.1:{actual}/kms/app"
        self.cs.base_url = f"https://127.0.0.1:{actual}/cs/app"
        self.cert_pem = cert_path.read_text()
        return self.kms.base_url, self.cs.base_url, cert_path

    def stop(self) -> None:
        if self.server:
            self.server.shutdown()
