output "broker_url" {
  description = "Base URL of the broker (the CCR calls POST <broker_url>/sas); descriptor federation.azure.broker_url."
  value       = "https://${azurerm_function_app_flex_consumption.this.default_hostname}"
}

output "audience" {
  description = "Token audience the broker accepts; descriptor federation.azure.audience and Release Policy allowed_audiences."
  value       = local.audience
}

output "signer_principal_id" {
  description = "Object ID of the SAS-signing identity (holds the data roles)."
  value       = azurerm_user_assigned_identity.signer.principal_id
}

output "function_app_name" {
  description = "Function app name."
  value       = azurerm_function_app_flex_consumption.this.name
}

output "application_insights_id" {
  description = "Application Insights resource receiving the SAS issuance log."
  value       = azurerm_application_insights.this.id
}
