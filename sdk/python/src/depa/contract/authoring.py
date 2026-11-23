"""YAML templates -> canonical protocol documents.

Contract templates (``scenarios/<name>/contract.yaml``) become a ``depa-training/contract/v2``
document: a fresh ``contract_id``, ``validity`` resolved (``now`` / ``duration_days``), recipes
loaded from ``path`` and embedded with their digests, and each dataset's ``manifest_digest``
resolved from the CS catalog (or given explicitly).

Dataset templates (``scenarios/<name>/datasets/*.yaml``) hold a catalog descriptor plus two
SDK-local sections that are never published: ``source`` (plaintext directory + include globs
for ``depa dataset encrypt``) and ``policy`` (the Release Policy for ``depa dataset policy set``).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from depa.contract.recipe import load_recipe
from depa.contract.schema import require_schema
from depa.contract.validate import require_valid_contract
from depa.contract.yamlio import load_template
from depa.core.dedf import Manifest
from depa.core.encoding import digest, is_digest, sha256_hex
from depa.core.ids import check_dataset_id
from depa.core.timeutil import format_rfc3339, parse_rfc3339, utcnow
from depa.errors import ValidationError

CONTRACT_SCHEMA = "depa-training/contract/v2"
ManifestResolver = Callable[[str, str], str]

_TEMPLATE_ONLY_VALIDITY = {"not_before", "not_after", "duration_days"}


def _resolve_validity(spec: object, now: datetime) -> dict[str, str]:
    if not isinstance(spec, dict) or set(spec) - _TEMPLATE_ONLY_VALIDITY:
        raise ValidationError("validity must contain not_before and not_after or duration_days", code="bad_template")
    start_raw = spec.get("not_before", "now")
    start = now if start_raw == "now" else parse_rfc3339(start_raw)
    if "not_after" in spec and "duration_days" in spec:
        raise ValidationError("validity: give either not_after or duration_days", code="bad_template")
    if "not_after" in spec:
        end = parse_rfc3339(spec["not_after"])
    else:
        days = spec.get("duration_days")
        if not isinstance(days, int) or isinstance(days, bool) or not 1 <= days <= 3650:
            raise ValidationError("validity.duration_days must be an integer in 1..3650", code="bad_template")
        end = start + timedelta(days=days)
    return {"not_before": format_rfc3339(start), "not_after": format_rfc3339(end)}


def map_dataset_id(dataset_id: str, party_map: Mapping[str, str]) -> str:
    """``<owner>/<name>`` with the owner replaced through ``party_map`` (unmapped owners unchanged)."""
    owner, name = check_dataset_id(dataset_id).split("/", 1)
    return f"{party_map.get(owner, owner)}/{name}"


def map_recipe(document: Mapping[str, Any], party_map: Mapping[str, str]) -> dict[str, Any]:
    """The recipe with ``inputs[].dataset_id`` mapped (its digest changes accordingly)."""
    out = dict(document)
    out["inputs"] = [
        dict(item, dataset_id=map_dataset_id(str(item["dataset_id"]), party_map)) if isinstance(item, dict) else item
        for item in document.get("inputs", [])
    ]
    return out


def _map_parties(doc: dict[str, Any], party_map: Mapping[str, str], version_suffix: str) -> None:
    """Rewrite a rendered contract template onto the environment's party slots (in place)."""
    parties = doc.get("parties")
    if isinstance(parties, dict):
        if isinstance(parties.get("tdc"), str):
            parties["tdc"] = party_map.get(parties["tdc"], parties["tdc"])
        if isinstance(parties.get("tdps"), list):
            parties["tdps"] = list(dict.fromkeys(party_map.get(p, p) for p in parties["tdps"]))
    for entry in doc.get("datasets") or []:
        if isinstance(entry, dict) and isinstance(entry.get("dataset_id"), str):
            entry["dataset_id"] = map_dataset_id(entry["dataset_id"], party_map)
            if isinstance(entry.get("version"), str):
                entry["version"] = entry["version"] + version_suffix
    output = doc.get("output")
    if isinstance(output, dict) and isinstance(output.get("recipients"), list):
        mapped = []
        for ref in output["recipients"]:
            if isinstance(ref, str) and ref.startswith("party:") and "#" in ref:
                party, kid = ref[len("party:") :].split("#", 1)
                mapped.append(f"party:{party_map.get(party, party)}#{kid}")
            else:
                mapped.append(ref)
        output["recipients"] = mapped


def _resolve_recipes(items: object, base_dir: Path, party_map: Mapping[str, str] | None = None) -> list[dict[str, Any]]:
    if not isinstance(items, list) or not items:
        raise ValidationError("computation.recipes must be a non-empty list", code="bad_template")
    out = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValidationError(f"computation.recipes[{i}] must be a mapping", code="bad_template")
        if "path" in item:
            document = load_recipe((base_dir / str(item["path"])).resolve())
        elif isinstance(item.get("document"), dict):
            document = item["document"]
        else:
            raise ValidationError(f"computation.recipes[{i}] needs 'path' or 'document'", code="bad_template")
        recipe_digest = digest(document)
        if "digest" in item and item["digest"] != recipe_digest:
            raise ValidationError(f"computation.recipes[{i}].digest does not match the recipe", code="bad_template")
        if party_map:
            document = map_recipe(document, party_map)
            recipe_digest = digest(document)
        out.append({"digest": recipe_digest, "document": document})
    return out


