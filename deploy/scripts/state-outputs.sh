#!/usr/bin/env bash
# Print the outputs of one component root of the single-subscription deployment as a JSON object
# ({name: value}), read straight from its Terraform state blob (no `terraform init` needed).
#   state-outputs.sh --account STATE_ACCOUNT --environment ENV COMPONENT
# COMPONENT: kms | contract-service | registry | data-provider-tdp-<n> | data-consumer |
#            ccr-provider-cluster | ccr-provider-config. Prints {} when the component has no state.
# Needs `az` logged in with Storage Blob Data Reader (or Contributor) on the state container.
set -euo pipefail
account="" env="" container="tfstate"
while [[ $# -gt 1 ]]; do
  case "$1" in
    --account) account="$2"; shift 2 ;;
    --environment) env="$2"; shift 2 ;;
    --container) container="$2"; shift 2 ;;
    *) break ;;
  esac
done
component="${1:-}"
[[ -n "${account}" && -n "${env}" && -n "${component}" ]] \
  || { echo "usage: state-outputs.sh --account STATE_ACCOUNT --environment ENV COMPONENT" >&2; exit 2; }
key="depa-training/${env}/${component}.tfstate"
exists="$(az storage blob exists --auth-mode login --account-name "${account}" --container-name "${container}" \
  --name "${key}" --query exists -o tsv)"
if [[ "${exists}" != "true" ]]; then
  echo '{}'
  exit 0
fi
az storage blob download --auth-mode login --account-name "${account}" --container-name "${container}" \
  --name "${key}" --file /dev/stdout --no-progress --only-show-errors \
  | jq -c '(.outputs // {}) | map_values(.value)'
