"""Protocol documents in plain language: contracts, governance actions and release policies."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from depa.core.ids import dataset_owner
from depa.core.timeutil import parse_rfc3339, utcnow
from depa.errors import ValidationError

PLATFORMS = {
    "aci-snp": "Azure confidential containers (AMD SEV-SNP)",
    "gcp-cs-tdx": "Google Confidential Space (Intel TDX)",
    "virtual": "Virtual clean room (development only: no hardware protection)",
}
TIERS = {
    1: "Tier 1: declarative recipe of reviewed plugins",
    2: "Tier 2: exported compute graph, checked op by op",
    3: "Tier 3: approved custom clean-room image",
}
PLUGINS = {
    "sql/duckdb": "SQL query over the inputs (DuckDB)",
    "tabular/split": "Prepare features and split into train / validation / test",
    "xgboost/train": "Train an XGBoost model",
    "sklearn/train": "Train a scikit-learn model",
    "torch/train": "Train a PyTorch model",
    "torch/graph-train": "Train an exported PyTorch compute graph",
    "keras/train": "Train a Keras model",
    "hf/sft": "Fine-tune a language model (LoRA)",
    "eval/classification": "Evaluate classification metrics",
    "eval/regression": "Evaluate regression metrics",
    "eval/segmentation": "Evaluate segmentation metrics",
    "custom/entrypoint": "Run the approved custom entrypoint",
}
OUTPUT_MODES = {
    "tdc": "encrypted to the named recipients' registered keys",
    "tee-only": "sealed to a KMS key released only to attested inference clean rooms (never to a party)",
}


def human_bytes(n: object) -> str:
    if not isinstance(n, int | float):
        return "?"
    size = float(n)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024 or unit == "GiB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GiB"


def privacy_text(privacy: Mapping[str, Any]) -> str:
    if privacy.get("mode") == "dp":
        return f"Differential privacy: ε = {privacy.get('epsilon')}, δ = {privacy.get('delta')}"
    return "No differential privacy guarantee"


def _when(value: str, now: datetime) -> str:
    try:
        moment = parse_rfc3339(value)
    except ValidationError:
        return value
    days = (moment - now).total_seconds() / 86400
    if days >= 1:
        return f"{value} (in {days:.0f} days)"
    if days >= 0:
        return f"{value} (within a day)"
    return f"{value} ({-days:.0f} days ago)"


def _step(step: Mapping[str, Any]) -> dict[str, Any]:
    uses = str(step.get("uses", ""))
    config = step.get("with") or {}
    return {
        "id": str(step.get("id", "")),
        "uses": uses,
        "what": PLUGINS.get(uses.split("@", 1)[0], "Plugin step"),
        "query": config.get("query") if isinstance(config, dict) else None,
        "config": json.dumps(config, indent=2, sort_keys=True, default=str),
    }


def contract_summary(record: Mapping[str, Any], party_id: str, now: datetime | None = None) -> dict[str, Any]:
    """Everything a signer should read, as display-ready values."""
    current = now or utcnow()
    c = record["contract"]
    computation, output, validity = c["computation"], c["output"], c["validity"]
    signed = {str(s.get("party")): s for s in record.get("signatures", [])}
    tdc = c["parties"]["tdc"]
    owners = sorted({dataset_owner(d["dataset_id"]) for d in c["datasets"]})
    required = list(record.get("required_signers") or [tdc, *owners])
    return {
        "contract_id": c["contract_id"],
        "title": c.get("title") or c["contract_id"],
        "description": c.get("description", ""),
        "purpose": c.get("purpose", ""),
        "state": record.get("state", ""),
        "digest": record.get("contract_digest", ""),
        "tdc": tdc,
        "tdps": list(c["parties"].get("tdps", owners)),
        "my_role": "consumer (TDC)" if party_id == tdc else ("data provider (TDP)" if party_id in owners else None),
        "signers": [
            {"party": p, "signed": p in signed, "at": signed.get(p, {}).get("at"), "me": p == party_id}
            for p in required
        ],
        "datasets": [
            {**d, "owner": dataset_owner(d["dataset_id"]), "mine": dataset_owner(d["dataset_id"]) == party_id}
            for d in c["datasets"]
        ],
        "tier": TIERS.get(computation.get("tier"), f"Tier {computation.get('tier')}"),
        "platforms": [(p, PLATFORMS.get(p, p)) for p in computation.get("platforms", [])],
        "releases": list(computation.get("ccr_releases") or []),
        "custom_ccr": computation.get("custom_ccr"),
        "recipes": [
            {
                "name": r["document"].get("name", ""),
                "family": r["document"].get("family", ""),
                "digest": r["digest"],
                "steps": [_step(s) for s in r["document"].get("steps", []) if isinstance(s, dict)],
            }
            for r in computation.get("recipes", [])
        ],
        "max_runs": computation.get("max_runs"),
        "runs_used": record.get("runs_used", 0),
        "max_minutes": computation.get("max_duration_minutes"),
        "privacy": privacy_text(c.get("privacy") or {}),
        "output_mode": OUTPUT_MODES.get(output.get("mode"), output.get("mode")),
        "recipients": list(output.get("recipients") or []),
        "formats": list(output.get("allowed_formats") or []),
        "max_bytes": human_bytes(output.get("max_bytes")),
        "metrics": list(output.get("metrics_allowlist") or []),
        "location": f"{output['location'].get('connector')} {output['location'].get('uri')}",
        "not_before": _when(validity["not_before"], current),
        "not_after": _when(validity["not_after"], current),
        "terms": c.get("terms") or {},
        "revocation": record.get("revocation"),
    }


def is_recipient(contract: Mapping[str, Any], party_id: str) -> bool:
    output = contract.get("output") or {}
    return output.get("mode") == "tdc" and any(
        str(r).startswith(f"party:{party_id}#") for r in output.get("recipients", [])
    )


def describe_action(action: Mapping[str, Any]) -> list[tuple[str, str]]:
    """Key facts of a governance action, falling back to its JSON arguments."""
    name = str(action.get("name", ""))
    args = action.get("args") or {}
    if name in ("set_party", "set_member"):
        return [
            ("party", str(args.get("party_id"))),
            ("name", str(args.get("name", ""))),
            ("roles", ", ".join(args.get("roles", []))),
            ("status", str(args.get("status", ""))),
            ("signing keys", ", ".join(str(k.get("kid")) for k in args.get("signing_keys", []))),
        ]
    if name == "approve_ccr_release":
        return [
            ("release", str(args.get("release_id"))),
            ("platform", PLATFORMS.get(args.get("platform"), str(args.get("platform")))),
            ("families", ", ".join(args.get("families", []))),
            ("tiers", ", ".join(str(t) for t in args.get("tiers", []))),
            ("measurements", json.dumps(args.get("measurements", {}), sort_keys=True)),
        ]
    if name == "set_peer_service":
        return [
            ("peer", str(args.get("name"))),
            ("url", str(args.get("url"))),
            ("keys", ", ".join(str(k.get("kid")) for k in args.get("jwks", []))),
        ]
    if name == "set_oidc_settings":
        return [
            ("issuer", str(args.get("issuer_url"))),
            ("token lifetime", f"{args.get('token_ttl_s')} s"),
            ("audiences", ", ".join(args.get("allowed_audiences_global", []))),
        ]
    if isinstance(args, dict) and all(isinstance(v, str | int | float | bool) for v in args.values()):
        return [(str(k), json.dumps(v) if isinstance(v, bool) else str(v)) for k, v in args.items()]
    return [("arguments", json.dumps(args, indent=2, sort_keys=True, default=str))]


def policy_rows(policy: Mapping[str, Any] | None) -> list[tuple[str, str]]:
    """A release policy as labelled rows; omitted fields shown with their protocol meaning."""
    if policy is None:
        return [("policy", "none set: the KMS releases no keys for this dataset")]
    privacy = policy.get("privacy") or {}
    dp = "required" if privacy.get("require_dp") else "not required"
    if privacy.get("require_dp"):
        dp += f" (ε ≤ {privacy.get('max_epsilon', '∞')}, δ ≤ {privacy.get('max_delta', '1')})"
    tdcs = policy.get("allowed_tdcs", [])
    return [
        ("platforms", ", ".join(PLATFORMS.get(p, p) for p in policy.get("allowed_platforms", [])) or "none"),
        ("tiers", ", ".join(str(t) for t in policy.get("allowed_tiers", [1]))),
        ("consumers (TDCs)", "any consumer" if "*" in tdcs else (", ".join(tdcs) or "none")),
        ("purposes", ", ".join(policy["allowed_purposes"]) if "allowed_purposes" in policy else "any purpose"),
        ("differential privacy", dp),
        ("output modes", ", ".join(policy.get("allowed_output_modes", ["tee-only"]))),
        ("storage audiences", ", ".join(policy.get("allowed_audiences", [])) or "none"),
        ("custom measurements", ", ".join(policy.get("allowed_custom_measurements", [])) or "none"),
        ("runs per contract", str(policy.get("max_runs_per_contract", "unlimited"))),
        ("expires", str(policy.get("not_after", "never"))),
    ]