def build_contract(
    template_path: Path,
    *,
    env: Mapping[str, str],
    resolve_manifest: ManifestResolver | None,
    contract_id: str | None = None,
    now: datetime | None = None,
    customize: Callable[[dict[str, Any]], None] | None = None,
    party_map: Mapping[str, str] | None = None,
    version_suffix: str = "",
) -> dict[str, Any]:
    """Render, resolve and validate a contract template. Raises ``ValidationError``.

    ``customize`` may adjust the rendered document (e.g. a deployment's output location) before
    the final validation.
    """
    doc = load_template(template_path, env)
    if party_map:  # slot mapping onto a fixed-party environment (see depa.deployment)
        _map_parties(doc, party_map, version_suffix)
    if doc.get("schema") != CONTRACT_SCHEMA:
        raise ValidationError(f"template schema must be {CONTRACT_SCHEMA}", code="bad_template")
    current = now or utcnow()
    contract: dict[str, Any] = {k: v for k, v in doc.items() if k not in ("validity", "datasets", "computation")}
    contract["contract_id"] = contract_id or str(uuid.uuid4())
    contract["validity"] = _resolve_validity(doc.get("validity"), current)
    datasets = []
    for i, entry in enumerate(doc.get("datasets") or []):
        if not isinstance(entry, dict):
            raise ValidationError(f"datasets[{i}] must be a mapping", code="bad_template")
        dataset_id = check_dataset_id(entry.get("dataset_id"))
        version = entry.get("version")
        if not isinstance(version, str):
            raise ValidationError(f"datasets[{i}].version must be a string", code="bad_template")
        manifest_digest = entry.get("manifest_digest")
        if manifest_digest is None:
            if resolve_manifest is None:
                raise ValidationError(
                    f"datasets[{i}]: manifest_digest not given and no catalog to resolve it",
                    code="bad_template",
                )
            manifest_digest = resolve_manifest(dataset_id, version)
        if not is_digest(manifest_digest):
            raise ValidationError(f"datasets[{i}].manifest_digest is malformed", code="bad_template")
        datasets.append({"dataset_id": dataset_id, "version": version, "manifest_digest": manifest_digest})
    contract["datasets"] = datasets
    computation = dict(doc.get("computation") or {})
    computation["recipes"] = _resolve_recipes(computation.get("recipes"), template_path.parent, party_map)
    if computation.get("tier") in (1, 2):
        computation.setdefault("custom_ccr", None)
    contract["computation"] = computation
    terms = contract.get("terms")
    if isinstance(terms, dict) and "legal_text_path" in terms:  # digest of the exact bytes (spec §8.1)
        text_path = template_path.parent / str(terms.pop("legal_text_path"))
        try:
            terms["legal_text_digest"] = "sha256:" + sha256_hex(text_path.read_bytes())
        except OSError as exc:
            raise ValidationError(f"cannot read legal text {text_path}: {exc.strerror}", code="bad_template") from exc
    location = contract.get("output", {}).get("location")
    if isinstance(location, dict) and location.get("federation") == {}:
        location.pop("federation")
    if contract.get("privacy", {}).get("mode") == "none":
        contract["privacy"] = {"mode": "none"}
    if customize is not None:
        customize(contract)
    require_valid_contract(contract)
    return contract


# ---------------------------------------------------------------------------------------------
# Dataset templates


@dataclass(frozen=True)
class DatasetSource:
    path: Path
    include: tuple[str, ...] | None


@dataclass(frozen=True)
class DatasetTemplate:
    descriptor: dict[str, Any]
    policy: dict[str, Any] | None
    source: DatasetSource | None

    @property
    def dataset_id(self) -> str:
        return str(self.descriptor["dataset_id"])

    @property
    def version(self) -> str:
        return str(self.descriptor["version"])


_DESCRIPTOR_FILLED = ("manifest_digest", "size_bytes", "records")


def load_dataset_template(path: Path, env: Mapping[str, str]) -> DatasetTemplate:
    doc = load_template(path, env)
    policy = doc.pop("policy", None)
    source_raw = doc.pop("source", None)
    source = None
    if source_raw is not None:
        if not isinstance(source_raw, dict) or not isinstance(source_raw.get("path"), str):
            raise ValidationError(f"{path}: source.path is required", code="bad_template")
        include = source_raw.get("include")
        if include is not None and (not isinstance(include, list) or not all(isinstance(p, str) for p in include)):
            raise ValidationError(f"{path}: source.include must be a list of globs", code="bad_template")
        # source.path is relative to the scenario directory (the parent of ``datasets/``).
        base = path.parent.parent if path.parent.name == "datasets" else path.parent
        source = DatasetSource((base / source_raw["path"]).resolve(), tuple(include) if include else None)
    if policy is not None:
        require_schema(policy, "release-policy-v1", f"{path}: policy")
    check_dataset_id(doc.get("dataset_id"))
    if doc.get("federation") == {}:
        doc.pop("federation")
    return DatasetTemplate(descriptor=doc, policy=policy, source=source)


def finalize_descriptor(
    descriptor: Mapping[str, Any], manifest: Manifest, *, records: int | None = None
) -> dict[str, Any]:
    """Fill ``manifest_digest`` / ``size_bytes`` (and ``records``) and validate the descriptor."""
    if manifest.dataset_id != descriptor.get("dataset_id") or manifest.version != descriptor.get("version"):
        raise ValidationError("manifest does not belong to this dataset descriptor", code="manifest_mismatch")
    out = {k: v for k, v in descriptor.items() if k not in _DESCRIPTOR_FILLED}
    kms = dict(out.get("kms") or {})
    kms["kid"] = manifest.wrapped_dek()["kid"]  # the key actually used (may be a rotated #n)
    out["kms"] = kms
    out["manifest_digest"] = manifest.digest
    out["size_bytes"] = manifest.total_ciphertext_size
    if records is not None:
        out["records"] = records
    require_schema(out, "dataset-descriptor", "dataset descriptor")
    from depa.storage.factory import validate_location  # noqa: PLC0415 - avoids an import cycle

    validate_location(out["location"], federation=out.get("federation"))
    return out
