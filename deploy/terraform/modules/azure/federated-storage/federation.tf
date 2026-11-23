# Trust in the DC KMS OIDC issuer.
#
# federation_mode = "broker" (spec §10.1, default): the TDP-operated broker verifies the ES256
# KMS token and returns a user-delegation SAS bounded by its signer identity's container roles.
#
# federation_mode = "entra" (spec §10): one user-assigned identity per subject with a federated
# credential and a container-scoped data role. Requires RS256 KMS tokens.

locals {
  entra_grants = var.federation_mode == "entra" ? var.grants : {}
}

module "broker" {
  source = "../federation-broker"
  count  = var.federation_mode == "broker" ? 1 : 0

  name_prefix          = var.name_prefix
  component            = var.component
  resource_group_name  = var.resource_group_name
  location             = var.location
  storage_account_id   = azurerm_storage_account.this.id
  storage_account_name = azurerm_storage_account.this.name
  blob_endpoint        = azurerm_storage_account.this.primary_blob_endpoint
  oidc_issuer_url      = var.oidc_issuer_url
  grants = {
    for subject, g in var.grants : subject => {
      access       = g.access
      container    = g.container_name
      container_id = azurerm_storage_container.this[g.container_name].id
    }
  }
  writer_role_definition_id  = local.has_write ? azurerm_role_definition.blob_writer[0].role_definition_resource_id : null
  package_path               = var.broker.package_path
  kms_jwks_json              = var.broker.kms_jwks_json
  host_storage_account_name  = var.broker.host_storage_account_name
  audience                   = var.broker.audience
  jwks_refresh               = var.broker.jwks_refresh
  sas_max_ttl_seconds        = var.broker.sas_max_ttl_seconds
  log_analytics_workspace_id = var.broker.log_analytics_workspace_id
  tags                       = var.tags
}

resource "azurerm_user_assigned_identity" "grant" {
  for_each = local.entra_grants

  name                = local.identity_names[each.key]
  resource_group_name = var.resource_group_name
  location            = var.location
  tags                = merge(local.tags, { depa_access = each.value.access })
}

resource "azurerm_federated_identity_credential" "kms" {
  for_each = local.entra_grants

  name                      = "depa-kms"
  user_assigned_identity_id = azurerm_user_assigned_identity.grant[each.key].id
  issuer                    = var.oidc_issuer_url
  subject                   = each.key
  audience                  = [local.token_exchange_audience]
}

resource "azurerm_role_assignment" "grant" {
  for_each = local.entra_grants

  scope                = azurerm_storage_container.this[each.value.container_name].id
  principal_id         = azurerm_user_assigned_identity.grant[each.key].principal_id
  principal_type       = "ServicePrincipal"
  role_definition_name = each.value.access == "read" ? "Storage Blob Data Reader" : null
  role_definition_id   = each.value.access == "write" ? azurerm_role_definition.blob_writer[0].role_definition_resource_id : null
  description          = "DEPA CCR ${each.value.access} access for ${each.key}"
}
