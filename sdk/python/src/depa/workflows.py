"""Multi-step party workflows shared by the CLI and the dev scenario runner."""

from __future__ import annotations

import io
import shutil
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import httpx

from depa.clients.base import BaseClient
from depa.clients.cs import CsClient
from depa.clients.http import pinned_ssl_context
from depa.clients.kms import KmsClient
from depa.contract.authoring import DatasetTemplate, ManifestResolver, finalize_descriptor
from depa.contract.verify import ManifestLoader
from depa.core import dedf
from depa.core.encoding import b64url_encode, canonicalize, digest, loads_object
from depa.core.jws import jwks_lookup
from depa.core.keys import HpkeKeyPair, Signer
from depa.errors import ConfigError, ValidationError, VerificationError
from depa.profile import Profile
from depa.storage import Connector
from depa.storage.factory import party_connector

# ---------------------------------------------------------------------------------------------
# Trust bundle for CCR deployments


def trust_bundle_document(profile: Profile, kms: BaseClient, cs: BaseClient) -> dict[str, Any]:
    """The CCR trust anchors (spec §9.1): both services' URL, pinned TLS certificate and JWKS.

    Deterministic for a given deployment, so ``depa dc setup`` (which sets the expected digest in
    both services) and ``depa job submit`` (which passes the bundle to the CCR) agree.
    """

    def pem(which: str) -> str:
        return profile.service_cert_pem(which)  # verbatim (the digest is over the exact PEM text)

    return {
        "v": 1,
        "cs": {"url": cs.base_url, "service_cert_pem": pem("cs"), "jwks": list(cs.info().jwks)},
        "kms": {"url": kms.base_url, "service_cert_pem": pem("kms"), "jwks": list(kms.info().jwks)},
    }


def build_trust_bundle(profile: Profile, kms: BaseClient, cs: BaseClient) -> str:
    """``DEPA_TRUST_BUNDLE`` value: base64url(JCS(bundle)); the binding carries ``digest(bundle)``."""
    return b64url_encode(canonicalize(trust_bundle_document(profile, kms, cs)))


def trust_bundle_settings_actions(
    profile: Profile, client: BaseClient, kms: BaseClient, cs: BaseClient
) -> list[dict[str, Any]]:
    """``set_settings.trust_bundle_digest`` for ``client`` unless it already holds the current value."""
    expected = digest(trust_bundle_document(profile, kms, cs))
    current = (client.gov_state().get("settings") or {}).get("trust_bundle_digest")
    if current == expected:
        return []
    return [{"name": "set_settings", "args": {"trust_bundle_digest": expected}}]


# ---------------------------------------------------------------------------------------------
# Datasets (TDP)


def encrypt_dataset(
    template: DatasetTemplate,
    *,
    kms: KmsClient,
    signer: Signer,
    out_dir: Path,
    source: Path | None = None,
    segment_size: int = dedf.DEFAULT_SEGMENT_SIZE,
    progress: Callable[[str, int, int], None] | None = None,
) -> dedf.Manifest:
    """Encrypt the template's source to the dataset's current KMS key (fetched over pinned TLS)."""
    src = source or (template.source.path if template.source else None)
    if src is None or not src.is_dir():
        raise ConfigError(f"dataset source directory not found: {src}")
    record = kms.get_dataset(template.dataset_id)
    key = record.current_key()
    manifest = dedf.encrypt_directory(
        src,
        out_dir,
        dataset_id=template.dataset_id,
        version=template.version,
        kms_kid=key.kid,
        kms_public_key=key.x25519,
        signer=signer,
        segment_size=segment_size,
        include=template.source.include if template.source and source is None else None,
        progress=progress,
    )
    return dedf.parse_manifest(manifest)


def load_encrypted(out_dir: Path) -> tuple[bytes, str, dedf.Manifest]:
    raw = (out_dir / dedf.MANIFEST_NAME).read_bytes()
    jws = (out_dir / dedf.MANIFEST_JWS_NAME).read_text().strip()
    return raw, jws, dedf.parse_manifest(loads_object(raw))


def upload_directory(connector: Connector, src_dir: Path, manifest: dedf.Manifest) -> int:
    """Upload ``objects/`` first and the manifest last (readers treat the manifest as the commit)."""
    count = 0
    for entry in manifest.files:
        key = dedf.object_key(entry.path)
        with (src_dir / key).open("rb") as src:
            connector.upload(key, src, entry.ciphertext_size)
        count += 1
    for name in (dedf.MANIFEST_JWS_NAME, dedf.MANIFEST_NAME):
        data = (src_dir / name).read_bytes()
        connector.upload(name, io.BytesIO(data), len(data))
    return count + 2


