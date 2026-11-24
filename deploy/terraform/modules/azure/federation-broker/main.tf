locals {
  tags = merge(var.tags, {
    workload   = "depa-training"
    component  = var.component
    managed_by = "terraform"
  })
  audience  = coalesce(var.audience, "api://depa-federation-broker/${var.storage_account_name}")
  workspace = var.log_analytics_workspace_id != null ? var.log_analytics_workspace_id : azurerm_log_analytics_workspace.this[0].id
}

# --- Identities ------------------------------------------------------------------------------

# Signs user-delegation SAS. A user-delegation SAS can never grant more than its signer's RBAC,
# so the data roles below bound every SAS the broker can mint.
resource "azurerm_user_assigned_identity" "signer" {
  name                = "${var.name_prefix}-broker-signer-mi"
  resource_group_name = var.resource_group_name
  location            = var.location
  tags                = local.tags
}

# Function host runtime (AzureWebJobsStorage, deployment package, telemetry). No data access.
resource "azurerm_user_assigned_identity" "host" {
  name                = "${var.name_prefix}-broker-host-mi"
  resource_group_name = var.resource_group_name
  location            = var.location
  tags                = local.tags
}

resource "azurerm_role_assignment" "delegator" {
  scope                = var.storage_account_id
  role_definition_name = "Storage Blob Delegator"
  principal_id         = azurerm_user_assigned_identity.signer.principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_role_assignment" "grant" {
  for_each = var.grants

  scope                = each.value.container_id
  principal_id         = azurerm_user_assigned_identity.signer.principal_id
  principal_type       = "ServicePrincipal"
  role_definition_name = each.value.access == "read" ? "Storage Blob Data Reader" : null
  role_definition_id   = each.value.access == "write" ? var.writer_role_definition_id : null
  description          = "DEPA federation broker ${each.value.access} SAS for ${each.key}"

  lifecycle {
    precondition {
      condition     = each.value.access == "read" || var.writer_role_definition_id != null
      error_message = "writer_role_definition_id is required for write grants."
    }
  }
}

# --- Host storage (deployment package + runtime state), Entra-only -----------------------------

resource "azurerm_storage_account" "host" {
  name                            = var.host_storage_account_name
  resource_group_name             = var.resource_group_name
  location                        = var.location
  account_kind                    = "StorageV2"
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  https_traffic_only_enabled      = true
  min_tls_version                 = "TLS1_2"
  shared_access_key_enabled       = false
  default_to_oauth_authentication = true
  allow_nested_items_to_be_public = false
  local_user_enabled              = false
  tags                            = local.tags
}

resource "azurerm_storage_container" "deployment" {
  name                  = "deployment"
  storage_account_id    = azurerm_storage_account.host.id
  container_access_type = "private"
}

resource "azurerm_role_assignment" "host_storage" {
  scope                = azurerm_storage_account.host.id
  role_definition_name = "Storage Blob Data Owner"
  principal_id         = azurerm_user_assigned_identity.host.principal_id
  principal_type       = "ServicePrincipal"
}

# --- Telemetry: SAS issuance is logged (spec §10.1); ingestion is Entra-authenticated -----------

resource "azurerm_log_analytics_workspace" "this" {
  count = var.log_analytics_workspace_id == null ? 1 : 0

  name                = "${var.name_prefix}-broker-law"
  location            = var.location
  resource_group_name = var.resource_group_name
  sku                 = "PerGB2018"
  retention_in_days   = var.log_retention_days
  tags                = local.tags
}

resource "azurerm_application_insights" "this" {
  name                         = "${var.name_prefix}-broker-ai"
  location                     = var.location
  resource_group_name          = var.resource_group_name
  workspace_id                 = local.workspace
  application_type             = "other"
  local_authentication_enabled = false
  tags                         = local.tags
}

resource "azurerm_role_assignment" "host_telemetry" {
  scope                = azurerm_application_insights.this.id
  role_definition_name = "Monitoring Metrics Publisher"
  principal_id         = azurerm_user_assigned_identity.host.principal_id
  principal_type       = "ServicePrincipal"
}

# --- Function app (Flex Consumption, Python 3.12) ---------------------------------------------

resource "azurerm_service_plan" "this" {
  name                = "${var.name_prefix}-broker-plan"
  resource_group_name = var.resource_group_name
  location            = var.location
  os_type             = "Linux"
  sku_name            = "FC1"
  tags                = local.tags
}

resource "azurerm_function_app_flex_consumption" "this" {
  name                = "${var.name_prefix}-broker"
  resource_group_name = var.resource_group_name
  location            = var.location
  service_plan_id     = azurerm_service_plan.this.id
  tags                = local.tags

  runtime_name           = "python"
  runtime_version        = "3.12"
  maximum_instance_count = var.maximum_instance_count
  instance_memory_in_mb  = 2048

  storage_container_type            = "blobContainer"
  storage_container_endpoint        = "${azurerm_storage_account.host.primary_blob_endpoint}${azurerm_storage_container.deployment.name}"
  storage_authentication_type       = "UserAssignedIdentity"
  storage_user_assigned_identity_id = azurerm_user_assigned_identity.host.id

  https_only                                     = true
  public_network_access_enabled                  = true
  webdeploy_publish_basic_authentication_enabled = false

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.signer.id, azurerm_user_assigned_identity.host.id]
  }

  site_config {
    minimum_tls_version                    = "1.2"
    scm_minimum_tls_version                = "1.2"
    http2_enabled                          = true
    application_insights_connection_string = azurerm_application_insights.this.connection_string
  }

  app_settings = {
    AzureWebJobsStorage__accountName          = azurerm_storage_account.host.name
    AzureWebJobsStorage__credential           = "managedidentity"
    AzureWebJobsStorage__clientId             = azurerm_user_assigned_identity.host.client_id
    APPLICATIONINSIGHTS_AUTHENTICATION_STRING = "ClientId=${azurerm_user_assigned_identity.host.client_id};Authorization=AAD"
    BROKER_ISSUER                             = var.oidc_issuer_url
    BROKER_AUDIENCE                           = local.audience
    BROKER_JWKS                               = jsonencode(jsondecode(var.kms_jwks_json))
    BROKER_JWKS_REFRESH                       = tostring(var.jwks_refresh)
    BROKER_ACCOUNT_NAME                       = var.storage_account_name
    BROKER_BLOB_ENDPOINT                      = var.blob_endpoint
    BROKER_IDENTITY_CLIENT_ID                 = azurerm_user_assigned_identity.signer.client_id
    BROKER_SAS_MAX_TTL_SECONDS                = tostring(var.sas_max_ttl_seconds)
    BROKER_GRANTS = jsonencode({
      for s, g in var.grants : s => { container = g.container, prefix = g.prefix, access = g.access }
    })
  }

  depends_on = [
    azurerm_role_assignment.host_storage,
    azurerm_role_assignment.host_telemetry,
  ]

  lifecycle {
    # Set by deploy/scripts/deploy-brokers.sh to the sha256 of the deployed package.
    ignore_changes = [tags["depa-broker-package-sha256"]]
  }

}
