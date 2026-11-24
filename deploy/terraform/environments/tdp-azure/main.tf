resource "azurerm_resource_group" "this" {
  name     = var.resource_group_name
  location = var.location
  tags     = merge(var.tags, { workload = "depa-training", party = var.party_id, managed_by = "terraform" })
}

module "tdp_storage" {
  source = "../../modules/azure/tdp-storage"

  name_prefix          = var.name_prefix
  resource_group_name  = azurerm_resource_group.this.name
  location             = azurerm_resource_group.this.location
  storage_account_name = var.storage_account_name
  oidc_issuer_url      = var.oidc_issuer_url
  datasets             = var.datasets
  allowed_ip_rules     = var.allowed_ip_rules
  federation_mode      = var.federation_mode
  broker = var.federation_mode == "broker" ? {
    kms_jwks_json             = var.kms_jwks_json
    host_storage_account_name = var.broker_host_storage_account_name
  } : null
  tags = merge(var.tags, { party = var.party_id })
}
