locals {
  suffix  = random_string.suffix.result
  parties = { for p in var.parties : p.party_id => p }
  compute = one([for p in var.parties : p.party_id if p.compute])
  tags = merge(var.tags, {
    workload   = "depa-training"
    deployment = "azure-single-subscription-${var.name_prefix}"
    managed_by = "terraform"
  })
}

# Keeps globally unique names (ledgers, storage accounts, registry, function apps) unique.
resource "random_string" "suffix" {
  length  = 5
  upper   = false
  special = false
}

resource "azurerm_resource_group" "party" {
  for_each = local.parties

  name     = "${var.name_prefix}-${each.key}-rg"
  location = var.location
  tags     = merge(local.tags, { party = each.key, roles = join(".", sort(each.value.roles)) })
}

resource "azurerm_resource_group" "shared" {
  name     = "${var.name_prefix}-shared-rg"
  location = var.location
  tags     = merge(local.tags, { party = "dc" })
}

resource "azurerm_log_analytics_workspace" "this" {
  name                = "${var.name_prefix}-${local.suffix}-law"
  location            = var.location
  resource_group_name = azurerm_resource_group.shared.name
  sku                 = "PerGB2018"
  retention_in_days   = 30
  tags                = local.tags
}

# --- Governance plane: KMS and Contract Service, each on its own ACL instance --------------------
# Module sources are literals tracking `main`. The GitHub workflows check out both service repos
# (same GitHub owner, DEPA_SERVICES_REF) and render local_override.tf.tpl to use those checkouts.
module "kms" {
  source = "git::https://github.com/prescera/depa-training-kms-azure.git//deploy/terraform/stacks/1-core?ref=main"

  name_prefix                        = "${var.name_prefix}-kms"
  environment                        = local.suffix
  region                             = var.location
  region_short                       = var.region_short
  tenant_id                          = var.tenant_id
  resource_group_name                = "${var.name_prefix}-dc-kms-rg"
  gateway_hostname                   = coalesce(var.kms_gateway_hostname, "kms.${var.name_prefix}.internal")
  key_vault_allowed_ip_ranges        = var.operator_ip_ranges
  key_vault_network_default_action   = var.key_vault_network_default_action
  ledger_administrator_principal_ids = var.ledger_administrator_principal_ids
  tags                               = merge(local.tags, { party = "dc" })
}

module "contract_service" {
  source = "git::https://github.com/prescera/contract-service.git//deploy/terraform/stacks/1-core?ref=main"

  name_prefix                        = "${var.name_prefix}-cs"
  environment                        = local.suffix
  region                             = var.location
  region_short                       = var.region_short
  tenant_id                          = var.tenant_id
  resource_group_name                = "${var.name_prefix}-dc-cs-rg"
  enable_gateway                     = false
  key_vault_allowed_ip_ranges        = var.operator_ip_ranges
  key_vault_network_default_action   = var.key_vault_network_default_action
  ledger_administrator_principal_ids = var.ledger_administrator_principal_ids
  tags                               = merge(local.tags, { party = "dc" })
}

# --- Registry for CCR release images ------------------------------------------------------------
resource "azurerm_container_registry" "this" {
  name                          = "${var.name_prefix}${local.suffix}acr"
  resource_group_name           = azurerm_resource_group.shared.name
  location                      = var.location
  sku                           = "Standard"
  admin_enabled                 = false
  anonymous_pull_enabled        = false
  public_network_access_enabled = true
  tags                          = local.tags
}

resource "azurerm_role_assignment" "acr_push" {
  for_each = toset(var.registry_pusher_object_ids)

  scope                = azurerm_container_registry.this.id
  role_definition_name = "AcrPush"
  principal_id         = each.value
}

# --- Compute plane: the TDC's AKS + virtual nodes v2 (confidential ACI) --------------------------
module "compute" {
  source = "../../../modules/azure/ccr-aks-vn2"
  count  = local.compute == null ? 0 : 1

  name_prefix                     = trim(substr("${var.name_prefix}-${local.compute}", 0, 24), "-")
  location                        = var.location
  create_resource_group           = false
  resource_group_name             = azurerm_resource_group.party[local.compute].name
  admin_group_object_ids          = var.aks_admin_group_object_ids
  job_submitters                  = [for id in var.job_submitter_object_ids : { object_id = id, kind = "User" }]
  api_server_authorized_ip_ranges = var.aks_api_authorized_ip_ranges
  acr_id                          = azurerm_container_registry.this.id
  enable_acr_pull                 = true
  log_analytics_workspace_id      = azurerm_log_analytics_workspace.this.id
  enable_diagnostics              = true
  # Demo simplification: the KMS/CS ledgers have no service tag, so clean rooms may reach any
  # Azure endpoint in the region on 443 (defence in depth only; see the module README).
  egress_https_service_tags = [
    "AzureActiveDirectory",
    "Storage",
    "AzureContainerRegistry",
    "MicrosoftContainerRegistry",
    "AzureFrontDoor.FirstParty",
    "AzureCloud.${var.location}",
  ]
  tags = merge(local.tags, { party = local.compute })
}
