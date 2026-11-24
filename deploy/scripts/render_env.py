#!/usr/bin/env python3
"""Render depa-env.yaml (schema: sdk/python/src/depa/env/schema.json) from the component outputs of the single-subscription Azure deployment.
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


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--outputs-json", required=True, help="{component: outputs} (render-env.sh)")
    p.add_argument("--parties-json", required=True)
    p.add_argument("--keys-dir", required=True)
    p.add_argument("--releases-dir", required=True)
    p.add_argument("--out", default="depa-env.yaml")
    a = p.parse_args()

    components: dict[str, dict[str, Any]] = json.loads(Path(a.outputs_json).read_text())
    parties = json.loads(Path(a.parties_json).read_text())["parties"]
    out = Path(a.out).resolve()
    certs = out.parent / "certs"
    certs.mkdir(parents=True, exist_ok=True)

    collaborative: dict[str, Any] = {}
    for svc, component in (("kms", "kms"), ("cs", "contract-service")):
        ledger = (components.get(component) or {}).get("ledger_name")
        if not ledger:
            sys.exit(f"render_env: the {component} component is not deployed")
        pem = certs / f"{svc}.pem"
        pem.write_text(ledger_cert(ledger))
        collaborative[svc] = {
            "url": f"https://{ledger}.confidential-ledger.azure.com/app",
            "service_cert": os.path.relpath(pem, out.parent),
        }
    collaborative["kms"]["oidc_issuer"] = components["kms"].get(
        "oidc_issuer_url", f"{collaborative['kms']['url']}/oidc"
    )

    # Providers: data-provider-tdp-<n> and the consumer's own datasets (data-consumer.storage).
    provider_storage = {
        c["party_id"]: c["storage"] for name, c in components.items() if name.startswith("data-provider-") and c
    }
    consumer = components.get("data-consumer") or {}
    if consumer.get("storage"):
        provider_storage["tdc"] = consumer["storage"]
    storage = {}
    for party, s in provider_storage.items():
        wildcard = [d for ds_id, d in s["datasets"].items() if ds_id.endswith("/*")]
        first = wildcard[0] if wildcard else next(iter(s["datasets"].values()))
        storage[party] = {
            "connector": "azure-blob",
            # The SDK places dataset <owner>/<name> at <uri><name>/<version>/. Demo parties hold
            # all their datasets in one container (owner wildcard grant), so <uri> is that
            # container; otherwise it is the account and each dataset has its own container.
            "uri": first["location"]["uri"] if wildcard else f"https://{s['storage_account_name']}.blob.core.windows.net/",
            "region": first["location"]["region"],
            "federation": first["federation"],
        }

    outputs = {}
    if consumer.get("output_storage"):
        location = consumer["output_storage"]["location"]
        outputs["tdc"] = {"connector": location["connector"], "uri": location["uri"], "federation": location["federation"]}

    compute: dict[str, Any] = {}
    if (components.get("ccr-provider-config") or {}).get("compute"):
        compute["aks"] = components["ccr-provider-config"]["compute"]
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
        **({"registry": {"login_server": components["registry"]["login_server"]}} if (components.get("registry") or {}).get("login_server") else {}),
        "releases_dir": str(Path(a.releases_dir).resolve()),
    }
    out.write_text(yaml.safe_dump(env, sort_keys=False))
    print(out)


if __name__ == "__main__":
    sys.exit(main())
