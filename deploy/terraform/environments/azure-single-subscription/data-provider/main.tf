# One data provider (TDP): its resource group, encrypted dataset storage (one `datasets` container
# for every dataset of the provider) and its SAS federation broker (spec §10.1) with the KMS JWKS
# pinned. The KMS issuer and JWKS are inputs (the workflows read them from the kms component). The broker code is deployed after apply with deploy/scripts/deploy-brokers.sh.
module "naming" {
  source = "../modules/naming"

  subscription_id = var.subscription_id
  name_prefix     = var.name_prefix
  tags            = var.tags
}


locals {
  short  = replace(var.party_id, "-", "")
  issuer = var.kms_oidc_issuer_url
}

resource "azurerm_resource_group" "this" {
  name     = module.naming.resource_groups[var.party_id]
  location = var.location
  tags     = merge(module.naming.tags, { party = var.party_id, roles = "tdp" })
}

module "storage" {
  source = "../../../modules/azure/tdp-storage"

  name_prefix          = "d${local.short}-${module.naming.suffix}"
  resource_group_name  = azurerm_resource_group.this.name
  location             = var.location
  storage_account_name = "${local.short}${module.naming.suffix}d"
  oidc_issuer_url      = local.issuer
  datasets             = { "${var.party_id}/*" = { container_name = "datasets" } }
  broker = {
    kms_jwks_json             = var.kms_jwks_json
    host_storage_account_name = "${local.short}${module.naming.suffix}f"
  }
  tags = merge(module.naming.tags, { party = var.party_id })
}

# Demo only: the workflows upload encrypted datasets on the provider's behalf.
resource "azurerm_role_assignment" "operator_dataset_upload" {
  for_each = toset(var.operator_object_ids)

  scope                = module.storage.storage_account_id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = each.value
}
