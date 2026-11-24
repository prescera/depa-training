output "datasets" {
  description = "Per dataset: `location` and `federation` fragments for the CS Dataset Descriptor (spec §7.2)."
  value       = module.tdp_storage.datasets
}

output "audience" {
  description = "Add to each dataset's KMS Release Policy `allowed_audiences` (spec §7.1)."
  value       = module.tdp_storage.audience
}

output "broker_url" {
  description = "Federation broker base URL (broker mode)."
  value       = module.tdp_storage.broker_url
}

output "storage_account_name" {
  description = "Upload target account for the encrypted dataset objects."
  value       = module.tdp_storage.storage_account_name
}

output "broker_deployment" {
  description = "Broker function app to deploy the package to: deploy/scripts/deploy-brokers.sh --resource-group <resource_group_name> --name <function_app_name>."
  value       = module.tdp_storage.broker_deployment
}
