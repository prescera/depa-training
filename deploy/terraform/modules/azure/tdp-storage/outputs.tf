output "storage_account_name" {
  description = "Name of the storage account holding the encrypted datasets."
  value       = module.storage.storage_account_name
}

output "storage_account_id" {
  description = "Resource ID of the storage account."
  value       = module.storage.storage_account_id
}

output "audience" {
  description = "Token audience CCRs request from the KMS; put it in each dataset's Release Policy `allowed_audiences` (spec §7.1). Descriptor federation.azure.audience in broker mode."
  value       = module.storage.audience
}

output "broker_url" {
  description = "Federation broker base URL (descriptor federation.azure.broker_url), or null in entra mode."
  value       = module.storage.broker_url
}

output "datasets" {
  description = "Per dataset_id: the `location` and `federation` fragments of the CS Dataset Descriptor (spec §7.2), plus the container name."
  value = {
    for id, c in local.containers : id => {
      location = {
        connector = "azure-blob"
        uri       = module.storage.grants["dataset:${id}"].uri
        region    = var.location
      }
      federation = {
        azure = module.storage.grants["dataset:${id}"].federation_azure
      }
      container = c
    }
  }
}
