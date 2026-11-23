"""``depa contract verify``: everything a party should check before signing (spec §8.2).

Re-checks, against the live services (over pinned TLS): the contract document itself, the CS
record and digest, every dataset's catalog entry and manifest binding (and, given access, the
manifest signature), the signer's own Release Policies, CCR releases in both registries
(approved, non-revoked, identical, covering platforms / tier / recipe families), tier-3
measurements, output recipients and their registered HPKE keys in both services, privacy and
validity. Errors block ``depa contract sign``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx

from depa.clients.base import Party
from depa.clients.cs import CsClient
from depa.clients.kms import KmsClient
from depa.contract.policy import evaluate_policy
from depa.contract.schema import schema_errors
from depa.contract.validate import contract_errors
from depa.core.dedf import verify_manifest
from depa.core.encoding import digest, sha256_hex
from depa.core.ids import dataset_owner, parse_recipient
from depa.core.timeutil import parse_rfc3339, utcnow
from depa.errors import DepaError, ValidationError

# Loads ``(manifest.json bytes, manifest.jws text)`` for a catalog descriptor.
ManifestLoader = Callable[[Mapping[str, Any]], tuple[bytes, str]]
# Downloads the legal text at ``terms.uri``.
TextLoader = Callable[[str], bytes]
MAX_LEGAL_TEXT = 10 << 20


def fetch_legal_text(uri: str) -> bytes:
    """HTTPS download (publicly trusted TLS, ≤ 3 HTTPS redirects, size-capped)."""
    if not uri.startswith("https://"):
        raise ValidationError("terms.uri must be an https URL", code="bad_terms")
    with httpx.Client(timeout=30, follow_redirects=True, max_redirects=3) as client, client.stream("GET", uri) as r:
        if r.url.scheme != "https" or r.status_code != 200:
            raise ValidationError(f"terms.uri returned HTTP {r.status_code}", code="bad_terms")
        data = bytearray()
        for chunk in r.iter_bytes():
            data += chunk
            if len(data) > MAX_LEGAL_TEXT:
                raise ValidationError("legal text too large", code="bad_terms")
    return bytes(data)


@dataclass
class VerifyReport:
    contract_id: str
    contract_digest: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    info: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_json(self) -> dict[str, Any]:
        return {
            "contract_id": self.contract_id,
            "contract_digest": self.contract_digest,
            "ok": self.ok,
            "errors": self.errors,
            "warnings": self.warnings,
            "info": self.info,
        }


def _party(client: KmsClient | CsClient, party_id: str, report: VerifyReport) -> Party | None:
    try:
        return client.get_party(party_id)
    except DepaError as exc:
        report.errors.append(f"{client.service_name}: party {party_id} cannot be read: {exc}")
        return None


def verify_contract(
    contract: Mapping[str, Any],
    *,
    party_id: str,
    kms: KmsClient,
    cs: CsClient,
    record: Mapping[str, Any] | None = None,
    manifest_loader: ManifestLoader | None = None,
    now: datetime | None = None,
    legal_text_loader: TextLoader | None = fetch_legal_text,
) -> VerifyReport:
    current = now or utcnow()
    report = VerifyReport(str(contract.get("contract_id", "")), digest(dict(contract)))
    errs = contract_errors(contract)
    if errs:
        report.errors.extend(errs)
        return report

    if record is not None:
        if record.get("contract_digest") != report.contract_digest or digest(record.get("contract", {})) != (
            report.contract_digest
        ):
            report.errors.append("the CS record does not hold exactly this contract (digest mismatch)")
        report.info.append(f"CS state: {record.get('state')}, runs used: {record.get('runs_used')}")
        if record.get("state") in ("revoked", "expired", "exhausted"):
            report.errors.append(f"contract is {record.get('state')}")

    parties = contract["parties"]
    own = [d for d in contract["datasets"] if dataset_owner(d["dataset_id"]) == party_id]
    if party_id != parties["tdc"] and not own:
        report.errors.append(f"{party_id} is not a required signer of this contract")

    validity = contract["validity"]
    if parse_rfc3339(validity["not_after"]) <= current:
        report.errors.append("contract validity has already ended")
    if "virtual" in contract["computation"]["platforms"]:
        report.warnings.append("contract allows the development-only 'virtual' platform")

    _check_datasets(contract, party_id, kms, cs, manifest_loader, current, report)
    _check_computation(contract, kms, cs, report)
    _check_output(contract, kms, cs, report)

    _check_terms(contract, legal_text_loader, report)
    privacy = contract["privacy"]
    if privacy["mode"] == "dp":
        report.info.append(f"privacy: DP epsilon={privacy['epsilon']} delta={privacy['delta']}")
    else:
        report.warnings.append("privacy mode is none (no differential privacy guarantee)")
    return report


def _check_datasets(
    contract: Mapping[str, Any],
    party_id: str,
    kms: KmsClient,
    cs: CsClient,
    manifest_loader: ManifestLoader | None,
    now: datetime,
    report: VerifyReport,
) -> None:
    owner_parties: dict[str, Party | None] = {}
    for ds in contract["datasets"]:
        dataset_id, version = ds["dataset_id"], ds["version"]
        owner = dataset_owner(dataset_id)
        try:
            descriptor = cs.get_dataset_descriptor(dataset_id, version)
        except DepaError as exc:
            report.errors.append(f"{dataset_id}@{version}: not in the CS catalog: {exc}")
            continue
        for err in schema_errors(descriptor, "dataset-descriptor"):
            report.errors.append(f"{dataset_id}@{version}: catalog descriptor {err}")
        if descriptor.get("manifest_digest") != ds["manifest_digest"]:
            report.errors.append(f"{dataset_id}@{version}: manifest_digest differs from the catalog")
        if owner != party_id:
            continue
        try:
            record = kms.get_dataset(dataset_id)
        except DepaError as exc:
            report.errors.append(f"{dataset_id}: KMS dataset record cannot be read: {exc}")
            continue
        if record.owner != party_id:
            report.errors.append(f"{dataset_id}: KMS owner is {record.owner}, not {party_id}")
        if record.state != "active":
            report.errors.append(f"{dataset_id}: KMS dataset state is {record.state}")
        findings = evaluate_policy(record.policy, contract, dataset_id=dataset_id, now=now)
        report.errors.extend(findings.errors)
        report.warnings.extend(findings.warnings)
        if manifest_loader is None:
            report.warnings.append(f"{dataset_id}: manifest signature not re-verified (no storage access given)")
            continue
        if owner not in owner_parties:
            owner_parties[owner] = _party(cs, owner, report)
        owner_party = owner_parties[owner]
        if owner_party is None:
            continue
        try:
            raw, jws = manifest_loader(descriptor)
            verify_manifest(
                raw,
                jws,
                owner_party.key_lookup(),
                expected_digest=ds["manifest_digest"],
                expected_dataset_id=dataset_id,
                expected_version=version,
            )
            report.info.append(f"{dataset_id}@{version}: manifest signature and digest verified")
        except DepaError as exc:
            report.errors.append(f"{dataset_id}@{version}: manifest verification failed: {exc}")


def _check_computation(contract: Mapping[str, Any], kms: KmsClient, cs: CsClient, report: VerifyReport) -> None:
    computation = contract["computation"]
    tier = computation["tier"]
    families = {r["document"].get("family") for r in computation["recipes"]}
    for r in computation["recipes"]:
        doc = r["document"]
        uses = ", ".join(str(s.get("uses")) for s in doc.get("steps", []))
        report.info.append(f"recipe {r['digest']}: {doc.get('name')} (family {doc.get('family')}; steps: {uses})")
    if tier == 3:
        custom = computation["custom_ccr"]
        report.warnings.append(
            f"tier 3 custom CCR {custom['image']} (measurement {custom['measurement_id']}, "
            f"review report {custom['review_report_digest']}): review the source {custom['source']}"
        )
        return
    covered_platforms: set[str] = set()
    covered_families: set[str] = set()
    for release_id in computation["ccr_releases"]:
        records = []
        for client in (kms, cs):
            try:
                entry = client.get_release(release_id)
            except DepaError as exc:
                report.errors.append(f"release {release_id}: not readable from {client.service_name}: {exc}")
                continue
            if not entry.approved:
                report.errors.append(f"release {release_id} is {entry.state} in {client.service_name}")
            records.append(entry.release)
        if len(records) == 2 and digest(records[0]) != digest(records[1]):
            report.errors.append(f"release {release_id} differs between the KMS and CS registries")
        if not records:
            continue
        release = records[0]
        for err in schema_errors(release, "ccr-release"):
            report.errors.append(f"release {release_id}: {err}")
        if release.get("platform") not in computation["platforms"]:
            report.warnings.append(
                f"release {release_id} is for platform {release.get('platform')}, not in the contract"
            )
            continue
        if tier not in release.get("tiers", []):
            report.warnings.append(f"release {release_id} does not support tier {tier}")
            continue
        covered_platforms.add(str(release.get("platform")))
        covered_families.update(str(f) for f in release.get("families", []))
        report.info.append(
            f"release {release_id}: {release.get('platform')} measurements {release.get('measurements')}"
        )
    for platform in computation["platforms"]:
        if platform not in covered_platforms:
            report.warnings.append(f"no listed release can run tier {tier} on {platform}")
    missing = families - covered_families
    if missing:
        report.errors.append(f"no listed release contains trainer family {sorted(missing)}")


def _check_output(contract: Mapping[str, Any], kms: KmsClient, cs: CsClient, report: VerifyReport) -> None:
    output = contract["output"]
    location = output["location"]
    report.info.append(
        f"outputs: mode {output['mode']}, formats {output['allowed_formats']}, max {output['max_bytes']} bytes, "
        f"to {location['connector']} {location['uri']}; metrics {output['metrics_allowlist']}"
    )
    if output["mode"] == "tee-only":
        report.info.append("outputs are sealed to the KMS output key and never released to a party")
        return
    for recipient in output.get("recipients", []):
        _, party_id, kid = parse_recipient(recipient)
        keys = []
        for client in (cs, kms):
            party = _party(client, party_id, report)
            if party is None:
                continue
            if not party.active:
                report.errors.append(f"recipient party {party_id} is {party.status} in {client.service_name}")
            try:
                keys.append(party.hpke_key(kid))
            except DepaError as exc:
                report.errors.append(f"{client.service_name}: {exc}")
        if len(keys) == 2 and keys[0] != keys[1]:
            report.errors.append(f"recipient {recipient}: HPKE key differs between the CS and KMS registries")


def _check_terms(contract: Mapping[str, Any], loader: TextLoader | None, report: VerifyReport) -> None:
    """Spec §8.1: ``legal_text_digest = sha256(exact bytes)``, checked against ``terms.uri``."""
    terms = contract.get("terms") or {}
    expected, uri = terms.get("legal_text_digest"), terms.get("uri")
    if expected and uri:
        if loader is None:
            report.warnings.append("legal text not checked (download disabled)")
            return
        try:
            actual = "sha256:" + sha256_hex(loader(str(uri)))
        except (DepaError, httpx.HTTPError) as exc:
            report.errors.append(f"legal text at {uri} cannot be downloaded: {exc}")
            return
        if actual != expected:
            report.errors.append(f"legal text at {uri} does not match terms.legal_text_digest")
        else:
            report.info.append(f"legal text verified ({uri})")
    elif expected:
        report.warnings.append("terms.legal_text_digest given without terms.uri: obtain and check the text yourself")
    elif uri:
        report.warnings.append(f"terms.uri {uri} is not bound by a legal_text_digest")
