locals {
  containers = {
    for id, d in var.datasets : id => coalesce(d.container_name, trim(substr(replace(split("/", id)[1], "/[._]+/", "-"), 0, 63), "-"))
  }
}

module "storage" {
  source = "../federated-storage"

  component = "tdp-storage"
  grants = {
    for id, c in local.containers : "dataset:${id}" => {
      access         = "read"
      container_name = c
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
