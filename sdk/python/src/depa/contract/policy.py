"""Release Policy v1 (spec §7.1) evaluated against a contract, as a TDP would before signing.

The KMS enforces the policy at every key release (§9.4 step 7); this mirrors those checks so a
TDP sees, before signing, which runs its policy would refuse. ``errors`` are conditions under
which no run could ever obtain the dataset key (or that contradict the TDP's stated intent);
``warnings`` narrow what is possible without making the contract useless.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from depa.contract.schema import schema_errors
from depa.core.timeutil import parse_rfc3339


@dataclass
class PolicyFindings:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def evaluate_policy(
    policy: Mapping[str, Any] | None, contract: Mapping[str, Any], *, dataset_id: str, now: datetime
) -> PolicyFindings:
    out = PolicyFindings()
    if policy is None:
        out.errors.append(f"{dataset_id}: no release policy is set (keys can never be released)")
        return out
    for err in schema_errors(dict(policy), "release-policy-v1"):
        out.errors.append(f"{dataset_id}: policy {err}")
    if out.errors:
        return out
    computation = contract["computation"]
    platforms = list(computation["platforms"])
    allowed_platforms = set(policy.get("allowed_platforms", []))
    blocked = [p for p in platforms if p not in allowed_platforms]
    if len(blocked) == len(platforms):
        out.errors.append(f"{dataset_id}: none of the contract platforms {platforms} is allowed by the policy")
    elif blocked:
        out.warnings.append(f"{dataset_id}: runs on {blocked} will be refused by the policy")
    if "virtual" in platforms and "virtual" in allowed_platforms:
        out.warnings.append(f"{dataset_id}: policy and contract allow the development-only 'virtual' platform")
    tier = computation["tier"]
    if tier not in policy.get("allowed_tiers", [1]):
        out.errors.append(f"{dataset_id}: tier {tier} is not allowed by the policy")
    tdcs = policy.get("allowed_tdcs", [])
    if "*" not in tdcs and contract["parties"]["tdc"] not in tdcs:
        out.errors.append(f"{dataset_id}: TDC {contract['parties']['tdc']} is not allowed by the policy")
    purposes = policy.get("allowed_purposes")
    if purposes is not None and contract["purpose"] not in purposes:
        out.errors.append(f"{dataset_id}: purpose {contract['purpose']} is not allowed by the policy")
    privacy_req = policy.get("privacy") or {}
    cprivacy = contract["privacy"]
    if privacy_req.get("require_dp"):
        if cprivacy.get("mode") != "dp":
            out.errors.append(
                f"{dataset_id}: policy requires differential privacy, contract mode is {cprivacy['mode']}"
            )
        else:
            if "max_epsilon" in privacy_req and cprivacy["epsilon"] > privacy_req["max_epsilon"]:
                out.errors.append(
                    f"{dataset_id}: contract epsilon {cprivacy['epsilon']} > policy max {privacy_req['max_epsilon']}"
                )
            if "max_delta" in privacy_req and cprivacy["delta"] > privacy_req["max_delta"]:
                out.errors.append(
                    f"{dataset_id}: contract delta {cprivacy['delta']} > policy max {privacy_req['max_delta']}"
                )
    mode = contract["output"]["mode"]
    if mode not in policy.get("allowed_output_modes", ["tee-only"]):
        out.errors.append(f"{dataset_id}: output mode {mode} is not allowed by the policy")
    if tier == 3:
        custom = computation.get("custom_ccr") or {}
        if custom.get("measurement_id") not in policy.get("allowed_custom_measurements", []):
            out.errors.append(f"{dataset_id}: custom CCR measurement is not in allowed_custom_measurements")
    if "not_after" in policy:
        policy_end = parse_rfc3339(policy["not_after"])
        if policy_end <= now:
            out.errors.append(f"{dataset_id}: the policy expired at {policy['not_after']}")
        elif parse_rfc3339(contract["validity"]["not_after"]) > policy_end:
            out.warnings.append(f"{dataset_id}: contract outlives the policy (not_after {policy['not_after']})")
    limit = policy.get("max_runs_per_contract")
    if limit is not None and computation["max_runs"] > limit:
        out.warnings.append(
            f"{dataset_id}: contract max_runs {computation['max_runs']} > policy max_runs_per_contract {limit}"
        )
    return out
