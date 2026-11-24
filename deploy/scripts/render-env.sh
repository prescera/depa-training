#!/usr/bin/env bash
# Render depa-env.yaml for the depa CLI from the single-subscription deployment's component states.
#
#   render-env.sh --account STATE_ACCOUNT --environment ENV --parties-json FILE \
#                 --keys-dir DIR --releases-dir DIR [--out depa-env.yaml]
#
# Reads the outputs of every component (kms, contract-service, registry, data-provider-tdp-1..4,
# data-consumer, ccr-provider-config) from their state blobs; components not deployed yet are
# skipped. Fetches both ledger TLS identities from the ACL identity service into certs/ next to
# the output file. GCP compute (optional): GCP_PROJECT, GCP_ZONE, GCP_SERVICE_ACCOUNT, GCP_SUBNET.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
account="" env="" args=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --account) account="$2"; shift 2 ;;
    --environment) env="$2"; shift 2 ;;
    *) args+=("$1"); shift ;;
  esac
done
[[ -n "${account}" && -n "${env}" ]] || { echo "render-env.sh: --account and --environment are required" >&2; exit 2; }
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT

components=(kms contract-service registry data-provider-tdp-1 data-provider-tdp-2 data-provider-tdp-3
            data-provider-tdp-4 data-consumer ccr-provider-config)
echo '{}' >"${tmp}/outputs.json"
for c in "${components[@]}"; do
  "${here}/state-outputs.sh" --account "${account}" --environment "${env}" "${c}" >"${tmp}/c.json"
  jq --arg c "${c}" --slurpfile v "${tmp}/c.json" '. + {($c): $v[0]}' "${tmp}/outputs.json" >"${tmp}/o.json"
  mv "${tmp}/o.json" "${tmp}/outputs.json"
done
python3 "${here}/render_env.py" --outputs-json "${tmp}/outputs.json" "${args[@]}"
