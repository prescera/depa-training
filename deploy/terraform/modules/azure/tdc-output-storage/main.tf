locals {
  subject = "output:${var.party_id}"
}

module "storage" {
  source = "../federated-storage"

  component = "tdc-output-storage"
  grants = {
    (local.subject) = {
      access         = "write"
      container_name = var.container_name
    }
  }

  name_prefix                     = var.name_prefix
  resource_group_name             = var.resource_group_name
  location                        = var.location
  storage_account_name            = var.storage_account_name
  account_replication_type        = var.account_replication_type
  allowed_ip_rules                = var.allowed_ip_rules
  blob_delete_retention_days      = var.blob_delete_retention_days
  container_delete_retention_days = var.container_delete_retention_days
  oidc_issuer_url                 = var.oidc_issuer_url
  federation_mode                 = var.federation_mode
  broker                          = var.broker
  tags                            = var.tags
}
