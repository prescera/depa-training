"""Typed client for ``depa-contract-service`` (spec §7.2, §8, §9.3, §9.5)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from depa.clients.base import BaseClient
from depa.clients.http import path_segment
from depa.clients.kms import dataset_path
from depa.core.ids import CS_SERVICE, check_party_id
from depa.core.keys import Signer
from depa.errors import ServiceError


class CsClient(BaseClient):
    service_name = CS_SERVICE

    # ---- catalog (§7.2) --------------------------------------------------------------------

    def publish_dataset(self, signer: Signer, descriptor: Mapping[str, Any]) -> Any:
        return self.http.signed("POST", "/catalog/datasets", signer, "catalog.publish", descriptor)

    def list_datasets(self) -> Any:
        return self.http.get("/catalog/datasets")

    def get_dataset(self, dataset_id: str) -> dict[str, Any]:
        path = f"/catalog{dataset_path(dataset_id)}"
        return self._obj(self.http.get(path), "catalog dataset")

    def get_dataset_version(self, dataset_id: str, version: str) -> dict[str, Any]:
        path = f"/catalog{dataset_path(dataset_id)}/versions/{path_segment(version)}"
        return self._obj(self.http.get(path), "catalog dataset version")

    def get_dataset_descriptor(self, dataset_id: str, version: str) -> dict[str, Any]:
        """The published descriptor of one version (the record wraps it with publisher metadata)."""
        record = self.get_dataset_version(dataset_id, version)
        descriptor = record.get("descriptor")
        if (
            not isinstance(descriptor, dict)
            or descriptor.get("dataset_id") != dataset_id
            or descriptor.get("version") != version
            or descriptor.get("manifest_digest") != record.get("manifest_digest")
        ):
            raise ServiceError("catalog record is malformed", service=CS_SERVICE, code="bad_response")
        return descriptor

    # ---- contracts (§8.2) ------------------------------------------------------------------

    def propose_contract(self, signer: Signer, contract: Mapping[str, Any]) -> Any:
        return self.http.signed("POST", "/contracts", signer, "contract.propose", contract)

    def get_contract(self, contract_id: str) -> dict[str, Any]:
        record = self._obj(self.http.get(f"/contracts/{path_segment(contract_id)}"), "contract")
        if not isinstance(record.get("contract"), dict) or not isinstance(record.get("contract_digest"), str):
            raise ServiceError("contract record is malformed", service=CS_SERVICE, code="bad_response")
        return record

    def list_contracts(self, party: str) -> Any:
        return self.http.get("/contracts", params={"party": check_party_id(party)})

    def sign_contract(self, signer: Signer, contract_id: str, contract_digest: str) -> Any:
        path = f"/contracts/{path_segment(contract_id)}/signatures"
        return self.http.signed("POST", path, signer, "contract.sign", {"contract_digest": contract_digest})

    def revoke_contract(self, signer: Signer, contract_id: str, reason: str) -> Any:
        path = f"/contracts/{path_segment(contract_id)}/revoke"
        body = {"contract_id": contract_id, "reason": reason}  # path id echoed, as the KMS requires
        return self.http.signed("POST", path, signer, "contract.revoke", body)

    # ---- runs (§9.3, §9.5) -----------------------------------------------------------------

    def authorize_run(
        self,
        contract_id: str,
        *,
        binding: Mapping[str, Any],
        attestation: Mapping[str, Any],
        bearer: str | None = None,
    ) -> dict[str, Any]:
        path = f"/contracts/{path_segment(contract_id)}/runs"
        body = {"binding": dict(binding), "attestation": dict(attestation)}
        obj = self._obj(self.http.post(path, body, headers=self._bearer(bearer)), "runs")
        if not all(isinstance(obj.get(k), str) for k in ("run_id", "rat")):
            raise ServiceError("run authorization: malformed response", service=CS_SERVICE, code="bad_response")
        return {**obj, "txid": self.http.last_transaction_id}

    def refresh_rat(self, contract_id: str, run_id: str, jws: str) -> dict[str, Any]:
        """Spec §9.3a: a fresh RAT for an authorized, unfinished run (body signed by the run key)."""
        path = f"/contracts/{path_segment(contract_id)}/runs/{path_segment(run_id)}/rat"
        obj = self._obj(self.http.post(path, {"jws": jws}), "rat refresh")
        if not isinstance(obj.get("rat"), str):
            raise ServiceError("rat refresh: malformed response", service=CS_SERVICE, code="bad_response")
        return obj

    def post_run_event(self, contract_id: str, run_id: str, jws: str) -> dict[str, Any]:
        path = f"/contracts/{path_segment(contract_id)}/runs/{path_segment(run_id)}/events"
        result = self.http.post(path, {"jws": jws})
        return {**(result if isinstance(result, dict) else {}), "txid": self.http.last_transaction_id}

    def get_run(self, run_id: str) -> dict[str, Any]:
        return self._obj(self.http.get(f"/runs/{path_segment(run_id)}"), "run")

    def list_runs(self, contract_id: str) -> list[dict[str, Any]]:
        result = self._obj(self.http.get(f"/contracts/{path_segment(contract_id)}/runs"), "runs")
        return [r for r in result.get("runs", []) if isinstance(r, dict)]
