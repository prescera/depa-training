#!/usr/bin/env python3
"""Render depa-env.yaml (schema: sdk/python/src/depa/env/schema.json) from azure-pilot outputs.
Invoked by render-env.sh; see there for usage."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path
from typing import Any

import yaml

IDENTITY_URL = "https://identity.confidential-ledger.core.azure.com/ledgerIdentity/{}"


def ledger_cert(ledger: str) -> str:
    with urllib.request.urlopen(IDENTITY_URL.format(ledger), timeout=30) as response:  # noqa: S310
        return str(json.load(response)["ledgerTlsCertificate"])


def load(path: str | None) -> Any:
    return json.loads(Path(path).read_text()) if path else None


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--platform-json", required=True)
    p.add_argument("--workloads-json")
    p.add_argument("--parties-json", required=True)
    p.add_argument("--keys-dir", required=True)
    p.add_argument("--releases-dir", required=True)
    p.add_argument("--out", default="depa-env.yaml")
    a = p.parse_args()

    platform = load(a.platform_json)
    workloads = load(a.workloads_json) or {}
    parties = load(a.parties_json)["parties"]
    out = Path(a.out).resolve()
    certs = out.parent / "certs"
    certs.mkdir(parents=True, exist_ok=True)

    collaborative: dict[str, Any] = {}
    for svc in ("kms", "cs"):
        ledger = platform[svc]["ledger_name"]
        pem = certs / f"{svc}.pem"
        pem.write_text(ledger_cert(ledger))
        collaborative[svc] = {
            "url": f"https://{ledger}.confidential-ledger.azure.com/app",
            "service_cert": os.path.relpath(pem, out.parent),
        }
    collaborative["kms"]["oidc_issuer"] = workloads.get(
        "kms_oidc_issuer_url", f"{collaborative['kms']['url']}/oidc"
    )

    storage = {}
    for party, s in (workloads.get("storage") or {}).items():
        wildcard = [d for ds_id, d in s["datasets"].items() if ds_id.endswith("/*")]
        first = wildcard[0] if wildcard else next(iter(s["datasets"].values()))
        storage[party] = {
            "connector": "azure-blob",
            # The SDK places dataset <owner>/<name> at <uri><name>/<version>/. Pilot parties hold
            # all their datasets in one container (owner wildcard grant), so <uri> is that
            # container; otherwise it is the account and each dataset has its own container.
            "uri": first["location"]["uri"] if wildcard else f"https://{s['storage_account_name']}.blob.core.windows.net/",
            "region": first["location"]["region"],
            "federation": first["federation"],
        }

    outputs = {
        party: {
            "connector": o["location"]["connector"],
            "uri": o["location"]["uri"],
            "federation": o["location"]["federation"],
        }
        for party, o in (workloads.get("outputs") or {}).items()
    }

    compute: dict[str, Any] = {}
    if workloads.get("compute"):
        c = workloads["compute"]
        compute["aks"] = {
            "subscription_id": platform["subscription_id"],
            "resource_group": c["resource_group"],
            "cluster_name": c["cluster_name"],
            "namespace": c["namespace"],
        }
    if os.environ.get("GCP_PROJECT"):
        compute["gcp"] = {
            "project": os.environ["GCP_PROJECT"],
            "zone": os.environ["GCP_ZONE"],
            "service_account": os.environ["GCP_SERVICE_ACCOUNT"],
            "subnetwork": os.environ["GCP_SUBNET"],
        }

    env = {
        "collaborative": collaborative,
        "parties": [
            {"party_id": x["party_id"], "roles": x["roles"], "key_dir": str(Path(a.keys_dir).resolve() / x["party_id"])}
            for x in parties
        ],
        "storage": storage,
        "outputs": outputs,
        "compute": compute,
        "registry": {"login_server": platform["registry"]["login_server"]},
        "releases_dir": str(Path(a.releases_dir).resolve()),
    }
    out.write_text(yaml.safe_dump(env, sort_keys=False))
    print(out)


if __name__ == "__main__":
    sys.exit(main())
