output "storage_account_id" {
  description = "Resource ID of the storage account."
  value       = azurerm_storage_account.this.id
}

output "storage_account_name" {
  description = "Name of the storage account."
  value       = azurerm_storage_account.this.name
}

output "audience" {
  description = "Token audience CCRs must request from the KMS (broker audience, or api://AzureADTokenExchange in entra mode); list it in the dataset Release Policy `allowed_audiences` / KMS `allowed_audiences_global`."
  value       = var.federation_mode == "broker" ? module.broker[0].audience : local.token_exchange_audience
}

output "broker_url" {
  description = "Federation broker base URL (broker mode), else null."
  value       = var.federation_mode == "broker" ? module.broker[0].broker_url : null
}

output "grants" {
  description = "Per OIDC subject: container URI and the descriptor `federation.azure` block ({broker_url, audience} in broker mode, {tenant_id, client_id} in entra mode)."
  value = {
    for subject, g in var.grants : subject => {
      access    = g.access
      container = g.container_name
      uri       = "${azurerm_storage_account.this.primary_blob_endpoint}${g.container_name}/"
      # map(string) without nulls: {broker_url, audience} (broker) or {tenant_id, client_id} (entra)
      federation_azure = {
        for k, v in {
          broker_url = var.federation_mode == "broker" ? module.broker[0].broker_url : null
          audience   = var.federation_mode == "broker" ? module.broker[0].audience : null
          tenant_id  = var.federation_mode == "entra" ? azurerm_user_assigned_identity.grant[subject].tenant_id : null
          client_id  = var.federation_mode == "entra" ? azurerm_user_assigned_identity.grant[subject].client_id : null
        } : k => v if v != null
      }
    }
  }
}
