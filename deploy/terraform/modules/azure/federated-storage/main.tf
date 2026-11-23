locals {
  tags = merge(var.tags, {
    workload   = "depa-training"
    component  = var.component
    managed_by = "terraform"
  })

  containers = toset([for g in values(var.grants) : g.container_name])
  has_write  = anytrue([for g in values(var.grants) : g.access == "write"])

  identity_names = {
    for subject in keys(var.grants) : subject => format(
      "%s-%s",
      substr(trim(replace("${var.name_prefix}-${subject}", "/[^A-Za-z0-9_-]+/", "-"), "-"), 0, 100),
      substr(sha256(subject), 0, 8),
    )
  }

  token_exchange_audience = "api://AzureADTokenExchange"
}

# Entra ID-only data plane: shared keys and SAS are disabled. The azurerm
# provider must set `storage_use_azuread = true`.
resource "azurerm_storage_account" "this" {
  name                              = var.storage_account_name
  resource_group_name               = var.resource_group_name
  location                          = var.location
  account_kind                      = "StorageV2"
  account_tier                      = "Standard"
  account_replication_type          = var.account_replication_type
  access_tier                       = "Hot"
  https_traffic_only_enabled        = true
  min_tls_version                   = "TLS1_2"
  shared_access_key_enabled         = false
  default_to_oauth_authentication   = true
  allow_nested_items_to_be_public   = false
  cross_tenant_replication_enabled  = false
  infrastructure_encryption_enabled = true
  local_user_enabled                = false
  sftp_enabled                      = false
  allowed_copy_scope                = "AAD"
  public_network_access_enabled     = true
  tags                              = local.tags

  blob_properties {
    versioning_enabled = true

    delete_retention_policy {
      days = var.blob_delete_retention_days
    }

    container_delete_retention_policy {
      days = var.container_delete_retention_days
    }
  }

  dynamic "network_rules" {
    for_each = length(var.allowed_ip_rules) > 0 ? [1] : []
    content {
      default_action = "Deny"
      bypass         = ["AzureServices"]
      ip_rules       = var.allowed_ip_rules
    }
  }
}

resource "azurerm_storage_container" "this" {
  for_each = local.containers

  name                  = each.key
  storage_account_id    = azurerm_storage_account.this.id
  container_access_type = "private"
}

# Write-only data role for CCR outputs: create/append blobs, no read, list or
# delete. Blob versioning keeps overwritten versions recoverable by the owner.
resource "azurerm_role_definition" "blob_writer" {
  count = local.has_write ? 1 : 0

  name        = "DEPA CCR output writer (${var.storage_account_name})"
  scope       = azurerm_storage_account.this.id
  description = "Create blobs only (DEPA Training CCR outputs). No read, list or delete."

  permissions {
    actions = []
    data_actions = [
      "Microsoft.Storage/storageAccounts/blobServices/containers/blobs/write",
      "Microsoft.Storage/storageAccounts/blobServices/containers/blobs/add/action",
    ]
  }

  assignable_scopes = [azurerm_storage_account.this.id]
}
