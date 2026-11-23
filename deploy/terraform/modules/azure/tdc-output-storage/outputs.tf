output "storage_account_name" {
  description = "Name of the output storage account."
  value       = module.storage.storage_account_name
}

output "storage_account_id" {
  description = "Resource ID of the output storage account."
  value       = module.storage.storage_account_id
}

output "audience" {
  description = "Token audience for output tokens; must be in the KMS `allowed_audiences_global` (spec §4 set_oidc_settings)."
  value       = module.storage.audience
}

output "broker_url" {
  description = "Federation broker base URL (broker mode), or null in entra mode."
  value       = module.storage.broker_url
}

output "output_location" {
  description = "Contract `output.location` object (spec §8.1)."
  value = {
    connector = "azure-blob"
    uri       = module.storage.grants[local.subject].uri
    federation = {
      azure = module.storage.grants[local.subject].federation_azure
    }
  }
}
