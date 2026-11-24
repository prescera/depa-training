#!/usr/bin/env bash
# Fail fast unless an Azure region can host the whole pilot:
#   (a) Azure Confidential Ledger      (az provider show -n Microsoft.ConfidentialLedger)
#   (b) Functions Flex Consumption     (az functionapp list-flexconsumption-locations; brokers)
#   (c) AKS virtual nodes v2 with confidential pods (static list below)
#   check-region.sh <location>          e.g. eastus, "East US", centralindia
# Needs `az` logged in to the target subscription.
set -euo pipefail
norm() { tr '[:upper:]' '[:lower:]' | tr -d ' \t"'; }
location="$(norm <<<"${1:?usage: check-region.sh <location>}")"

# Virtual nodes v2 regions supporting confidential pods, from "Region Deployments" in
# https://github.com/microsoft/virtualnodesOnAzureContainerInstances (update with that page).
VN2_CONFIDENTIAL_REGIONS=(
  "Central India" "East Asia" "East US" "East US 2" "Germany West Central" "Italy North"
  "Japan East" "North Europe" "Southeast Asia" "Switzerland North" "UAE North" "West Europe"
  "West US" "West US 2" "South Central US"
)

fail=0
check() {
  local what="$1"; shift
  if printf '%s\n' "$@" | norm | grep -qxF "${location}"; then
    echo "ok    ${what}"
  else
    echo "FAIL  ${what}: ${location} not offered" >&2
    fail=1
  fi
}

# Resource type names are registered with provider-specific casing ("Ledgers"); match any case.
mapfile -t acl < <(az provider show --namespace Microsoft.ConfidentialLedger -o json \
  | jq -r '.resourceTypes[] | select(.resourceType | ascii_downcase == "ledgers") | .locations[]')
if [[ "${#acl[@]}" -eq 0 ]]; then
  echo "ERROR could not read Azure Confidential Ledger locations from the resource provider" >&2
  exit 2
fi
check "Azure Confidential Ledger" "${acl[@]}"

mapfile -t flex < <(az functionapp list-flexconsumption-locations --query "[].name" -o tsv)
if [[ "${#flex[@]}" -eq 0 ]]; then
  echo "ERROR could not read Functions Flex Consumption locations" >&2
  exit 2
fi
check "Functions Flex Consumption" "${flex[@]}"

check "AKS virtual nodes v2 confidential pods" "${VN2_CONFIDENTIAL_REGIONS[@]}"

if [[ "${fail}" -ne 0 ]]; then
  # Regions that pass all three checks, so the operator can pick one.
  mapfile -t candidates < <(comm -12 \
    <(comm -12 <(printf '%s\n' "${acl[@]}" | norm | sort -u) <(printf '%s\n' "${flex[@]}" | norm | sort -u)) \
    <(printf '%s\n' "${VN2_CONFIDENTIAL_REGIONS[@]}" | norm | sort -u))
  if [[ "${#candidates[@]}" -gt 0 ]]; then
    echo "Regions offering all three: ${candidates[*]}" >&2
  else
    echo "No region offers all three for this subscription." >&2
  fi
fi
exit "${fail}"
