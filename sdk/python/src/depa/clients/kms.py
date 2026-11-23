"""Typed client for ``depa-kms`` (spec §5, §7.1, §9.4, §10, §11)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from depa.clients.base import BaseClient
from depa.clients.http import path_segment
from depa.core.ids import KMS_SERVICE, check_dataset_id
from depa.core.keys import Signer, x25519_public_from_b64
from depa.errors import ServiceError, ValidationError


@dataclass(frozen=True)
class DatasetKey:
    kid: str
    x25519: bytes
    state: str


@dataclass(frozen=True)
class DatasetRecord:
    dataset_id: str
    owner: str
    keys: tuple[DatasetKey, ...]
    policy: dict[str, Any] | None
    state: str
    raw: dict[str, Any]

    @classmethod
    def parse(cls, obj: object) -> DatasetRecord:
        if not isinstance(obj, dict):
            raise ValidationError("dataset record must be an object", code="bad_dataset")
        keys = []
        for k in obj.get("keys", []):
            if not isinstance(k, dict) or not isinstance(k.get("kid"), str):
                raise ValidationError("malformed dataset key", code="bad_dataset")
            keys.append(DatasetKey(k["kid"], x25519_public_from_b64(k.get("x25519")), str(k.get("state", ""))))
        policy = obj.get("policy")
        return cls(
            dataset_id=check_dataset_id(obj.get("dataset_id")),
            owner=str(obj.get("owner", "")),
            keys=tuple(keys),
            policy=policy if isinstance(policy, dict) else None,
            state=str(obj.get("state", "")),
            raw=obj,
        )

    def current_key(self) -> DatasetKey:
        """The ``current`` key (highest ``#n``), used to encrypt new dataset versions."""
        active = [k for k in self.keys if k.state == "current"]
        if not active:
            raise ValidationError(f"dataset {self.dataset_id} has no active key", code="no_active_key")

        def version(key: DatasetKey) -> int:
            suffix = key.kid.rsplit("#", 1)[-1]
            return int(suffix) if suffix.isdigit() else -1

        return max(active, key=version)

    def key(self, kid: str) -> DatasetKey:
        for k in self.keys:
            if k.kid == kid:
                return k
        raise ValidationError(f"dataset {self.dataset_id} has no key {kid}", code="unknown_kid")


def dataset_path(dataset_id: str) -> str:
    """``/datasets/{owner}/{name}`` (two path segments, as routed by the KMS)."""
    owner, name = check_dataset_id(dataset_id).split("/")
    return f"/datasets/{path_segment(owner)}/{path_segment(name)}"


class KmsClient(BaseClient):
    service_name = KMS_SERVICE

    # ---- datasets (§7.1) -------------------------------------------------------------------

    def create_dataset(self, signer: Signer, dataset_id: str, description: str) -> DatasetRecord:
        body = {"dataset_id": check_dataset_id(dataset_id), "description": description}
        result = self.http.signed("POST", "/datasets", signer, "dataset.create", body)
        return (
            DatasetRecord.parse(result)
            if isinstance(result, dict) and "keys" in result
            else self.get_dataset(dataset_id)
        )

    def get_dataset(self, dataset_id: str) -> DatasetRecord:
        return DatasetRecord.parse(self.http.get(dataset_path(dataset_id)))

    def set_policy(self, signer: Signer, dataset_id: str, policy: Mapping[str, Any]) -> DatasetRecord:
        path = f"{dataset_path(dataset_id)}/policy"
        # The signed body names the dataset so the signature cannot be redirected to another
        # dataset of the same owner (the KMS requires ``{dataset_id, policy}``; see report).
        body = {"dataset_id": dataset_id, "policy": dict(policy)}
        return DatasetRecord.parse(self.http.signed("PUT", path, signer, "dataset.set_policy", body))

    def policy_history(self, dataset_id: str) -> dict[str, Any]:
        return self._obj(self.http.get(f"{dataset_path(dataset_id)}/policy/history"), "policy history")

    def rotate_dataset(self, signer: Signer, dataset_id: str, *, retire_previous: bool = False) -> Any:
        path = f"{dataset_path(dataset_id)}/rotate"
        body = {"dataset_id": dataset_id, "retire_previous": retire_previous}
        return self.http.signed("POST", path, signer, "dataset.rotate", body)

    def suspend_dataset(self, signer: Signer, dataset_id: str, reason: str) -> Any:
        path = f"{dataset_path(dataset_id)}/suspend"
        return self.http.signed("POST", path, signer, "dataset.suspend", {"dataset_id": dataset_id, "reason": reason})

    def resume_dataset(self, signer: Signer, dataset_id: str, reason: str) -> Any:
        path = f"{dataset_path(dataset_id)}/resume"
        return self.http.signed("POST", path, signer, "dataset.resume", {"dataset_id": dataset_id, "reason": reason})

    # ---- CCR-facing (§9.4, §10, §11) -------------------------------------------------------

    def release_keys(
        self,
        *,
        binding: Mapping[str, Any],
        attestation: Mapping[str, Any],
        rat: str,
        dataset_id: str,
        wrapping_key_pem: str,
        kids: Sequence[str] | None = None,
        bearer: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "binding": dict(binding),
            "attestation": dict(attestation),
            "rat": rat,
            "dataset_id": dataset_id,
            "wrappingKey": wrapping_key_pem,
        }
        if kids is not None:
            body["kids"] = list(kids)
        result = self.http.post("/keys/release", body, headers=self._bearer(bearer))
        obj = self._obj(result, "keys/release")
        if not isinstance(obj.get("keys"), list) or not isinstance(obj.get("release_receipt"), str):
            raise ServiceError("keys/release: malformed response", service=KMS_SERVICE, code="bad_response")
        return obj

    def oidc_token(
        self,
        *,
        binding: Mapping[str, Any],
        attestation: Mapping[str, Any],
        rat: str,
        resource: Mapping[str, Any],
        audience: str,
        bearer: str | None = None,
    ) -> str:
        body = {
            "binding": dict(binding),
            "attestation": dict(attestation),
            "rat": rat,
            "resource": dict(resource),
            "audience": audience,
        }
        obj = self._obj(self.http.post("/oidc/token", body, headers=self._bearer(bearer)), "oidc/token")
        for field in ("token", "id_token", "access_token"):
            value = obj.get(field)
            if isinstance(value, str) and value.count(".") == 2:
                return value
        raise ServiceError("oidc/token: response carries no JWT", service=KMS_SERVICE, code="bad_response")

    def oidc_configuration(self) -> dict[str, Any]:
        return self._obj(self.http.get("/oidc/.well-known/openid-configuration"), "openid-configuration")

    def oidc_jwks(self) -> dict[str, Any]:
        return self._obj(self.http.get("/oidc/jwks"), "oidc/jwks")

    def output_key(
        self,
        contract_id: str,
        *,
        binding: Mapping[str, Any],
        attestation: Mapping[str, Any],
        rat: str,
        bearer: str | None = None,
    ) -> tuple[str, bytes]:
        """Per-contract output public key for mode ``tee-only`` -> ``(kid, x25519)``."""
        body = {"binding": dict(binding), "attestation": dict(attestation), "rat": rat}
        path = f"/outputs/{path_segment(contract_id)}/key"
        obj = self._obj(self.http.post(path, body, headers=self._bearer(bearer)), "outputs/key")
        kid = obj.get("kid")
        if not isinstance(kid, str) or not kid:
            raise ServiceError("outputs/key: malformed response", service=KMS_SERVICE, code="bad_response")
        return kid, x25519_public_from_b64(obj.get("x25519"))

    def output_release(
        self,
        contract_id: str,
        *,
        binding: Mapping[str, Any],
        attestation: Mapping[str, Any],
        wrapping_key_pem: str,
        bearer: str | None = None,
    ) -> dict[str, Any]:
        """DEPA-inferencing integration point (§11): attested inference workloads only."""
        body = {"binding": dict(binding), "attestation": dict(attestation), "wrappingKey": wrapping_key_pem}
        path = f"/outputs/{path_segment(contract_id)}/release"
        return self._obj(self.http.post(path, body, headers=self._bearer(bearer)), "outputs/release")
