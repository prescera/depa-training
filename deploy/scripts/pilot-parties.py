#!/usr/bin/env python3
"""Derive the azure-pilot `parties` variable from scenario contracts.

  pilot-parties.py [--tdc tdc] [--member dc-member-1] <scenario>... > parties.auto.tfvars.json

Every TDP and dataset named in scenarios/<name>/contract.yaml gets a party entry (datasets
merged across scenarios); the TDC hosts the compute cluster. Needs PyYAML.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_RE = re.compile(r"\$\{[A-Z0-9_]+:-([^}]*)\}")


def load_contract(scenario: str) -> dict:
    path = ROOT / "scenarios" / scenario / "contract.yaml"
    if not path.is_file():
        sys.exit(f"pilot-parties: {path} not found")
    # Templates use ${NAME:-default}; the defaults are the scenario's party/dataset ids.
    return yaml.safe_load(_DEFAULT_RE.sub(r"\1", path.read_text()))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tdc", default="tdc", help="TDC party id (DEPA_TDC); hosts the compute cluster")
    parser.add_argument("--member", default="dc-member-1", help="DC member party id")
    parser.add_argument("scenarios", nargs="+")
    args = parser.parse_args()

    datasets: dict[str, dict[str, dict]] = {}
    for scenario in args.scenarios:
        contract = load_contract(scenario)
        for tdp in contract.get("parties", {}).get("tdps", []):
            datasets.setdefault(tdp, {})
        for ds in contract.get("datasets", []):
            owner = ds["dataset_id"].split("/", 1)[0]
            datasets.setdefault(owner, {})[ds["dataset_id"]] = {}

    parties = [{"party_id": args.member, "roles": ["dc-member"]}]
    parties += [{"party_id": p, "roles": ["tdp"], "datasets": d} for p, d in sorted(datasets.items())]
    parties.append({"party_id": args.tdc, "roles": ["tdc"], "compute": True})
    json.dump({"parties": parties}, sys.stdout, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