def publish_dataset(
    cs: CsClient,
    signer: Signer,
    template: DatasetTemplate,
    encrypted_dir: Path,
    *,
    records: int | None = None,
) -> dict[str, Any]:
    raw, jws, manifest = load_encrypted(encrypted_dir)
    dedf.verify_manifest(
        raw, jws, jwks_lookup([_self_jwk(signer)]), expected_digest=None, expected_signer_prefix=signer.kid
    )
    descriptor = finalize_descriptor(template.descriptor, manifest, records=records)
    cs.publish_dataset(signer, descriptor)
    return descriptor


def _self_jwk(signer: Signer) -> dict[str, Any]:
    jwk = dict(signer.public_jwk())
    jwk["kid"] = signer.kid
    return jwk


def catalog_resolver(cs: CsClient) -> ManifestResolver:
    def resolve(dataset_id: str, version: str) -> str:
        descriptor = cs.get_dataset_version(dataset_id, version)
        value = descriptor.get("manifest_digest")
        if not isinstance(value, str):
            raise ValidationError(f"catalog entry {dataset_id}@{version} has no manifest_digest", code="bad_descriptor")
        return value

    return resolve


def storage_manifest_loader(access_token: str | None = None) -> ManifestLoader:
    """Read manifests with the party's own storage credentials (for ``contract verify``)."""

    def load(descriptor: Mapping[str, Any]) -> tuple[bytes, str]:
        location = dict(descriptor["location"])
        connector = party_connector(location, access_token=access_token)
        raw = connector.read_bytes(dedf.MANIFEST_NAME, dedf.MAX_MANIFEST_BYTES)
        jws = connector.read_bytes(dedf.MANIFEST_JWS_NAME, 64 * 1024).decode("ascii")
        return raw, jws

    return load


# ---------------------------------------------------------------------------------------------
# Governance


def submit_to_both(
    kms: KmsClient,
    cs: CsClient,
    signer: Signer,
    actions: Sequence[Mapping[str, Any]],
    expires_in_s: int = 604800,
) -> dict[str, dict[str, Any]]:
    """Identical proposals to both services (e.g. ``approve_ccr_release``, spec §4)."""
    return {
        "kms": kms.gov_propose(signer, actions, expires_in_s),
        "cs": cs.gov_propose(signer, actions, expires_in_s),
    }


def latest_accepted_args(client: BaseClient, action: str) -> dict[str, Any] | None:
    """Arguments of the most recently accepted governance action ``action`` (None if never)."""
    found = None
    for proposal in sorted(client.gov_proposals(), key=lambda p: p.get("created_at", 0)):
        if proposal.get("state") == "accepted":
            for a in proposal.get("actions", []):
                if a.get("name") == action:
                    found = dict(a.get("args") or {})
    return found


def pinned_peer_jwks(client: BaseClient, peer_name: str) -> list[dict[str, Any]] | None:
    """The JWKS most recently pinned for ``peer_name`` by an accepted ``set_peer_service``."""
    pinned = None
    for proposal in sorted(client.gov_proposals(), key=lambda p: p.get("created_at", 0)):
        if proposal.get("state") != "accepted":
            continue
        for action in proposal.get("actions", []):
            args = action.get("args") or {}
            if action.get("name") == "set_peer_service" and args.get("name") == peer_name:
                pinned = list(args.get("jwks", []))
    return pinned


