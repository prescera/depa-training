output "kms_ledger_name" {
  description = "KMS Confidential Ledger name (Private Link Service step)."
  value       = module.kms.ledger_name
}

output "kms_ledger_endpoint" {
  description = "KMS ledger endpoint (DEPA_KMS_URL when not using the gateway; bundle deployment)."
  value       = module.kms.ledger_endpoint
}

output "kms_ledger_tls_certificate_pem" {
  description = "KMS ledger TLS certificate (pin in DEPA_TRUST_BUNDLE / CLI)."
  value       = module.kms.ledger_tls_certificate_pem
}

output "kms_key_vault_name" {
  description = "KMS Key Vault name."
  value       = module.kms.key_vault_name
}

output "kms_member_certificate_name" {
  description = "KMS ledger member certificate name in Key Vault."
  value       = module.kms.member_certificate_name
}

output "kms_ops_identity_client_id" {
  description = "AZURE_CLIENT_ID of the KMS certificate renewal workflow."
  value       = module.kms.ops_identity_client_id
}

output "kms_gateway_stack_inputs" {
  description = "`kms_core` input of ../2-gateway."
  value       = module.kms.gateway_stack_inputs
}

output "cs_ledger_name" {
  description = "Contract Service Confidential Ledger name."
  value       = module.contract_service.ledger_name
}

output "cs_ledger_endpoint" {
  description = "Contract Service ledger endpoint (DEPA_CS_URL)."
  value       = module.contract_service.ledger_endpoint
}

output "cs_ledger_tls_certificate_pem" {
  description = "Contract Service ledger TLS certificate (pin in DEPA_TRUST_BUNDLE / CLI)."
  value       = module.contract_service.ledger_tls_certificate_pem
}

output "cs_key_vault_name" {
  description = "Contract Service Key Vault name."
  value       = module.contract_service.key_vault_name
}

output "cs_member_certificate_name" {
  description = "Contract Service ledger member certificate name in Key Vault."
  value       = module.contract_service.member_certificate_name
}

output "cs_gateway_stack_inputs" {
  description = "`cs_core` input of ../2-gateway (null unless cs_enable_gateway)."
  value       = module.contract_service.gateway_stack_inputs
}
