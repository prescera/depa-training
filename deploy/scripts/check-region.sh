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

mapfile -t acl < <(az provider show --namespace Microsoft.ConfidentialLedger \
  --query "resourceTypes[?resourceType=='ledgers'].locations[]" -o tsv)
check "Azure Confidential Ledger" "${acl[@]}"

mapfile -t flex < <(az functionapp list-flexconsumption-locations --query "[].name" -o tsv)
check "Functions Flex Consumption" "${flex[@]}"

check "AKS virtual nodes v2 confidential pods" "${VN2_CONFIDENTIAL_REGIONS[@]}"
exit "${fail}"
