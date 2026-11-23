output "dns_records" {
  description = "Public DNS records to create (hostname -> gateway IP)."
  value       = concat([module.kms_gateway.dns_record], module.contract_service_gateway[*].dns_record)
}

output "kms_oidc_issuer_url" {
  description = "KMS OIDC issuer URL: set_oidc_settings.issuer_url and the issuer TDPs trust in their storage federation."
  value       = module.kms_gateway.oidc_issuer_url
}

output "kms_oidc_discovery_url" {
  description = "KMS OIDC discovery document URL."
  value       = module.kms_gateway.oidc_discovery_url
}

output "kms_gateway_public_ip_id" {
  description = "KMS gateway public IP resource ID."
  value       = module.kms_gateway.gateway_public_ip_id
}

output "ledger_private_endpoint_ids" {
  description = "Private endpoint connections to approve on each ledger PLS."
  value = {
    kms              = module.kms_gateway.ledger_private_endpoint_ids
    contract_service = one(module.contract_service_gateway[*].ledger_private_endpoint_ids)
  }
}

output "renewal_workflow_settings" {
  description = "GitHub environment variables for the certificate renewal workflow(s)."
  value = {
    kms              = module.kms_gateway.renewal_workflow_settings
    contract_service = one(module.contract_service_gateway[*].renewal_workflow_settings)
  }
}
