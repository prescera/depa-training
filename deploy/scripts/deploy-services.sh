#!/usr/bin/env bash
# Build and deploy the KMS and Contract Service bundles to their ACL instances
# (PUT /app/userDefinedEndpoints via each repo's scripts/deploy-acl.sh) and save the ledger
# TLS identities fetched from the ACL identity service.
#
#   deploy-services.sh --platform-json FILE --kms-repo DIR --cs-repo DIR [--out-dir DIR]
#
# FILE is `terraform -chdir=deploy/terraform/environments/azure-pilot/1-platform output -json
# pilot_platform`. The caller must be an ACL Administrator of both ledgers (the identity that ran
# Terraform, by default) and logged in with `az`. Writes <out-dir>/{kms,cs}.pem and
# <out-dir>/services.json ({kms: {app_url, bundle_digest}, cs: {...}}).
set -euo pipefail

platform_json="" kms_repo="" cs_repo="" out_dir="depa-services"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --platform-json) platform_json="$2"; shift 2 ;;
    --kms-repo) kms_repo="$(realpath "$2")"; shift 2 ;;
    --cs-repo) cs_repo="$(realpath "$2")"; shift 2 ;;
    --out-dir) out_dir="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
[[ -f "${platform_json}" && -d "${kms_repo}" && -d "${cs_repo}" ]] \
  || { echo "usage: deploy-services.sh --platform-json FILE --kms-repo DIR --cs-repo DIR [--out-dir DIR]" >&2; exit 2; }
mkdir -p "${out_dir}"

token="$(az account get-access-token --resource https://confidential-ledger.azure.com --query accessToken -o tsv)"
declare -A results
for svc in kms cs; do
  ledger="$(jq -er ".${svc}.ledger_name" "${platform_json}")"
  repo="$([[ "${svc}" == kms ]] && echo "${kms_repo}" || echo "${cs_repo}")"
  curl -sSf "https://identity.confidential-ledger.core.azure.com/ledgerIdentity/${ledger}" \
    | jq -er '.ledgerTlsCertificate' >"${out_dir}/${svc}.pem"
  echo "==> deploying ${svc} bundle to ${ledger}" >&2
  results[${svc}]="$(ACL_NAME="${ledger}" ACL_TOKEN="${token}" ACL_CACERT="$(realpath "${out_dir}/${svc}.pem")" \
    "${repo}/scripts/deploy-acl.sh")"
done
jq -n --argjson kms "${results[kms]}" --argjson cs "${results[cs]}" '{kms: $kms, cs: $cs}' \
  | tee "${out_dir}/services.json"
