#!/usr/bin/env bash
# Deploy the federation broker package (modules/azure/federation-broker/build-package.sh) to
# Flex Consumption function apps, authenticated with Entra ID.
#
# Basic publishing authentication stays disabled on the apps, so Terraform cannot deploy the
# code (its zip deploy uses basic-auth publishing credentials). This script uses the same
# one-deploy endpoint as the Azure CLI's Flex path, with an Entra token for App Service, but
# with RemoteBuild=false: `az functionapp deployment source config-zip` forces a remote build
# for Python apps, which would replace the hash-locked package with a server-side build.
#
# Idempotent: the sha256 of the deployed package is recorded in the app's
# depa-broker-package-sha256 tag (ignored by Terraform) and an unchanged package is skipped.
#
#   deploy-brokers.sh --package ZIP --from-json BROKERS.json   # [{resource_group_name, function_app_name}]
#   deploy-brokers.sh --package ZIP --resource-group RG --name APP
#
# Needs `az` logged in with permission to deploy to the apps (e.g. Website Contributor).
set -euo pipefail

TAG=depa-broker-package-sha256
log() { echo "[deploy-brokers] $*" >&2; }
die() { log "error: $*"; exit 1; }

package="" from_json="" rg="" name=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --package) package="$2"; shift 2 ;;
    --from-json) from_json="$2"; shift 2 ;;
    --resource-group) rg="$2"; shift 2 ;;
    --name) name="$2"; shift 2 ;;
    *) die "unknown argument $1" ;;
  esac
done
[[ -f "${package}" ]] || die "--package must name the broker zip (build-package.sh)"
if [[ -n "${from_json}" ]]; then
  mapfile -t apps < <(jq -r '.[] | "\(.resource_group_name) \(.function_app_name)"' "${from_json}")
elif [[ -n "${rg}" && -n "${name}" ]]; then
  apps=("${rg} ${name}")
else
  die "pass --from-json or --resource-group and --name"
fi
[[ "${#apps[@]}" -gt 0 ]] || die "no broker apps to deploy"

sha="$(sha256sum "${package}" | cut -d' ' -f1)"
token="$(az account get-access-token --resource https://appservice.azure.com --query accessToken -o tsv)"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT

deploy_one() {
  local rg="$1" app="$2" id scm status code
  id="$(az functionapp show --resource-group "${rg}" --name "${app}" --query id -o tsv)"
  if [[ "$(az resource show --ids "${id}" --query "tags.\"${TAG}\"" -o tsv 2>/dev/null)" == "${sha}" ]]; then
    log "${app}: package ${sha:0:12} already deployed"
    return 0
  fi
  # The SCM (deployment) host, as the Azure CLI resolves it: the site's Repository-type host
  # name, read from ARM and matched case-insensitively; otherwise derived from the default host
  # name (<app>[-<hash>].<rest> -> <app>[-<hash>].scm.<rest>, also for regional host names).
  az rest --method get --url "https://management.azure.com${id}?api-version=2024-04-01" \
    --output json >"${tmp}/site.json"
  scm="$(jq -r '[.properties.hostNameSslStates[]? | select((.hostType // "") | ascii_downcase == "repository") | .name][0] // empty' "${tmp}/site.json")"
  if [[ -z "${scm}" ]]; then
    local host
    host="$(jq -r '.properties.defaultHostName // empty' "${tmp}/site.json")"
    [[ "${host}" == *.* ]] || die "${app}: no SCM host and no default host name"
    scm="${host%%.*}.scm.${host#*.}"
  fi
  log "${app}: SCM host ${scm}"

  # Retry transient failures of the deployment service (5xx, 429, and the 400 it returns when
  # its own upload to the deployment storage account times out: BlobUploadFailedException).
  local attempt
  for attempt in 1 2 3 4; do
    log "${app}: deploying package ${sha:0:12} (attempt ${attempt})"
    code="$(curl -sS -o "${tmp}/resp" -w '%{http_code}' -X POST \
      "https://${scm}/api/publish?RemoteBuild=false&Deployer=depa" \
      -H "Authorization: Bearer ${token}" -H "Content-Type: application/zip" \
      --data-binary "@${package}" || true)"
    [[ "${code}" == 200 || "${code}" == 202 ]] && break
    cat "${tmp}/resp" >&2 || true
    if [[ "${attempt}" -lt 4 ]] && { [[ "${code}" == 000 || "${code}" == 429 || "${code}" == 5* ]] \
        || grep -q 'BlobUploadFailed' "${tmp}/resp" 2>/dev/null; }; then
      log "${app}: transient publish failure (HTTP ${code}); retrying in $((attempt * 20)) s"
      sleep $((attempt * 20))
      continue
    fi
    die "${app}: publish returned HTTP ${code}"
  done

  # Deployment status: 4 success, 3 failed, -1 cancelled, 5 superseded, 6 partially successful.
  for _ in $(seq 1 180); do
    sleep 5
    code="$(curl -sS -o "${tmp}/status" -w '%{http_code}' \
      "https://${scm}/api/deployments/latest" -H "Authorization: Bearer ${token}" || true)"
    [[ "${code}" == 200 || "${code}" == 202 ]] || continue
    status="$(jq -r '.status // empty' "${tmp}/status" 2>/dev/null || true)"
    case "${status}" in
      4)
        az resource tag --ids "${id}" --is-incremental --tags "${TAG}=${sha}" --output none
        log "${app}: deployed"
        return 0
        ;;
      3 | -1 | 5 | 6)
        jq . "${tmp}/status" >&2 || true
        die "${app}: deployment ended with status ${status}"
        ;;
    esac
  done
  die "${app}: deployment did not finish within 15 minutes"
}

for entry in "${apps[@]}"; do
  read -r app_rg app_name <<<"${entry}"
  deploy_one "${app_rg}" "${app_name}"
done
