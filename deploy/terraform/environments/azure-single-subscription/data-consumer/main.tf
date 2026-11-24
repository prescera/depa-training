# The data consumer (TDC, party `tdc`): its resource group, dataset storage for what it provides
# itself (the llm-sft base model) and its write-only output storage, each with a SAS federation
# broker (spec §10.1) pinning the KMS JWKS. Broker code: deploy/scripts/deploy-brokers.sh.
module "naming" {
  source = "../modules/naming"

  subscription_id = var.subscription_id
  name_prefix     = var.name_prefix
  tags            = var.tags
}


locals {
  party  = "tdc"
  issuer = var.kms_oidc_issuer_url
}

resource "azurerm_resource_group" "this" {
  name     = module.naming.resource_groups.tdc
  location = var.location
  tags     = merge(module.naming.tags, { party = local.party, roles = "tdc.tdp" })
}

module "datasets" {
  source = "../../../modules/azure/tdp-storage"

  name_prefix          = "d${local.party}-${module.naming.suffix}"
  resource_group_name  = azurerm_resource_group.this.name
  location             = var.location
  storage_account_name = "${local.party}${module.naming.suffix}d"
  oidc_issuer_url      = local.issuer
  datasets             = { "${local.party}/*" = { container_name = "datasets" } }
  broker = {
    kms_jwks_json             = var.kms_jwks_json
    host_storage_account_name = "${local.party}${module.naming.suffix}f"
  }
  tags = merge(module.naming.tags, { party = local.party })
}

module "outputs" {
  source = "../../../modules/azure/tdc-output-storage"

  party_id             = local.party
  name_prefix          = "o${local.party}-${module.naming.suffix}"
  resource_group_name  = azurerm_resource_group.this.name
  location             = var.location
  storage_account_name = "${local.party}${module.naming.suffix}o"
  oidc_issuer_url      = local.issuer
  broker = {
    kms_jwks_json             = var.kms_jwks_json
    host_storage_account_name = "${local.party}${module.naming.suffix}g"
  }
  tags = merge(module.naming.tags, { party = local.party })
}

# Demo only: the workflows upload the consumer's dataset and download the encrypted model.
resource "azurerm_role_assignment" "operator_dataset_upload" {
  for_each = toset(var.operator_object_ids)

  scope                = module.datasets.storage_account_id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = each.value
}

resource "azurerm_role_assignment" "operator_output_read" {
  for_each = toset(var.operator_object_ids)

  scope                = module.outputs.storage_account_id
  role_definition_name = "Storage Blob Data Reader"
  principal_id         = each.value
}
