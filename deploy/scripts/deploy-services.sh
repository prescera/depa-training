#!/usr/bin/env bash
# Build and install a DC service bundle on its Azure Confidential Ledger (PUT
# /app/userDefinedEndpoints via the service repo's scripts/deploy-acl.sh), after fetching the
# ledger TLS identity from the ACL identity service.
#
#   deploy-services.sh --service kms|cs --ledger NAME --repo DIR [--out-dir DIR]
#
# The caller must be an ACL Administrator of the ledger (the identity that ran Terraform, by
# default) and logged in with `az`. Writes <out-dir>/<service>.pem and <out-dir>/<service>.json
# ({app_url, bundle_digest}).
set -euo pipefail

service="" ledger="" repo="" out_dir="depa-services"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --service) service="$2"; shift 2 ;;
    --ledger) ledger="$2"; shift 2 ;;
    --repo) repo="$(realpath "$2")"; shift 2 ;;
    --out-dir) out_dir="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
[[ "${service}" =~ ^(kms|cs)$ && -n "${ledger}" && -d "${repo}" ]] \
  || { echo "usage: deploy-services.sh --service kms|cs --ledger NAME --repo DIR [--out-dir DIR]" >&2; exit 2; }
mkdir -p "${out_dir}"

token="$(az account get-access-token --resource https://confidential-ledger.azure.com --query accessToken -o tsv)"
curl -sSf "https://identity.confidential-ledger.core.azure.com/ledgerIdentity/${ledger}" \
  | jq -er '.ledgerTlsCertificate' >"${out_dir}/${service}.pem"
echo "==> deploying ${service} bundle to ${ledger}" >&2
ACL_NAME="${ledger}" ACL_TOKEN="${token}" ACL_CACERT="$(realpath "${out_dir}/${service}.pem")" \
  "${repo}/scripts/deploy-acl.sh" | tee "${out_dir}/${service}.json"
