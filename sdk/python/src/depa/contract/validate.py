"""Offline contract validation: schema plus every internal consistency rule (spec §8)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from depa.contract.recipe import against_contract_errors, structural_errors, trainer_errors
from depa.contract.schema import schema_errors
from depa.core.encoding import digest
from depa.core.ids import dataset_owner, parse_recipient
from depa.core.timeutil import parse_rfc3339
from depa.errors import ValidationError


def contract_errors(contract: Mapping[str, Any]) -> list[str]:
    errs = schema_errors(dict(contract), "contract-v2")
    if errs:
        return errs
    if parse_rfc3339(contract["validity"]["not_before"]) >= parse_rfc3339(contract["validity"]["not_after"]):
        errs.append("validity.not_before must be earlier than validity.not_after")
    parties = contract["parties"]
    owners = [dataset_owner(d["dataset_id"]) for d in contract["datasets"]]
    if set(parties["tdps"]) != set(owners):
        errs.append(f"parties.tdps {sorted(parties['tdps'])} must equal the dataset owners {sorted(set(owners))}")
    ids = [d["dataset_id"] for d in contract["datasets"]]
    if len(ids) != len(set(ids)):
        errs.append("a dataset appears more than once")
    if parties.get("ccrp") and parties["ccrp"] in (parties["tdc"], *parties["tdps"]):
        errs.append("parties.ccrp must be a distinct party")
    computation = contract["computation"]
    tier = computation["tier"]
    custom = computation.get("custom_ccr")
    releases = computation.get("ccr_releases", [])
    if tier in (1, 2):
        if not releases:
            errs.append("tier 1/2 contracts must list computation.ccr_releases")
        if custom:
            errs.append("computation.custom_ccr is only allowed for tier 3")
    else:
        if not custom:
            errs.append("tier 3 contracts require computation.custom_ccr")
        elif custom["platform"] not in computation["platforms"]:
            errs.append("custom_ccr.platform must be one of computation.platforms")
        if releases:
            errs.append("tier 3 contracts must not list ccr_releases")
    digests = [r["digest"] for r in computation["recipes"]]
    if len(digests) != len(set(digests)):
        errs.append("a recipe appears more than once")
    for i, entry in enumerate(computation["recipes"]):
        document = entry["document"]
        if digest(document) != entry["digest"]:
            errs.append(f"computation.recipes[{i}].digest does not match digest(document)")
            continue
        errs.extend(f"recipes[{i}]: {e}" for e in structural_errors(document))
        errs.extend(f"recipes[{i}]: {e}" for e in against_contract_errors(document, contract))
        errs.extend(f"recipes[{i}] (depa_train): {e}" for e in trainer_errors(document))
    output = contract["output"]
    recipients = output.get("recipients", [])
    if output["mode"] == "tdc":
        if not recipients:
            errs.append("output.recipients is required for output mode tdc")
        allowed_parties = {parties["tdc"], *parties["tdps"]}
        for r in recipients:
            try:
                kind, party, _ = parse_recipient(r)
            except ValidationError as exc:
                errs.append(str(exc))
                continue
            if kind != "party":
                errs.append(f"output recipient {r} must be a party key in mode tdc")
            elif party not in allowed_parties:
                errs.append(f"output recipient {r} is not a contract party (TDC or TDP)")
    elif recipients:
        errs.append("output.recipients must be empty for mode tee-only (outputs are sealed to the KMS)")
    from depa.storage.factory import validate_location  # noqa: PLC0415 - avoids an import cycle

    try:
        validate_location(output["location"], for_output=True)
    except ValidationError as exc:
        errs.append(f"output.location: {exc}")
    return errs


def require_valid_contract(contract: Mapping[str, Any]) -> None:
    errs = contract_errors(contract)
    if errs:
        raise ValidationError("contract is invalid:\n  " + "\n  ".join(errs), code="invalid_contract")
