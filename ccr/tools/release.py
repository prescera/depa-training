#!/usr/bin/env python3
"""Helpers for ccr/tools/build-release.sh (stdlib only).

  release.py policy-input --template T --image REF --variant prod|debug [--trust-bundle FILE]
      Render the `az confcom acipolicygen -i` input for one CCR image.
  release.py manifest --release-id ID --platform P --family F --image REPO@sha256:... [...]
      Write the release manifest {release: <spec §6 record>, deployment: {...}}.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import NoReturn

ENV_ALLOWLIST = [
    "DEPA_CONTRACT_ID",
    "DEPA_RECIPE_DIGEST",
    "DEPA_CS_URL",
    "DEPA_KMS_URL",
    "DEPA_PLATFORM",
    "DEPA_TRUST_BUNDLE",
    "DEPA_LOG_LEVEL",
]
LOG_LEVEL_RE = {"prod": "(info|warning|error)", "debug": "(debug|info|warning|error)"}
IMAGE_RE = re.compile(r"^[a-z0-9][a-z0-9._/:-]*@(sha256:[0-9a-f]{64})$")
RELEASE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,62}$")
PRINTABLE_RE = re.compile(r"^[\x20-\x7e]+$")
# Confidential ACI trust anchors, from
# https://github.com/microsoft/confidential-aci-examples/blob/main/docs/Confidential_ACI_SCHEME.md
# UVM endorsements (COSE_Sign1): production SVNs start at 100 (lower values must not be
# trusted); the current SVN as of 2026-08-04 is 104, the default minimum here.
UVM_DID = "did:x509:0:sha256:I__iuL25oXEVFdTP_aBLx_eT1RPHbCQ_ECBQfYZpt9s::eku:1.3.6.1.4.1.311.76.59.1.2"
UVM_FEED = "ContainerPlat-AMD-UVM"
UVM_MIN_SVN = "104"
# The CCE policy must only admit Microsoft's signed ACI infrastructure fragment.
INFRA_FRAGMENT = {
    "feed": "mcr.microsoft.com/aci/aci-cc-infra-fragment",
    "issuer": "did:x509:0:sha256:I__iuL25oXEVFdTP_aBLx_eT1RPHbCQ_ECBQfYZpt9s::eku:1.3.6.1.4.1.311.76.59.1.3",
}
INFRA_MIN_SVN = 4


def number(text: str) -> int | float:
    value = float(text)
    if not value > 0:
        raise argparse.ArgumentTypeError("must be a positive number")
    return int(value) if value.is_integer() else value


def fail(msg: str) -> NoReturn:
    sys.exit(f"release.py: {msg}")


def policy_input(args: argparse.Namespace) -> None:
    doc = json.loads(Path(args.template).read_text())
    containers = doc.get("containers", [])
    if len(containers) != 1 or containers[0].get("name") != "ccr":
        fail("template must declare exactly one container named 'ccr'")
    props = containers[0]["properties"]
    props["image"] = args.image
    for rule in props["environmentVariables"]:
        if rule["name"] == "DEPA_LOG_LEVEL":
            rule["value"] = LOG_LEVEL_RE[args.variant]
        elif rule["name"] == "DEPA_TRUST_BUNDLE" and args.trust_bundle:
            # A fixed bundle is measured into the policy (spec §12): pinned services, no regex.
            bundle = Path(args.trust_bundle).read_text().strip()
            if not PRINTABLE_RE.fullmatch(bundle) or len(bundle) > 32 * 1024:
                fail("trust bundle must be <= 32 KiB of printable ASCII on one line")
            rule.pop("regex", None)
            rule["value"] = bundle
    names = sorted(r["name"] for r in props["environmentVariables"])
    if names != sorted(ENV_ALLOWLIST):
        fail(f"template env rules {names} differ from the runtime contract {sorted(ENV_ALLOWLIST)}")
    json.dump(doc, sys.stdout, indent=2)
    sys.stdout.write("\n")


def check_policy(args: argparse.Namespace) -> None:
    """The generated policy must admit exactly the Microsoft infrastructure fragment, SVN >= min."""
    rego = Path(args.policy).read_text()
    match = re.search(r"^fragments := (\[.*?^\])$", rego, re.M | re.S)
    if match is None:
        fail("policy has no fragments list")
    fragments = json.loads(match.group(1))
    if len(fragments) != 1:
        fail(f"expected exactly one fragment, found {len(fragments)}")
    frag = fragments[0]
    if frag.get("feed") != INFRA_FRAGMENT["feed"] or frag.get("issuer") != INFRA_FRAGMENT["issuer"]:
        fail(f"unexpected fragment {frag.get('feed')!r} / {frag.get('issuer')!r}")
    if not str(frag.get("minimum_svn", "")).isdigit() or int(frag["minimum_svn"]) < args.min_svn:
        fail(f"fragment minimum_svn {frag.get('minimum_svn')!r} < {args.min_svn}")
    print(f"policy fragment ok: {frag['feed']} svn>={frag['minimum_svn']}")


def manifest(args: argparse.Namespace) -> None:
    if not RELEASE_ID_RE.fullmatch(args.release_id):
        fail(f"invalid release_id {args.release_id!r}")
    match = IMAGE_RE.fullmatch(args.image)
    if match is None:
        fail("image must be digest-pinned (<repo>@sha256:<64 hex>)")
    digest = match.group(1)
    tiers = sorted({int(t) for t in args.tiers.split(",")})
    if not tiers or any(t not in (1, 2) for t in tiers):
        fail("tiers must be a subset of 1,2")

    deployment: dict[str, object] = {"image": args.image}
    if args.platform == "aci-snp":
        if not args.cce_policy_b64_file:
            fail("aci-snp releases need --cce-policy-b64-file")
        policy_b64 = Path(args.cce_policy_b64_file).read_text().strip()
        host_data = hashlib.sha256(base64.b64decode(policy_b64, validate=True)).hexdigest()
        measurements: dict[str, object] = {
            "host_data": [host_data],
            "uvm": {"did": UVM_DID, "feed": UVM_FEED, "min_svn": args.uvm_min_svn},
        }
        deployment["cce_policy_b64"] = policy_b64
    elif args.platform == "gcp-cs-tdx":
        measurements = {
            "image_digests": [digest],
            "support_attributes": [] if args.debug else ["STABLE"],
            "require_debug_disabled": not args.debug,
        }
    else:
        measurements = {"virtual_ids": [args.virtual_id]}

    deployment["resources"] = {"cpu": args.cpu, "memory_gb": args.memory_gb}
    deployment["env_allowlist"] = ENV_ALLOWLIST

    provenance = {"source": args.source, "commit": args.commit, "image": args.image}
    if args.build_url:
        provenance["build"] = args.build_url
    record = {
        "release_id": args.release_id,
        "platform": args.platform,
        "families": [args.family],
        "tiers": tiers,
        "measurements": measurements,
        "provenance": provenance,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"release": record, "deployment": deployment}, indent=2) + "\n")
    print(out)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("policy-input")
    p.add_argument("--template", required=True)
    p.add_argument("--image", required=True)
    p.add_argument("--variant", choices=sorted(LOG_LEVEL_RE), required=True)
    p.add_argument("--trust-bundle")
    p.set_defaults(func=policy_input)

    c = sub.add_parser("check-policy")
    c.add_argument("--policy", required=True, help="decoded CCE policy (rego)")
    c.add_argument("--min-svn", type=int, default=INFRA_MIN_SVN)
    c.set_defaults(func=check_policy)

    m = sub.add_parser("manifest")
    m.add_argument("--release-id", required=True)
    m.add_argument("--platform", choices=["aci-snp", "gcp-cs-tdx", "virtual"], required=True)
    m.add_argument("--family", choices=["classic", "torch", "llm"], required=True)
    m.add_argument("--image", required=True)
    m.add_argument("--tiers", default="1,2")
    m.add_argument("--cce-policy-b64-file")
    m.add_argument("--uvm-min-svn", default=UVM_MIN_SVN)
    m.add_argument("--virtual-id", default="virtual-dev")
    m.add_argument("--debug", action="store_true")
    m.add_argument("--cpu", type=number, required=True)
    m.add_argument("--memory-gb", type=number, required=True)
    m.add_argument("--source", required=True)
    m.add_argument("--commit", required=True)
    m.add_argument("--build-url", default="")
    m.add_argument("--out", required=True)
    m.set_defaults(func=manifest)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
