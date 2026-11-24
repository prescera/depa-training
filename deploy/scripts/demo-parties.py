#!/usr/bin/env python3
"""Print the fixed demo parties of the single-subscription Azure deployment as Terraform JSON variables ({"parties": [...]}).

  demo-parties.py > parties.auto.tfvars.json

Any scenario runs on these slots: the SDK maps a scenario's TDC onto `tdc` and its other
dataset owners (sorted by id) onto tdp-1..tdp-4 (four is the most providers in any scenario).
`tdc` is also a TDP because it owns a dataset in some scenarios (the llm-sft base model).
Every TDP gets one `datasets` container readable for any `dataset:<party>/*` token through its
federation broker. Keep in sync with PARTIES in party-keys.sh.
"""

from __future__ import annotations

import json
import sys

TDP_SLOTS = 4
DATASETS_CONTAINER = "datasets"


def tdp(party_id: str, roles: list[str], **extra: object) -> dict[str, object]:
    return {
        "party_id": party_id,
        "roles": roles,
        "datasets": {f"{party_id}/*": {"container_name": DATASETS_CONTAINER}},
        **extra,
    }


def main() -> None:
    parties = [
        {"party_id": "dc-member-1", "roles": ["dc-member"]},
        tdp("tdc", ["tdc", "tdp"], compute=True),
        *(tdp(f"tdp-{i}", ["tdp"]) for i in range(1, TDP_SLOTS + 1)),
    ]
    json.dump({"parties": parties}, sys.stdout, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
