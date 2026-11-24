output "ledger_name" {
  description = "Ledger name (https://<name>.confidential-ledger.azure.com)."
  value       = module.contract_service.ledger_name
}

output "ledger_endpoint" {
  description = "Ledger endpoint; the app base URL is <endpoint>/app."
  value       = module.contract_service.ledger_endpoint
}

output "identity_service_endpoint" {
  description = "ACL identity service endpoint (ledger TLS identity)."
  value       = module.contract_service.identity_service_endpoint
}

output "resource_group_name" {
  description = "Resource group of the service."
  value       = module.contract_service.resource_group_name
}

output "key_vault_name" {
  description = "Key Vault holding the ledger member certificate."
  value       = module.contract_service.key_vault_name
}
