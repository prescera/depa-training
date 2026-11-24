output "ledger_name" {
  description = "Ledger name (https://<name>.confidential-ledger.azure.com)."
  value       = module.kms.ledger_name
}

output "ledger_endpoint" {
  description = "Ledger endpoint; the app base URL is <endpoint>/app."
  value       = module.kms.ledger_endpoint
}

output "identity_service_endpoint" {
  description = "ACL identity service endpoint (ledger TLS identity)."
  value       = module.kms.identity_service_endpoint
}

output "resource_group_name" {
  description = "Resource group of the service."
  value       = module.kms.resource_group_name
}

output "key_vault_name" {
  description = "Key Vault holding the ledger member certificate."
  value       = module.kms.key_vault_name
}

output "oidc_issuer_url" {
  description = "KMS OIDC issuer (set_oidc_settings.issuer_url): the ledger app endpoint + /oidc."
  value       = "${trimsuffix(module.kms.ledger_endpoint, "/")}/app/oidc"
}

output "gateway_stack_inputs" {
  description = "Input for environments/dc/2-gateway if a publicly trusted KMS issuer is added later (AWS/GCP storage)."
  value       = module.kms.gateway_stack_inputs
}
