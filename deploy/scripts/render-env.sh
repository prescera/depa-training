#!/usr/bin/env bash
# Render depa-env.yaml for the depa CLI from the azure-pilot Terraform outputs.
#
#   render-env.sh [--state-dir deploy/terraform/environments/azure-pilot] \
#                 --parties-json parties.auto.tfvars.json --keys-dir DIR --releases-dir DIR \
#                 [--out depa-env.yaml]
#
# Reads `pilot_platform` (1-platform) and, when applied, `pilot_workloads` (2-workloads) with
# `terraform output -json` (both phases must be initialised against their state), fetches both
# ledger TLS identities from the ACL identity service into certs/ next to the output file, and
# writes {collaborative, parties, storage, outputs, compute, registry, releases_dir}.
# GCP compute (optional) comes from GCP_PROJECT, GCP_ZONE, GCP_SERVICE_ACCOUNT, GCP_SUBNET.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
state_dir="${here}/../terraform/environments/azure-pilot"
args=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --state-dir) state_dir="$2"; shift 2 ;;
    *) args+=("$1"); shift ;;
  esac
done
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT

terraform -chdir="${state_dir}/1-platform" output -json pilot_platform >"${tmp}/platform.json"
workloads=()
if terraform -chdir="${state_dir}/2-workloads" output -json pilot_workloads >"${tmp}/workloads.json" 2>/dev/null \
   && [[ "$(cat "${tmp}/workloads.json")" != "null" ]]; then
  workloads=(--workloads-json "${tmp}/workloads.json")
fi
python3 "${here}/render_env.py" --platform-json "${tmp}/platform.json" "${workloads[@]}" "${args[@]}"