def audit(kms: KmsClient, cs: CsClient) -> dict[str, list[str]]:
    """``depa dc audit``: deployed bundles approved, peer pins current, OIDC gateway consistent."""
    errors: list[str] = []
    info: list[str] = []
    infos = {"kms": kms.info(), "cs": cs.info()}
    for name, i in infos.items():
        approved = i.raw.get("bundle_digest_approved")
        info.append(f"{name}: version {i.version}, bundle {i.bundle_digest}")
        if approved is not True:
            errors.append(f"{name}: deployed bundle {i.bundle_digest} is not a governance-approved app digest")
    for client, peer, peer_info in (
        (kms, "depa-contract-service", infos["cs"]),
        (cs, "depa-kms", infos["kms"]),
    ):
        pinned = pinned_peer_jwks(client, peer)
        if pinned is None:
            errors.append(f"{client.service_name}: no peer pin for {peer}")
        elif digest(sorted(pinned, key=lambda k: str(k.get("kid")))) != digest(
            sorted(peer_info.jwks, key=lambda k: str(k.get("kid")))
        ):
            errors.append(f"{client.service_name}: pinned {peer} JWKS differs from the live service keys")
        else:
            info.append(f"{client.service_name}: {peer} pin matches")
    try:
        config = kms.oidc_configuration()
        issuer_jwks_uri = str(config.get("jwks_uri", ""))
        internal = kms.oidc_jwks()
        if issuer_jwks_uri.startswith("https://"):
            with httpx.Client(verify=pinned_ssl_context(None), timeout=15) as http:
                public = http.get(issuer_jwks_uri)
            if public.status_code != 200 or digest(public.json()) != digest(internal):
                errors.append("public OIDC JWKS (gateway) differs from the KMS-internal JWKS")
            else:
                info.append("OIDC gateway JWKS matches the KMS")
    except (httpx.HTTPError, ValueError, ValidationError) as exc:
        errors.append(f"OIDC gateway check failed: {exc}")
    except Exception as exc:  # noqa: BLE001 - OIDC may be unconfigured; report and continue
        errors.append(f"OIDC configuration unavailable: {exc}")
    return {"errors": errors, "info": info}


# ---------------------------------------------------------------------------------------------
# Outputs (TDC)


def fetch_output(
    receipt: Mapping[str, Any],
    name: str,
    out_dir: Path,
    *,
    location: Mapping[str, Any],
    access_token: str | None,
) -> dedf.Manifest:
    """Download one output (manifest + objects) with the TDC's own credentials."""
    entry = next((o for o in receipt.get("outputs", []) if o.get("name") == name), None)
    if entry is None:
        raise ValidationError(f"receipt has no output {name!r}", code="no_output")
    base = {k: v for k, v in location.items() if k != "federation"}
    prefix_uri = str(entry["location"])
    if not prefix_uri.startswith(str(base["uri"])):
        raise VerificationError("receipt output location is outside the contract output location", code="bad_location")
    base["uri"] = prefix_uri
    connector = party_connector(base, access_token=access_token)
    raw = connector.read_bytes(dedf.MANIFEST_NAME, dedf.MAX_MANIFEST_BYTES)
    manifest = dedf.parse_manifest(loads_object(raw), output=True)
    if manifest.digest != entry["manifest_digest"]:
        raise VerificationError("output manifest does not match the receipt", code="manifest_digest")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / dedf.MANIFEST_NAME).write_bytes(raw)
    jws = connector.read_bytes(dedf.MANIFEST_JWS_NAME, 64 * 1024)
    (out_dir / dedf.MANIFEST_JWS_NAME).write_bytes(jws)
    for f in manifest.files:
        target = out_dir / dedf.object_key(f.path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with connector.open_read(dedf.object_key(f.path)) as src, target.open("wb") as dst:
            shutil.copyfileobj(src, dst, 1 << 20)
    return manifest


def decrypt_output(
    encrypted_dir: Path,
    out_dir: Path,
    *,
    key: HpkeKeyPair,
    recipient: str,
    expected_digest: str,
    run_key: Mapping[str, Any] | None = None,
) -> list[Path]:
    raw = (encrypted_dir / dedf.MANIFEST_NAME).read_bytes()
    manifest = dedf.parse_manifest(loads_object(raw), output=True)
    if manifest.digest != expected_digest:
        raise VerificationError("output manifest does not match the expected digest", code="manifest_digest")
    if run_key is not None:
        jws = (encrypted_dir / dedf.MANIFEST_JWS_NAME).read_text().strip()
        kid = str(run_key.get("kid", ""))
        dedf.verify_manifest(
            raw,
            jws,
            jwks_lookup([dict(run_key)]),
            expected_digest=expected_digest,
            expected_signer_prefix=kid,
            output=True,
        )
    wrapped = manifest.wrapped_dek_for(recipient)
    if wrapped.get("kid") != key.kid:
        raise ValidationError(
            f"output is sealed to {recipient} key {wrapped.get('kid')}, not {key.kid}", code="kid_mismatch"
        )
    dek = dedf.unwrap_dek(wrapped, key.private_key, dataset_id=manifest.dataset_id, version=manifest.version)
    written = []
    for f in manifest.files:
        target = out_dir / f.path
        with (encrypted_dir / dedf.object_key(f.path)).open("rb") as src:
            dedf.decrypt_to_path(dek, f, src, target, segment_size=manifest.segment_size)
        written.append(target)
    return written
